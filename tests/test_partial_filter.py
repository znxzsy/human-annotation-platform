from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from annotation_platform.store import Store


def source_row(event_id, ordinal):
    return (
        event_id, ordinal, "test", f"page-{ordinal}", f"request-{ordinal}", 0,
        "[1,2,3,4,5]", "/test.svg", "[]", "ok", "[]", f"sha-{ordinal}", "[]",
    )


class PartialFilterTest(unittest.TestCase):
    def test_only_returns_groups_with_one_to_four_real_verdicts(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            store = Store(work / "review.sqlite3", work / "audit.jsonl")
            with store.connect() as con:
                con.executemany(
                    """INSERT INTO source_events(
                        event_id,source_ordinal,source_shard,page_id,request_id,
                        duplicate_request_id,slot_indices_json,image_ref,model_raw_content,
                        parse_status,parsed_slots_json,source_sha256,source_titles_json
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    [source_row(f"event-{i}", i) for i in range(1, 6)],
                )
                con.executemany(
                    """INSERT INTO review_groups(
                        event_id,status,claimed_by,lease_until,version,updated_at
                    ) VALUES(?,?,?,?,0,'2026-08-26T00:00:00+00:00')""",
                    [
                        ("event-1", "unreviewed", None, None),
                        ("event-2", "unreviewed", None, None),
                        ("event-3", "in_progress", "当前人员", "2099-01-01T00:00:00+00:00"),
                        ("event-4", "submitted", None, None),
                        ("event-5", "in_progress", "其他人员", "2099-01-01T00:00:00+00:00"),
                    ],
                )
                rows = [("event-1", 1, None, "旧版占位")]
                rows += [("event-2", slot, "correct", "甲") for slot in range(1, 2)]
                rows += [("event-3", slot, "correct", "乙") for slot in range(1, 5)]
                rows += [("event-4", slot, "correct", "丙") for slot in range(1, 6)]
                rows += [("event-5", slot, "correct", "丁") for slot in range(1, 3)]
                con.executemany(
                    """INSERT INTO slot_reviews(
                        event_id,slot,verdict,updated_by,updated_at
                    ) VALUES(?,?,?,?,'2026-08-26T00:00:00+00:00')""",
                    rows,
                )

            self.assertEqual(store.navigate(0, 1, "partial", 1, 5, "", "当前人员")["source_ordinal"], 2)
            self.assertEqual(store.navigate(2, 1, "partial", 1, 5, "", "当前人员")["source_ordinal"], 3)
            self.assertIsNone(store.navigate(3, 1, "partial", 1, 5, "", "当前人员"))


if __name__ == "__main__":
    unittest.main()
