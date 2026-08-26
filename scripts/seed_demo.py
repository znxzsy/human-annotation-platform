#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import html
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from annotation_platform.store import Store, now  # noqa: E402


PROBLEMS = [
    ("38+27=65", "38+27=65"),
    ("72-19=53", "72-19=53"),
    ("6\\times8=48", "6×8=48"),
    ("56\\div7=8", "56÷7=8"),
    ("45+18=63", "45+18=63"),
]


def worksheet_svg(group_number: int) -> str:
    cards = []
    for index, (_, visible) in enumerate(PROBLEMS, 1):
        y = 115 + (index - 1) * 190
        answer = visible if not (group_number + index) % 4 else visible.replace("=", "=")
        cards.append(
            f'<rect x="70" y="{y}" width="760" height="150" rx="20" fill="#fffdfa" stroke="#d9d1c3"/>'
            f'<text x="100" y="{y + 42}" font-family="system-ui" font-size="21" font-weight="700" fill="#315d88">SLOT {index}</text>'
            f'<rect x="260" y="{y + 25}" width="470" height="95" rx="13" fill="#f5f1e9" stroke="#d9d1c3" stroke-dasharray="8 6"/>'
            f'<text x="300" y="{y + 88}" font-family="serif" font-size="42" fill="#292b27">{html.escape(answer)}</text>'
        )
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="900" height="1120" viewBox="0 0 900 1120">
<rect width="900" height="1120" fill="#e9e5dc"/>
<text x="70" y="62" font-family="system-ui" font-size="28" font-weight="800" fill="#20231f">五题练习 · 演示组 {group_number:02}</text>
<text x="70" y="92" font-family="system-ui" font-size="16" fill="#74776f">全部内容均为程序生成，不含真实学生数据</text>
{''.join(cards)}
</svg>'''


def review_rows(group_number: int):
    if group_number in (4, 10, 11, 12):
        return []
    count = {5: 2, 6: 4}.get(group_number, 5)
    rows = []
    for slot in range(1, count + 1):
        verdict = "correct"
        revised = None
        reason = None
        note = None
        if group_number in (2, 8) and slot in (2, 5):
            verdict = "wrong"
            revised = PROBLEMS[slot - 1][0]
            reason = "visual_misread"
            note = "演示：模型漏看了一处笔迹"
        elif group_number in (3, 9) and slot == 4:
            verdict = "unsure"
            reason = "image_blurred"
            note = "演示：作答区域模糊"
        rows.append((slot, verdict, revised, reason, note))
    return rows


def create_demo(db_path: Path, audit_path: Path, static_root: Path) -> dict:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    demo_dir = static_root / "demo"
    demo_dir.mkdir(parents=True, exist_ok=True)
    store = Store(db_path, audit_path)
    stamp = now()
    with store.connect() as con:
        if con.execute("SELECT COUNT(*) FROM source_events").fetchone()[0]:
            return store.summary()
        for group_number in range(1, 13):
            event_id = hashlib.sha256(f"demo-group-{group_number}".encode()).hexdigest()
            image_name = f"group-{group_number:02}.svg"
            (demo_dir / image_name).write_text(worksheet_svg(group_number), encoding="utf-8")
            parsed = [{"r": expr, "h": 0} for expr, _ in PROBLEMS]
            raw = json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
            con.execute(
                """INSERT INTO source_events(
                    event_id,source_ordinal,source_shard,page_id,request_id,
                    duplicate_request_id,slot_indices_json,image_ref,model_raw_content,
                    parse_status,parsed_slots_json,source_sha256,source_titles_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_id, group_number, "synthetic-demo", f"demo-page-{group_number:02}",
                    f"demo-request-{group_number:02}", 0, "[1,2,3,4,5]",
                    f"demo/{image_name}", raw, "ok", raw,
                    hashlib.sha256(raw.encode()).hexdigest(),
                    json.dumps(["两位数加法", "两位数减法", "乘法", "除法", "两位数加法"], ensure_ascii=False),
                ),
            )
            reviews = review_rows(group_number)
            status = "submitted" if len(reviews) == 5 else "unreviewed"
            con.execute(
                """INSERT INTO review_groups(
                    event_id,status,version,submitted_by,submitted_at,updated_at
                ) VALUES(?,?,?,?,?,?)""",
                (
                    event_id, status, 1 if reviews else 0,
                    "演示标注员甲" if status == "submitted" else None,
                    stamp if status == "submitted" else None, stamp,
                ),
            )
            for slot, verdict, revised, reason, note in reviews:
                actor = ("演示标注员甲", "演示标注员乙", "演示标注员丙")[group_number % 3]
                con.execute(
                    """INSERT INTO slot_reviews(
                        event_id,slot,verdict,revised_r,revised_h,reason_code,note,updated_by,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (event_id, slot, verdict, revised, 0 if revised else None, reason, note, actor, stamp),
                )
            if group_number in (1, 2, 3):
                pool = {1: "goodcase", 2: "badcase", 3: "unknown"}[group_number]
                recheck_slot = {1: 1, 2: 2, 3: 4}[group_number]
                con.execute(
                    """INSERT INTO slot_rechecks(
                        event_id,slot,verdict,pool,note,reviewed_by,reviewed_at,original_by
                    ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        event_id, recheck_slot, "accurate" if group_number != 2 else "inaccurate", pool,
                        "演示二次复核记录", "演示复核员", stamp,
                        ("演示标注员甲", "演示标注员乙", "演示标注员丙")[group_number % 3],
                    ),
                )
    return store.summary()


def main():
    parser = argparse.ArgumentParser(description="Create a synthetic local demo database.")
    parser.add_argument("--db", type=Path, default=ROOT / "runtime/demo.sqlite3")
    parser.add_argument("--audit", type=Path, default=ROOT / "runtime/demo-audit.jsonl")
    parser.add_argument("--static", type=Path, default=ROOT / "annotation_platform/static")
    args = parser.parse_args()
    result = create_demo(args.db, args.audit, args.static)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
