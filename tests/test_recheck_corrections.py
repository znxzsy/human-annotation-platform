from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from annotation_platform.exporter import export_snapshot
from annotation_platform.store import Store, now


class RecheckCorrectionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / "review.sqlite3", self.root / "audit.jsonl")

    def tearDown(self):
        self.tmp.cleanup()

    def add_group(self, name: str, first_verdict: str, revised_r=None, reason_code=None):
        event_id = hashlib.sha256(name.encode()).hexdigest()
        parsed = [{"r": f"{index}+1={index + 1}", "h": 0} for index in range(1, 6)]
        stamp = now()
        with self.store.connect() as con:
            con.execute(
                """INSERT INTO source_events(
                    event_id,source_ordinal,source_shard,page_id,request_id,
                    duplicate_request_id,slot_indices_json,image_ref,model_raw_content,
                    parse_status,parsed_slots_json,source_sha256,source_titles_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    event_id,
                    con.execute("SELECT COUNT(*)+1 FROM source_events").fetchone()[0],
                    "test",
                    f"page-{name}",
                    f"request-{name}",
                    0,
                    "[1,2,3,4,5]",
                    f"images/{name}.png",
                    json.dumps(parsed, ensure_ascii=False),
                    "ok",
                    json.dumps(parsed, ensure_ascii=False),
                    hashlib.sha256(name.encode()).hexdigest(),
                    "[]",
                ),
            )
            con.execute(
                """INSERT INTO review_groups(
                    event_id,status,version,submitted_by,submitted_at,updated_at
                ) VALUES(?,?,?,?,?,?)""",
                (event_id, "submitted", 1, "初标员", stamp, stamp),
            )
            for slot in range(1, 6):
                verdict = first_verdict if slot == 1 else "correct"
                con.execute(
                    """INSERT INTO slot_reviews(
                        event_id,slot,verdict,revised_r,revised_h,reason_code,note,
                        updated_by,updated_at
                    ) VALUES(?,?,?,?,?,?,?,?,?)""",
                    (
                        event_id,
                        slot,
                        verdict,
                        revised_r if slot == 1 else None,
                        0 if slot == 1 and revised_r else None,
                        reason_code if slot == 1 else None,
                        None,
                        "初标员",
                        stamp,
                    ),
                )
        return event_id, parsed

    @staticmethod
    def read_jsonl(path: Path):
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]

    def test_inaccurate_recheck_can_store_a_final_label_without_overwriting_first_pass(self):
        event_id, _ = self.add_group("unknown-corrected", "unsure", reason_code="image_blurred")

        item = self.store.save_recheck(
            event_id,
            1,
            "复核员",
            "recheck-final-correct",
            "inaccurate",
            "图像实际清晰",
            "unknown",
            final_verdict="correct",
        )

        recheck = item["rechecks"][0]
        self.assertEqual(recheck["verdict"], "inaccurate")
        self.assertEqual(recheck["final_verdict"], "correct")
        self.assertIsNone(recheck["final_r"])
        self.assertEqual(item["slots"][0]["verdict"], "unsure")

        with self.assertRaisesRegex(ValueError, "修正结果"):
            self.store.save_recheck(
                event_id,
                1,
                "复核员",
                "recheck-final-wrong-invalid",
                "inaccurate",
                "",
                "unknown",
                final_verdict="wrong",
            )
        with self.assertRaisesRegex(ValueError, "Unknown 原因"):
            self.store.save_recheck(
                event_id,
                1,
                "复核员",
                "recheck-final-unsure-invalid",
                "inaccurate",
                "",
                "unknown",
                final_verdict="unsure",
            )

    def test_export_uses_resolved_rechecks_and_quarantines_unresolved_inaccuracies(self):
        recovered_id, recovered_parsed = self.add_group(
            "unknown-recovered", "unsure", reason_code="image_blurred"
        )
        quarantined_id, _ = self.add_group("good-quarantined", "correct")
        corrected_id, _ = self.add_group("wrong-corrected", "wrong", revised_r="旧修正")

        self.store.save_recheck(
            recovered_id,
            1,
            "复核员",
            "recover-unknown",
            "inaccurate",
            "实际可辨认",
            "unknown",
            final_verdict="correct",
        )
        self.store.save_recheck(
            quarantined_id,
            1,
            "复核员",
            "quarantine-good",
            "inaccurate",
            "等待修正",
            "goodcase",
        )
        self.store.save_recheck(
            corrected_id,
            1,
            "复核员",
            "correct-wrong",
            "inaccurate",
            "重新转写",
            "badcase",
            final_verdict="wrong",
            final_r="一加一等于二",
            final_h=0,
        )

        snapshot = export_snapshot(self.store.db_path, self.root / "exports")
        sft = self.read_jsonl(snapshot / "sft_complete_groups.jsonl")
        dpo = self.read_jsonl(snapshot / "dpo_candidates.jsonl")
        kto = self.read_jsonl(snapshot / "kto_candidates.jsonl")

        outputs = {row["event_id"]: row["output"] for row in sft}
        self.assertEqual(outputs[recovered_id][0], recovered_parsed[0])
        self.assertNotIn(quarantined_id, outputs)
        self.assertEqual(outputs[corrected_id][0], {"r": "一加一等于二", "h": 0})
        self.assertIn(
            "一加一等于二",
            [row["chosen"]["r"] for row in dpo if row["event_id"] == corrected_id],
        )
        self.assertIn(
            "wrong",
            [row["label"] for row in kto if row["event_id"] == corrected_id],
        )

    def test_pending_picker_keeps_unresolved_inaccuracies_in_the_work_queue(self):
        event_id, _ = self.add_group("pending-correction", "correct")
        with self.store.connect() as con:
            con.execute(
                """UPDATE slot_reviews SET verdict='unsure',reason_code='image_blurred'
                   WHERE event_id=? AND slot>1""",
                (event_id,),
            )
        self.store.save_recheck(
            event_id,
            1,
            "复核员",
            "pending-inaccurate",
            "inaccurate",
            "等待最终修正",
            "goodcase",
        )

        pending = self.store.recheck_pick(
            "goodcase", random_pick=True, pending_only=True
        )
        self.assertEqual(pending["event_id"], event_id)

        self.store.save_recheck(
            event_id,
            1,
            "复核员",
            "pending-resolved",
            "inaccurate",
            "已经修正",
            "goodcase",
            final_verdict="correct",
        )
        self.assertIsNone(
            self.store.recheck_pick("goodcase", random_pick=True, pending_only=True)
        )


if __name__ == "__main__":
    unittest.main()
