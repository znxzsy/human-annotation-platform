from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""): h.update(block)
    return h.hexdigest()


def export_snapshot(db_path: Path, output_root: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    part, final = output_root / (stamp + ".part"), output_root / stamp
    part.mkdir(parents=True)
    files = {name: (part / name).open("w", encoding="utf-8") for name in (
        "review_groups.jsonl", "slot_reviews.jsonl", "group_rechecks.jsonl", "slot_rechecks.jsonl",
        "kto_candidates.jsonl", "dpo_candidates.jsonl", "sft_complete_groups.jsonl")}
    counts = Counter()
    try:
        con = sqlite3.connect(str(db_path)); con.row_factory = sqlite3.Row
        groups = con.execute("""SELECT s.*,g.* FROM source_events s JOIN review_groups g USING(event_id)
                              WHERE g.status='submitted' ORDER BY s.source_ordinal""").fetchall()
        for group in groups:
            g = dict(group); parsed = json.loads(g["parsed_slots_json"])
            slots = [dict(x) for x in con.execute("SELECT * FROM slot_reviews WHERE event_id=? ORDER BY slot", (g["event_id"],))]
            recheck = con.execute("SELECT * FROM group_rechecks WHERE event_id=?", (g["event_id"],)).fetchone()
            files["review_groups.jsonl"].write(json.dumps(g, ensure_ascii=False) + "\n")
            if recheck:
                files["group_rechecks.jsonl"].write(json.dumps(dict(recheck), ensure_ascii=False) + "\n")
                counts["recheck_" + recheck["verdict"]] += 1
            for slot_recheck in con.execute(
                "SELECT * FROM slot_rechecks WHERE event_id=? ORDER BY slot", (g["event_id"],)
            ):
                files["slot_rechecks.jsonl"].write(json.dumps(dict(slot_recheck), ensure_ascii=False) + "\n")
                counts["slot_recheck_" + slot_recheck["verdict"]] += 1
            final_items, complete = [], len(slots) == 5 and len(parsed) == 5
            for pos, slot in enumerate(slots):
                files["slot_reviews.jsonl"].write(json.dumps(slot, ensure_ascii=False) + "\n")
                counts[slot["verdict"]] += 1
                if slot["verdict"] in ("correct", "wrong"):
                    kto = {"event_id": g["event_id"], "page_id": g["page_id"], "request_id": g["request_id"], "slot": slot["slot"], "image": g["image_ref"], "label": slot["verdict"], "model_item": parsed[pos]}
                    files["kto_candidates.jsonl"].write(json.dumps(kto, ensure_ascii=False) + "\n")
                if slot["verdict"] == "wrong" and slot["revised_r"] is not None:
                    dpo = {"event_id": g["event_id"], "slot": slot["slot"], "image": g["image_ref"], "rejected": parsed[pos], "chosen": {"r": slot["revised_r"], "h": slot["revised_h"]}}
                    files["dpo_candidates.jsonl"].write(json.dumps(dpo, ensure_ascii=False) + "\n")
                if slot["verdict"] == "correct": final_items.append(parsed[pos])
                elif slot["verdict"] == "wrong" and slot["revised_r"] is not None: final_items.append({"r": slot["revised_r"], "h": slot["revised_h"]})
                else: complete = False
            if complete and len(final_items) == 5:
                files["sft_complete_groups.jsonl"].write(json.dumps({"event_id": g["event_id"], "image": g["image_ref"], "output": final_items}, ensure_ascii=False) + "\n")
        con.close()
    finally:
        for handle in files.values(): handle.close()
    manifest = {"version": 1, "generated_at": datetime.now(timezone.utc).isoformat(), "submitted_groups": len(groups), "verdicts": dict(counts), "files": {p.name: {"bytes": p.stat().st_size, "sha256": sha256(p)} for p in sorted(part.glob("*.jsonl"))}}
    (part / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (part / "FROZEN_OK").write_text("validated=true\n", encoding="utf-8")
    os.replace(part, final)
    return final
