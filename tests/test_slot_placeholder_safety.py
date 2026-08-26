from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from annotation_platform.store import Store


def slot(verdict=None):
    return {
        "verdict": verdict,
        "revised_r": "",
        "revised_h": None,
        "reason_code": "",
        "note": "",
    }


class SlotPlaceholderSafetyTest(unittest.TestCase):
    def test_empty_placeholders_do_not_clear_or_steal_real_annotations(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            store = Store(work / "review.sqlite3", work / "audit.jsonl")
            with store.connect() as con:
                con.execute(
                    """INSERT INTO source_events(
                        event_id,source_ordinal,source_shard,page_id,request_id,
                        duplicate_request_id,slot_indices_json,image_ref,model_raw_content,
                        parse_status,parsed_slots_json,source_sha256,source_titles_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    ("event-1", 1, "test", "page-1", "request-1", 0, "[1,2,3,4,5]",
                     "/test.svg", "[]", "ok", "[]", "sha-1", "[]"),
                )
                con.execute(
                    """INSERT INTO review_groups(
                        event_id,status,claimed_by,lease_until,version,updated_at
                    ) VALUES(?,?,?,?,?,?)""",
                    ("event-1", "in_progress", "查看人员", "2099-01-01T00:00:00+00:00", 0,
                     "2026-08-26T00:00:00+00:00"),
                )
                con.executemany(
                    """INSERT INTO slot_reviews(
                        event_id,slot,verdict,updated_by,updated_at
                    ) VALUES(?,?,?,?,?)""",
                    [
                        ("event-1", 1, "correct", "原标注员", "2026-08-26T01:00:00+00:00"),
                        ("event-1", 2, None, "旧版占位", "2026-08-26T01:00:00+00:00"),
                    ],
                )

            result = store.save(
                "event-1", "查看人员", "save-1", 0,
                [slot("correct"), slot("correct"), slot(), slot(), slot()], submit=False,
            )
            self.assertEqual(result["version"], 1)
            with store.connect() as con:
                rows = con.execute(
                    "SELECT slot,verdict,updated_by FROM slot_reviews WHERE event_id='event-1' ORDER BY slot"
                ).fetchall()
            self.assertEqual([dict(row) for row in rows], [
                {"slot": 1, "verdict": "correct", "updated_by": "原标注员"},
                {"slot": 2, "verdict": "correct", "updated_by": "查看人员"},
            ])

            with self.assertRaisesRegex(ValueError, "cannot clear existing verdict"):
                store.save(
                    "event-1", "查看人员", "save-2", 1,
                    [slot(), slot("correct"), slot(), slot(), slot()], submit=False,
                )


if __name__ == "__main__":
    unittest.main()
