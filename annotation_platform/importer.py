from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

from .contracts import parse_model_slots, safe_image_ref


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def event_id(page_id: str, request_id: str, ordinal: int, slot_indices: list[int]) -> str:
    source = "\n".join((page_id, request_id, str(ordinal), json.dumps(slot_indices, separators=(",", ":"))))
    return hashlib.sha256(source.encode()).hexdigest()


def iter_candidates(paths: list[Path]):
    source_ordinal = 0
    for path in paths:
        pages = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(pages, list):
            raise ValueError(f"not a page list: {path}")
        shard_hash = sha256(path)
        for page_ordinal, page in enumerate(pages):
            page_id = str(page.get("page_id") or "")
            slot_titles = {
                int(item.get("slot_index")): str(item.get("title") or "")
                for item in page.get("slot_items") or []
                if isinstance(item, dict) and str(item.get("slot_index") or "").isdigit()
            }
            for request_ordinal, request in enumerate(page.get("requests") or []):
                indices = request.get("slot_indices") or []
                if len(indices) != 5:
                    continue
                source_ordinal += 1
                yield {
                    "source_ordinal": source_ordinal,
                    "source_shard": path.name,
                    "source_page_ordinal": page_ordinal,
                    "request_ordinal": request_ordinal,
                    "page_id": page_id,
                    "request_id": str(request.get("id") or ""),
                    "slot_indices": list(indices),
                    "source_titles": [slot_titles.get(int(index), "") for index in indices],
                    "image_ref_raw": str(request.get("image_url") or ""),
                    "model_raw_content": str(request.get("model_raw_content") or ""),
                    "source_sha256": shard_hash,
                }


def import_shards(details_dir: Path, output_dir: Path) -> dict:
    paths = sorted(details_dir.glob("details_*.json"))
    if not paths:
        raise FileNotFoundError(f"no detail shards in {details_dir}")
    candidates = list(iter_candidates(paths))
    request_counts = Counter(row["request_id"] for row in candidates)
    raw_nonempty = sum(bool(row["model_raw_content"].strip()) for row in candidates)
    image_ref_nonempty = sum(bool(row["image_ref_raw"].strip()) for row in candidates)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "source_groups.jsonl"
    part = target.with_suffix(target.suffix + ".part")
    parse_counts, event_ids, rejected = Counter(), set(), 0
    written = 0
    with part.open("w", encoding="utf-8") as handle:
        for row in candidates:
            try:
                image_ref = safe_image_ref(row.pop("image_ref_raw"))
            except ValueError:
                rejected += 1
                continue
            parsed = parse_model_slots(row["model_raw_content"])
            eid = event_id(row["page_id"], row["request_id"], row["request_ordinal"], row["slot_indices"])
            if eid in event_ids:
                raise ValueError(f"duplicate event id: {eid}")
            event_ids.add(eid)
            parse_counts[parsed.status] += 1
            normalized = {
                "event_id": eid,
                **row,
                "duplicate_request_id": request_counts[row["request_id"]] > 1,
                "image_ref": image_ref,
                "image_kind": "stable_url" if image_ref.startswith(("http://", "https://")) else "local",
                "parse_status": parsed.status,
                "parse_error": parsed.error,
                "parsed_slots": parsed.json_items(),
            }
            handle.write(json.dumps(normalized, ensure_ascii=False, separators=(",", ":")) + "\n")
            written += 1
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(part, target)
    manifest = {
        "version": 1,
        "detail_shards": len(paths),
        "five_slot_events": written,
        "unique_event_ids": len(event_ids),
        "unique_request_ids": len(request_counts),
        "duplicate_request_ids": sum(count > 1 for count in request_counts.values()),
        "repeated_request_id_events": sum(count for count in request_counts.values() if count > 1),
        "extra_request_id_occurrences": sum(count - 1 for count in request_counts.values() if count > 1),
        "raw_nonempty": raw_nonempty,
        "image_ref_nonempty": image_ref_nonempty,
        "rejected_unsafe_image_refs": rejected,
        "parse_status": dict(sorted(parse_counts.items())),
        "source_groups_sha256": sha256(target),
        "source_groups_bytes": target.stat().st_size,
    }
    manifest_part = output_dir / "source_manifest.json.part"
    manifest_part.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(manifest_part, output_dir / "source_manifest.json")
    return manifest


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--details-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(import_shards(args.details_dir, args.output_dir), ensure_ascii=False))


if __name__ == "__main__":
    main()
