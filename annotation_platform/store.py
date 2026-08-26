from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConflictError(ValueError):
    pass


class Store:
    def __init__(self, db_path: Path, audit_path: Path):
        self.db_path, self.audit_path = Path(db_path), Path(audit_path)
        self.lock = threading.Lock()
        self.progress_lock = threading.Lock()
        self._progress_cache = None
        self._progress_cache_at = 0.0
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.audit_path.parent.mkdir(parents=True, exist_ok=True)
        schema = Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        with self.connect() as con:
            con.executescript(schema)
            columns = {row[1] for row in con.execute("PRAGMA table_info(source_events)")}
            if "source_titles_json" not in columns:
                con.execute("ALTER TABLE source_events ADD COLUMN source_titles_json TEXT NOT NULL DEFAULT '[]'")
            self._migrate_slot_review_reasons(con)
            self._migrate_group_rechecks_to_slots(con)
            self._migrate_recheck_original_annotator(con)
            self._migrate_recheck_unknown_pool(con)
            self._backfill_source_slot_types(con)
            # The daily leaderboard only scans the selected Beijing day.  Keep
            # that lookup off the annotation write path and out of a full audit
            # table scan as the append-only event log grows.
            con.execute(
                "CREATE INDEX IF NOT EXISTS idx_review_events_action_created_at "
                "ON review_events(action, created_at)"
            )
            con.execute("INSERT OR REPLACE INTO metadata VALUES('schema_version','8')")

    @staticmethod
    def _classify_question_type(text):
        value = str(text or "")
        if "\\frac" in value or "分之" in value or "/" in value:
            return "fraction"
        if any(token in value for token in ("单位换算", "厘米", "千米", "毫米", "千克", "公斤")):
            return "unit_conversion"
        if "方程" in value or "x" in value or "X" in value:
            return "equation"
        if "余数" in value or "有余数" in value:
            return "remainder"
        if "竖式" in value:
            return "vertical"
        if any(token in value for token in ("脱式", "简便", "递等式")):
            return "multi_step"
        if any(token in value for token in ("<", ">", "≤", "≥", "比较大小")):
            return "comparison"
        if re.search(r"\d\.\d", value):
            return "decimal"
        if any(token in value for token in ("□", "（", "\\quad")):
            return "fill_blank"
        return "arithmetic"

    @classmethod
    def _backfill_source_slot_types(cls, con):
        con.executescript("""
            CREATE TABLE IF NOT EXISTS source_slot_types (
              event_id TEXT NOT NULL REFERENCES source_events(event_id),
              slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 5),
              question_type TEXT NOT NULL,
              PRIMARY KEY(event_id,slot)
            );
            CREATE INDEX IF NOT EXISTS idx_source_slot_types_question_type
              ON source_slot_types(question_type);
        """)
        missing = con.execute("""SELECT s.event_id,s.source_titles_json,s.parsed_slots_json
            FROM source_events s WHERE NOT EXISTS(
              SELECT 1 FROM source_slot_types t WHERE t.event_id=s.event_id)""").fetchall()
        inserts = []
        for row in missing:
            try:
                titles = json.loads(row["source_titles_json"] or "[]")
                parsed = json.loads(row["parsed_slots_json"] or "[]")
            except (TypeError, ValueError):
                titles, parsed = [], []
            for index in range(5):
                title = titles[index] if index < len(titles) else ""
                result = parsed[index].get("r", "") if index < len(parsed) and isinstance(parsed[index], dict) else ""
                inserts.append((row["event_id"], index + 1, cls._classify_question_type(f"{title} {result}")))
        if inserts:
            con.executemany("INSERT OR REPLACE INTO source_slot_types VALUES(?,?,?)", inserts)

    @staticmethod
    def _migrate_slot_review_reasons(con):
        row = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='slot_reviews'"
        ).fetchone()
        if row and "no_handwriting" in (row[0] or ""):
            return
        con.executescript("""
            ALTER TABLE slot_reviews RENAME TO slot_reviews_schema_v3;
            CREATE TABLE slot_reviews (
              event_id TEXT NOT NULL REFERENCES source_events(event_id),
              slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 5),
              verdict TEXT CHECK(verdict IN ('correct','wrong','unsure')),
              revised_r TEXT,
              revised_h INTEGER CHECK(revised_h IN (0,1)),
              reason_code TEXT CHECK(reason_code IN (
                'math_error','visual_misread','slot_alignment','format_error',
                'image_blurred','ungradable','no_handwriting','other'
              )),
              note TEXT,
              updated_by TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              PRIMARY KEY(event_id,slot)
            );
            INSERT INTO slot_reviews(
              event_id,slot,verdict,revised_r,revised_h,reason_code,note,updated_by,updated_at
            ) SELECT
              event_id,slot,verdict,revised_r,revised_h,reason_code,note,updated_by,updated_at
            FROM slot_reviews_schema_v3;
            DROP TABLE slot_reviews_schema_v3;
        """)

    @staticmethod
    def _migrate_group_rechecks_to_slots(con):
        con.execute("""INSERT OR IGNORE INTO slot_rechecks(
            event_id,slot,verdict,pool,note,reviewed_by,reviewed_at
        ) SELECT q.event_id,n.slot,q.verdict,q.pool,q.note,q.reviewed_by,q.reviewed_at
          FROM group_rechecks q
          CROSS JOIN (
            SELECT 1 AS slot UNION ALL SELECT 2 UNION ALL SELECT 3 UNION ALL SELECT 4 UNION ALL SELECT 5
          ) n""")

    @staticmethod
    def _migrate_recheck_original_annotator(con):
        columns = {row[1] for row in con.execute("PRAGMA table_info(slot_rechecks)")}
        if "original_by" not in columns:
            con.execute("ALTER TABLE slot_rechecks ADD COLUMN original_by TEXT")
        con.execute("""UPDATE slot_rechecks
            SET original_by=(SELECT r.updated_by FROM slot_reviews r
                WHERE r.event_id=slot_rechecks.event_id AND r.slot=slot_rechecks.slot)
            WHERE original_by IS NULL""")

    @staticmethod
    def _migrate_recheck_unknown_pool(con):
        row = con.execute(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='slot_rechecks'"
        ).fetchone()
        if not row or "'unknown'" not in (row[0] or ""):
            con.executescript("""
                ALTER TABLE slot_rechecks RENAME TO slot_rechecks_schema_v7;
                DROP INDEX IF EXISTS idx_slot_rechecks_pool_verdict;
                CREATE TABLE slot_rechecks (
                  event_id TEXT NOT NULL REFERENCES source_events(event_id),
                  slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 5),
                  verdict TEXT NOT NULL CHECK(verdict IN ('accurate','inaccurate')),
                  pool TEXT NOT NULL CHECK(pool IN ('goodcase','badcase','unknown')),
                  note TEXT, reviewed_by TEXT NOT NULL, reviewed_at TEXT NOT NULL,
                  original_by TEXT,
                  PRIMARY KEY(event_id,slot)
                );
                INSERT INTO slot_rechecks(
                  event_id,slot,verdict,pool,note,reviewed_by,reviewed_at,original_by
                ) SELECT event_id,slot,verdict,pool,note,reviewed_by,reviewed_at,original_by
                  FROM slot_rechecks_schema_v7;
                DROP TABLE slot_rechecks_schema_v7;
                CREATE INDEX idx_slot_rechecks_pool_verdict ON slot_rechecks(pool,verdict);
            """)
        con.execute("""UPDATE slot_rechecks SET pool=CASE
            WHEN EXISTS(SELECT 1 FROM slot_reviews r WHERE r.event_id=slot_rechecks.event_id
                AND r.slot=slot_rechecks.slot AND r.verdict='unsure') THEN 'unknown'
            WHEN EXISTS(SELECT 1 FROM slot_reviews r WHERE r.event_id=slot_rechecks.event_id
                AND r.slot=slot_rechecks.slot AND r.verdict='wrong'
                AND TRIM(COALESCE(r.revised_r,''))!='') THEN 'badcase'
            WHEN EXISTS(SELECT 1 FROM slot_reviews r WHERE r.event_id=slot_rechecks.event_id
                AND r.slot=slot_rechecks.slot AND r.verdict='correct') THEN 'goodcase'
            ELSE pool END""")

    def connect(self):
        con = sqlite3.connect(str(self.db_path), timeout=30)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA synchronous=FULL")
        con.execute("PRAGMA busy_timeout=30000")
        return con

    @staticmethod
    def normalize_invite_code(code: str) -> str:
        value = re.sub(r"\s+", "", str(code or "")).upper()
        if not re.fullmatch(r"ANO-[A-HJ-NP-Z2-9]{4}(?:-[A-HJ-NP-Z2-9]{4}){3}", value):
            raise ValueError("邀请码格式错误")
        return value

    @staticmethod
    def normalize_reviewer_name(name: str) -> str:
        value = " ".join(str(name or "").strip().split())
        if not 2 <= len(value) <= 24:
            raise ValueError("姓名需为 2 至 24 个字符")
        if any(ord(char) < 32 for char in value) or any(char in "/\\<>\"'" for char in value):
            raise ValueError("姓名包含不支持的字符")
        if value.startswith("标注员-") or value in {"dashboard", "platform-admin"}:
            raise ValueError("请填写真实姓名")
        return value

    @staticmethod
    def invite_hash(code: str) -> str:
        import hashlib

        normalized = Store.normalize_invite_code(code)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def import_invites(self, codes) -> dict:
        stamp = now()
        inserted = 0
        existing = 0
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            for raw in codes:
                code = self.normalize_invite_code(raw)
                code_hash = self.invite_hash(code)
                cursor = con.execute(
                    """INSERT OR IGNORE INTO reviewer_invites(
                           code_hash,code_tail,status,created_at
                       ) VALUES(?,?, 'unused',?)""",
                    (code_hash, code[-4:], stamp),
                )
                if cursor.rowcount:
                    inserted += 1
                else:
                    existing += 1
        return {"inserted": inserted, "existing": existing, "total": inserted + existing}

    def import_master_keys(self, entries) -> dict:
        stamp = now()
        inserted = 0
        existing = 0
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            for entry in entries:
                if not isinstance(entry, dict):
                    raise ValueError("master key entry must be an object")
                code = self.normalize_invite_code(entry.get("code", ""))
                display_name = self.normalize_reviewer_name(entry.get("display_name", ""))
                code_hash = self.invite_hash(code)
                normal = con.execute(
                    "SELECT 1 FROM reviewer_invites WHERE code_hash=?", (code_hash,)
                ).fetchone()
                if normal:
                    raise ConflictError("金手指不能与普通邀请码重复")
                normal_name = con.execute(
                    "SELECT 1 FROM reviewer_invites WHERE display_name=? COLLATE NOCASE",
                    (display_name,),
                ).fetchone()
                if normal_name:
                    raise ConflictError("管理员姓名已被普通邀请码占用")
                cursor = con.execute(
                    """INSERT OR IGNORE INTO reviewer_master_keys(
                           code_hash,code_tail,display_name,status,created_at
                       ) VALUES(?,?,?,'active',?)""",
                    (code_hash, code[-4:], display_name, stamp),
                )
                if cursor.rowcount:
                    inserted += 1
                else:
                    existing += 1
        return {"inserted": inserted, "existing": existing, "total": inserted + existing}

    def bind_invite(self, code: str, display_name: str, client_ip="", user_agent="") -> dict:
        normalized_code = self.normalize_invite_code(code)
        normalized_name = self.normalize_reviewer_name(display_name)
        code_hash = self.invite_hash(normalized_code)
        stamp = now()
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            invite = con.execute(
                "SELECT * FROM reviewer_invites WHERE code_hash=?", (code_hash,)
            ).fetchone()
            if not invite:
                master = con.execute(
                    "SELECT * FROM reviewer_master_keys WHERE code_hash=? AND status='active'",
                    (code_hash,),
                ).fetchone()
                if not master:
                    raise PermissionError("邀请码无效或已停用")
                if master["display_name"].casefold() != normalized_name.casefold():
                    raise PermissionError("金手指需填写固定姓名：{}".format(master["display_name"]))
                regular_owner = con.execute(
                    "SELECT 1 FROM reviewer_invites WHERE display_name=? COLLATE NOCASE",
                    (master["display_name"],),
                ).fetchone()
                if regular_owner:
                    raise ConflictError("管理员姓名已被普通邀请码占用")
                con.execute(
                    """UPDATE reviewer_master_keys SET last_login_at=?,use_count=use_count+1
                       WHERE code_hash=?""",
                    (stamp, code_hash),
                )
                con.execute(
                    """INSERT INTO auth_events(
                       action,display_name,code_tail,client_ip,user_agent,created_at
                       ) VALUES(?,?,?,?,?,?)""",
                    ("master_login", master["display_name"], normalized_code[-4:], client_ip[:128], user_agent[:256], stamp),
                )
                return {
                    "display_name": master["display_name"],
                    "code_hash": code_hash,
                    "action": "master_login",
                }
            if invite["status"] == "disabled":
                raise PermissionError("邀请码无效或已停用")
            name_owner = con.execute(
                "SELECT code_hash FROM reviewer_invites WHERE display_name=? COLLATE NOCASE",
                (normalized_name,),
            ).fetchone()
            if name_owner and name_owner["code_hash"] != code_hash:
                raise ConflictError("该姓名已绑定其他邀请码")
            master_name_owner = con.execute(
                "SELECT 1 FROM reviewer_master_keys WHERE display_name=? COLLATE NOCASE AND status='active'",
                (normalized_name,),
            ).fetchone()
            if master_name_owner:
                raise ConflictError("该姓名为管理员专用")
            if invite["status"] == "bound" and invite["display_name"] != normalized_name:
                raise ConflictError("该邀请码已绑定其他姓名")
            action = "bind" if invite["status"] == "unused" else "login"
            con.execute(
                """UPDATE reviewer_invites SET display_name=?,status='bound',
                   bound_at=COALESCE(bound_at,?),last_login_at=? WHERE code_hash=?""",
                (normalized_name, stamp, stamp, code_hash),
            )
            con.execute(
                """INSERT INTO auth_events(
                   action,display_name,code_tail,client_ip,user_agent,created_at
                   ) VALUES(?,?,?,?,?,?)""",
                (action, normalized_name, normalized_code[-4:], client_ip[:128], user_agent[:256], stamp),
            )
        return {"display_name": normalized_name, "code_hash": code_hash, "action": action}

    def bound_invite_is_active(self, code_hash: str, display_name: str) -> bool:
        with self.connect() as con:
            row = con.execute(
                """SELECT 1 FROM reviewer_invites
                   WHERE code_hash=? AND display_name=? COLLATE NOCASE AND status='bound'""",
                (code_hash, display_name),
            ).fetchone()
            if not row:
                row = con.execute(
                    """SELECT 1 FROM reviewer_master_keys
                       WHERE code_hash=? AND display_name=? COLLATE NOCASE AND status='active'""",
                    (code_hash, display_name),
                ).fetchone()
        return bool(row)

    def record_auth_event(self, action: str, display_name="", code_tail="", client_ip="", user_agent=""):
        with self.connect() as con:
            con.execute(
                """INSERT INTO auth_events(
                   action,display_name,code_tail,client_ip,user_agent,created_at
                   ) VALUES(?,?,?,?,?,?)""",
                (action, display_name or None, code_tail or None, client_ip[:128], user_agent[:256], now()),
            )

    def invite_summary(self) -> dict:
        with self.connect() as con:
            counts = dict(con.execute(
                "SELECT status,COUNT(*) FROM reviewer_invites GROUP BY status"
            ).fetchall())
            master_counts = dict(con.execute(
                "SELECT status,COUNT(*) FROM reviewer_master_keys GROUP BY status"
            ).fetchall())
        return {
            "total": sum(counts.values()),
            "statuses": counts,
            "master_keys": {"total": sum(master_counts.values()), "statuses": master_counts},
        }

    def import_registry(self, path: Path) -> int:
        inserted = 0
        stamp = now()
        with self.connect() as con, Path(path).open(encoding="utf-8") as handle:
            for line in handle:
                row = json.loads(line)
                cur = con.execute("""INSERT OR IGNORE INTO source_events(
                    event_id,source_ordinal,source_shard,page_id,request_id,duplicate_request_id,
                    slot_indices_json,image_ref,model_raw_content,parse_status,parsed_slots_json,
                    source_sha256,source_titles_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", (
                    row["event_id"], row["source_ordinal"], row["source_shard"], row["page_id"], row["request_id"],
                    int(row["duplicate_request_id"]), json.dumps(row["slot_indices"]), row["image_ref"],
                    row["model_raw_content"], row["parse_status"], json.dumps(row["parsed_slots"], ensure_ascii=False),
                    row["source_sha256"], json.dumps(row.get("source_titles") or [], ensure_ascii=False),
                ))
                if cur.rowcount:
                    inserted += 1
                    con.execute("INSERT INTO review_groups(event_id,updated_at) VALUES(?,?)", (row["event_id"], stamp))
                elif row.get("source_titles"):
                    con.execute(
                        "UPDATE source_events SET source_titles_json=? WHERE event_id=? AND source_titles_json='[]'",
                        (json.dumps(row["source_titles"], ensure_ascii=False), row["event_id"]),
                    )
        return inserted

    def summary(self) -> dict:
        with self.connect() as con:
            total = con.execute("SELECT COUNT(*) FROM source_events").fetchone()[0]
            statuses = dict(con.execute("SELECT status,COUNT(*) FROM review_groups GROUP BY status").fetchall())
            verdicts = dict(con.execute("SELECT verdict,COUNT(*) FROM slot_reviews WHERE verdict IS NOT NULL GROUP BY verdict").fetchall())
            reviewed_slots = sum(verdicts.values())
            completed_groups = con.execute("""SELECT COUNT(*) FROM (
                SELECT event_id FROM slot_reviews WHERE verdict IS NOT NULL
                GROUP BY event_id HAVING COUNT(DISTINCT slot)=5)""").fetchone()[0]
            partial_groups = con.execute("""SELECT COUNT(*) FROM (
                SELECT event_id FROM slot_reviews WHERE verdict IS NOT NULL
                GROUP BY event_id HAVING COUNT(DISTINCT slot) BETWEEN 1 AND 4)""").fetchone()[0]
            parses = dict(con.execute("SELECT parse_status,COUNT(*) FROM source_events GROUP BY parse_status").fetchall())
            goodcase = con.execute("""SELECT COUNT(*) FROM review_groups g WHERE g.status='submitted'
                AND NOT EXISTS(SELECT 1 FROM slot_reviews r WHERE r.event_id=g.event_id AND r.verdict IN ('wrong','unsure'))""").fetchone()[0]
            badcase = con.execute("""SELECT COUNT(*) FROM review_groups g WHERE g.status='submitted'
                AND EXISTS(SELECT 1 FROM slot_reviews r WHERE r.event_id=g.event_id
                    AND r.verdict='wrong' AND TRIM(COALESCE(r.revised_r,''))!='')""").fetchone()[0]
            unknowncase = con.execute("""SELECT COUNT(*) FROM review_groups g WHERE g.status='submitted'
                AND EXISTS(SELECT 1 FROM slot_reviews r WHERE r.event_id=g.event_id
                    AND r.verdict='unsure')""").fetchone()[0]
            rechecks = dict(con.execute("SELECT verdict,COUNT(*) FROM slot_rechecks GROUP BY verdict").fetchall())
            recheck_pools = {
                row["pool"]: {"reviewed": row["reviewed"], "inaccurate": row["inaccurate"]}
                for row in con.execute("""SELECT pool,COUNT(*) AS reviewed,
                    SUM(verdict='inaccurate') AS inaccurate FROM slot_rechecks GROUP BY pool""")
            }
        return {"total": total, "statuses": statuses, "verdicts": verdicts, "parse_statuses": parses,
                "human_reviewed_slots": reviewed_slots,
                "human_completed_groups": completed_groups,
                "human_partial_groups": partial_groups,
                "human_remaining_groups": total - completed_groups,
                "human_undecided_slots": total * 5 - reviewed_slots,
                "goodcase": goodcase, "badcase": badcase,
                "unknowncase": unknowncase,
                "recheck_reviewed": sum(rechecks.values()),
                "recheck_accurate": rechecks.get("accurate", 0),
                "recheck_inaccurate": rechecks.get("inaccurate", 0),
                "recheck_pools": recheck_pools}

    def dashboard(self, batch_size: int = 1000) -> dict:
        batch_size = min(max(int(batch_size), 100), 5000)
        sql = """WITH complete AS (
                   SELECT event_id FROM slot_reviews WHERE verdict IS NOT NULL
                   GROUP BY event_id HAVING COUNT(DISTINCT slot)=5
                 ) SELECT CAST((s.source_ordinal-1)/? AS INTEGER)+1 AS batch,
                 MIN(s.source_ordinal) AS start_ordinal,MAX(s.source_ordinal) AS end_ordinal,COUNT(*) AS total,
                 SUM(c.event_id IS NOT NULL) AS completed,
                 SUM(g.status='in_progress') AS in_progress,
                 SUM(g.status='unreviewed') AS unreviewed,
                 SUM(g.status='submitted' AND NOT EXISTS(
                     SELECT 1 FROM slot_reviews r WHERE r.event_id=g.event_id AND r.verdict IN ('wrong','unsure'))) AS goodcase,
                 SUM(g.status='submitted' AND EXISTS(
                     SELECT 1 FROM slot_reviews r WHERE r.event_id=g.event_id AND r.verdict='wrong'
                     AND TRIM(COALESCE(r.revised_r,''))!='')) AS badcase
                 FROM source_events s JOIN review_groups g USING(event_id)
                 LEFT JOIN complete c ON c.event_id=s.event_id
                 GROUP BY batch ORDER BY batch"""
        with self.connect() as con:
            batches = [dict(row) for row in con.execute(sql, (batch_size,))]
        return {"batch_size": batch_size, "batches": batches, **self.summary()}

    def browse(self, query: str = "", kind: str = "all", min_wrong: int = 0,
               min_unknown: int = 0, limit: int = 100, offset: int = 0) -> dict:
        base = """SELECT s.event_id,s.source_ordinal,s.page_id,s.request_id,s.parse_status,
                  s.model_raw_content!='' AS has_model_raw,g.status,
                  SUM(CASE WHEN r.verdict='correct' THEN 1 ELSE 0 END) AS right_count,
                  SUM(CASE WHEN r.verdict='wrong' THEN 1 ELSE 0 END) AS wrong_count,
                  SUM(CASE WHEN r.verdict='unsure' THEN 1 ELSE 0 END) AS unknown_count
                  FROM source_events s JOIN review_groups g USING(event_id)
                  LEFT JOIN slot_reviews r USING(event_id) GROUP BY s.event_id"""
        clauses, args = ["wrong_count>=?", "unknown_count>=?"], [max(0, int(min_wrong)), max(0, int(min_unknown))]
        if query:
            clauses.append("(page_id LIKE ? OR request_id LIKE ? OR event_id LIKE ?)")
            needle = "%" + query.strip() + "%"
            args.extend([needle, needle, needle])
        if kind == "unreviewed": clauses.append("status='unreviewed'")
        elif kind == "good": clauses.extend(["status='submitted'", "wrong_count=0", "unknown_count=0"])
        elif kind == "bad": clauses.append("wrong_count+unknown_count>0")
        elif kind == "wrong": clauses.append("wrong_count>0")
        elif kind == "unknown": clauses.append("unknown_count>0")
        elif kind == "raw": clauses.append("has_model_raw=1")
        where = " AND ".join(clauses)
        limit, offset = min(max(int(limit), 1), 200), max(int(offset), 0)
        with self.connect() as con:
            total = con.execute(f"SELECT COUNT(*) FROM ({base}) x WHERE {where}", args).fetchone()[0]
            rows = [dict(row) for row in con.execute(
                f"SELECT * FROM ({base}) x WHERE {where} ORDER BY source_ordinal LIMIT ? OFFSET ?",
                args + [limit, offset])]
        for row in rows:
            row["slots"] = 5
            row["model_requests"] = 1 if row.pop("has_model_raw") else 0
            row["has_stable_request_image"] = True
        return {"total": total, "items": rows, "limit": limit, "offset": offset}

    def progress_dashboard(self, batch_size: int = 1000) -> dict:
        with self.progress_lock:
            if self._progress_cache is not None and time.monotonic() - self._progress_cache_at < 20:
                return self._progress_cache
            result = self._progress_dashboard_uncached(batch_size)
            self._progress_cache = result
            self._progress_cache_at = time.monotonic()
            return result

    def _progress_dashboard_uncached(self, batch_size: int = 1000) -> dict:
        type_sql = """WITH complete AS (
            SELECT event_id FROM slot_reviews WHERE verdict IS NOT NULL
            GROUP BY event_id HAVING COUNT(DISTINCT slot)=5
            ) SELECT t.question_type AS question_type,COUNT(*) AS samples,
            SUM(r.verdict IS NOT NULL) AS human_reviewed,
            SUM(c.event_id IS NOT NULL) AS human_completed,
            SUM(r.verdict='correct') AS human_right,
            SUM(r.verdict='wrong') AS human_wrong,
            SUM(r.verdict='unsure') AS human_unknown
            FROM source_slot_types t
            JOIN source_events s ON s.event_id=t.event_id
            JOIN review_groups g ON g.event_id=s.event_id
            LEFT JOIN slot_reviews r ON r.event_id=s.event_id AND r.slot=t.slot
            LEFT JOIN complete c ON c.event_id=s.event_id
            GROUP BY question_type"""
        batch_sql = """WITH complete AS (
            SELECT event_id FROM slot_reviews WHERE verdict IS NOT NULL
            GROUP BY event_id HAVING COUNT(DISTINCT slot)=5
            ) SELECT CAST((s.source_ordinal-1)/? AS INTEGER)+1 AS batch,
            COUNT(DISTINCT s.event_id) AS groups_count,COUNT(DISTINCT s.event_id)*5 AS slots,
            SUM(r.verdict IS NOT NULL) AS human_reviewed,
            SUM(c.event_id IS NOT NULL) AS completed_slots,
            COUNT(DISTINCT c.event_id) AS completed_groups,
            SUM(r.verdict='correct') AS human_right,
            SUM(r.verdict='wrong') AS human_wrong,
            SUM(r.verdict='unsure') AS human_unknown,
            COUNT(q.slot) AS rechecked_slots,
            SUM(q.verdict='accurate') AS recheck_accurate,
            SUM(q.verdict='inaccurate') AS recheck_inaccurate,
            COUNT(DISTINCT CASE WHEN g.status='submitted' AND EXISTS(
                SELECT 1 FROM slot_reviews z WHERE z.event_id=s.event_id AND z.verdict='wrong'
                AND TRIM(COALESCE(z.revised_r,''))!='') THEN s.event_id END) AS badcase_groups,
            COUNT(DISTINCT CASE WHEN g.status='submitted' AND EXISTS(
                SELECT 1 FROM slot_reviews z WHERE z.event_id=s.event_id AND z.verdict='unsure') THEN s.event_id END) AS unknowncase_groups,
            COUNT(DISTINCT CASE WHEN g.status='submitted' AND NOT EXISTS(
                SELECT 1 FROM slot_reviews z WHERE z.event_id=s.event_id AND z.verdict IN ('wrong','unsure')) THEN s.event_id END) AS goodcase_groups,
            MAX(r.updated_at) AS latest_annotation
            FROM source_events s JOIN review_groups g USING(event_id)
            LEFT JOIN slot_reviews r USING(event_id)
            LEFT JOIN slot_rechecks q ON q.event_id=s.event_id AND q.slot=r.slot
            LEFT JOIN complete c ON c.event_id=s.event_id
            GROUP BY batch ORDER BY batch"""
        with self.connect() as con:
            types = [dict(row) for row in con.execute(type_sql)]
            batches = [dict(row) for row in con.execute(batch_sql, (batch_size,))]
            marked = con.execute("SELECT COUNT(*) FROM slot_reviews WHERE verdict IS NOT NULL").fetchone()[0]
            completed = con.execute("""SELECT COUNT(*) FROM (
                SELECT event_id FROM slot_reviews WHERE verdict IS NOT NULL
                GROUP BY event_id HAVING COUNT(DISTINCT slot)=5)""").fetchone()[0]
            human_right = con.execute("SELECT COUNT(*) FROM slot_reviews WHERE verdict='correct'").fetchone()[0]
            human_wrong = con.execute("SELECT COUNT(*) FROM slot_reviews WHERE verdict='wrong'").fetchone()[0]
            human_unknown = con.execute("SELECT COUNT(*) FROM slot_reviews WHERE verdict='unsure'").fetchone()[0]
            active_claims = con.execute("SELECT COUNT(*) FROM review_groups WHERE status='in_progress' AND lease_until>?", (now(),)).fetchone()[0]
            stale_empty = con.execute("""SELECT COUNT(*) FROM review_groups g WHERE status='in_progress' AND lease_until<=?
                AND NOT EXISTS(SELECT 1 FROM slot_reviews r WHERE r.event_id=g.event_id AND r.verdict IS NOT NULL)""", (now(),)).fetchone()[0]
            raw_unreviewed = con.execute("SELECT COUNT(*) FROM review_groups WHERE status='unreviewed'").fetchone()[0]
            annotators = con.execute("SELECT COUNT(DISTINCT updated_by) FROM slot_reviews WHERE updated_by IS NOT NULL").fetchone()[0]
            started_groups = con.execute("SELECT COUNT(DISTINCT event_id) FROM slot_reviews").fetchone()[0]
        result = self.summary()
        result.update({"total_slots": result["total"] * 5,
                       "human_reviewed_slots": marked, "human_completed_groups": completed,
                       "human_right": human_right, "human_wrong": human_wrong,
                       "human_unknown": human_unknown,
                       "human_remaining_groups": result["total"] - completed,
                       "human_undecided_slots": result["total"] * 5 - marked,
                       "completed_groups": completed,
                       "inspected_groups": completed,
                       "question_types": types, "batches": batches, "batch_size": batch_size,
                       "workflow": {"effective_unreviewed": raw_unreviewed + stale_empty,
                                    "remaining_groups": result["total"] - completed,
                                    "active_claims": active_claims, "stale_empty_claims": stale_empty,
                                    "annotators": annotators, "started_groups": started_groups}})
        return result

    def queue(self, status="unreviewed", query="", limit=50, after=0) -> list[dict]:
        clauses, args = ["s.source_ordinal>?"], [int(after)]
        if status:
            clauses.append("g.status=?"); args.append(status)
        if query:
            clauses.append("(s.event_id=? OR s.page_id=? OR s.request_id=?)"); args.extend([query] * 3)
        args.append(min(max(int(limit), 1), 100))
        sql = """SELECT s.event_id,s.source_ordinal,s.page_id,s.request_id,s.parse_status,
                 s.duplicate_request_id,g.status,g.claimed_by,g.lease_until,g.version
                 FROM source_events s JOIN review_groups g USING(event_id)
                 WHERE %s ORDER BY s.source_ordinal LIMIT ?""" % " AND ".join(clauses)
        with self.connect() as con:
            return [dict(row) for row in con.execute(sql, args)]

    def item(self, event_id: str) -> dict | None:
        with self.connect() as con:
            row = con.execute("""SELECT s.*,g.status,g.claimed_by,g.lease_until,g.version,
                       g.submitted_by,g.submitted_at,g.updated_at FROM source_events s
                       JOIN review_groups g USING(event_id) WHERE event_id=?""", (event_id,)).fetchone()
            if not row:
                return None
            result = dict(row)
            result["slot_indices"] = json.loads(result.pop("slot_indices_json"))
            result["parsed_slots"] = json.loads(result.pop("parsed_slots_json"))
            result["slots"] = [dict(x) for x in con.execute("SELECT * FROM slot_reviews WHERE event_id=? ORDER BY slot", (event_id,))]
            result["rechecks"] = [dict(x) for x in con.execute(
                "SELECT * FROM slot_rechecks WHERE event_id=? ORDER BY slot", (event_id,)
            )]
            return result

    def item_by_ordinal(self, ordinal: int) -> dict | None:
        with self.connect() as con:
            row = con.execute("SELECT event_id FROM source_events WHERE source_ordinal=?", (int(ordinal),)).fetchone()
        return self.item(row["event_id"]) if row else None

    def navigate(self, ordinal: int, direction: int = 1, status: str = "", start: int = 1,
                 end: int = 2147483647, kind: str = "", actor: str = "") -> dict | None:
        direction = 1 if int(direction) >= 0 else -1
        operator, order = (">", "ASC") if direction > 0 else ("<", "DESC")
        clauses, args = [f"s.source_ordinal {operator} ?", "s.source_ordinal BETWEEN ? AND ?"], [int(ordinal), int(start), int(end)]
        if kind == "goodcase":
            clauses.extend(["g.status='submitted'", "EXISTS(SELECT 1 FROM slot_reviews r WHERE r.event_id=g.event_id AND r.verdict='correct')"])
        elif kind == "badcase":
            clauses.extend(["g.status='submitted'", "EXISTS(SELECT 1 FROM slot_reviews r WHERE r.event_id=g.event_id AND r.verdict='wrong' AND TRIM(COALESCE(r.revised_r,''))!='')"])
        elif kind == "wrong":
            clauses.extend(["g.status='submitted'", "EXISTS(SELECT 1 FROM slot_reviews r WHERE r.event_id=g.event_id AND r.verdict='wrong')"])
        elif kind == "unknown":
            clauses.extend(["g.status='submitted'", "EXISTS(SELECT 1 FROM slot_reviews r WHERE r.event_id=g.event_id AND r.verdict='unsure')"])
        elif status == "partial":
            clauses.extend([
                "(SELECT COUNT(DISTINCT r.slot) FROM slot_reviews r WHERE r.event_id=g.event_id AND r.verdict IS NOT NULL) BETWEEN 1 AND 4",
                "(g.status!='in_progress' OR g.claimed_by=? OR g.lease_until<=?)",
            ])
            args.extend([actor, now()])
        elif status == "unreviewed":
            clauses.append("(g.status='unreviewed' OR (g.status='in_progress' AND g.lease_until<=? AND (SELECT COUNT(DISTINCT r.slot) FROM slot_reviews r WHERE r.event_id=g.event_id AND r.verdict IS NOT NULL)<5))")
            args.append(now())
        elif status == "in_progress" and actor:
            clauses.extend(["g.status='in_progress'", "g.claimed_by=?"])
            args.append(actor)
        elif status:
            clauses.append("g.status=?")
            args.append(status)
        elif actor:
            clauses.append("(g.status!='in_progress' OR g.claimed_by=? OR g.lease_until<=?)")
            args.extend([actor, now()])
        sql = f"""SELECT s.event_id FROM source_events s JOIN review_groups g USING(event_id)
                  WHERE {' AND '.join(clauses)} ORDER BY s.source_ordinal {order} LIMIT 1"""
        with self.connect() as con:
            row = con.execute(sql, args).fetchone()
        return self.item(row["event_id"]) if row else None

    def recheck_pick(self, pool: str, ordinal=None, random_pick=False,
                     start=1, end=2147483647) -> dict | None:
        if pool not in ("goodcase", "badcase", "unknown"):
            raise ValueError("invalid recheck pool")
        eligible = {
            "goodcase": "r.verdict='correct'",
            "badcase": "r.verdict='wrong' AND TRIM(COALESCE(r.revised_r,''))!=''",
            "unknown": "r.verdict='unsure'",
        }[pool]
        pool_clause = "EXISTS(SELECT 1 FROM slot_reviews r WHERE r.event_id=g.event_id AND {})".format(eligible)
        base = """SELECT s.event_id FROM source_events s
                  JOIN review_groups g USING(event_id)
                  WHERE g.status='submitted' AND s.source_ordinal BETWEEN ? AND ?
                  AND {}""".format(pool_clause)
        bounds = (int(start), int(end))
        with self.connect() as con:
            if ordinal is not None:
                row = con.execute(
                    base + " AND s.source_ordinal=? LIMIT 1", bounds + (int(ordinal),)
                ).fetchone()
            elif random_pick:
                # Prefer a group with at least one eligible SLOT not yet checked in this pool.
                row = con.execute(
                    base + """ AND EXISTS(SELECT 1 FROM slot_reviews r
                              WHERE r.event_id=s.event_id AND {}
                              AND NOT EXISTS(SELECT 1 FROM slot_rechecks q
                                  WHERE q.event_id=r.event_id AND q.slot=r.slot AND q.pool=?))
                              ORDER BY RANDOM() LIMIT 1""".format(eligible),
                    bounds + (pool,),
                ).fetchone()
                if not row:
                    row = con.execute(base + " ORDER BY RANDOM() LIMIT 1", bounds).fetchone()
            else:
                raise ValueError("recheck pick mode required")
        return self.item(row["event_id"]) if row else None

    def reviewer_leaderboard(self, day: str = "") -> dict:
        if day:
            try:
                selected_day = datetime.strptime(day, "%Y-%m-%d").date()
            except ValueError:
                raise ValueError("invalid leaderboard date")
        else:
            selected_day = (datetime.now(timezone.utc) + timedelta(hours=8)).date()
        china_tz = timezone(timedelta(hours=8))
        day_start = datetime(
            selected_day.year, selected_day.month, selected_day.day, tzinfo=china_tz
        ).astimezone(timezone.utc)
        day_end = day_start + timedelta(days=1)
        sql = """WITH identities AS (
                   SELECT display_name AS reviewer FROM reviewer_invites
                    WHERE display_name IS NOT NULL AND status='bound'
                   UNION
                   SELECT display_name AS reviewer FROM reviewer_master_keys
                    WHERE status='active'
                 ), annotations AS (
                   SELECT updated_by AS reviewer,COUNT(*) AS annotated_slots,
                          COUNT(DISTINCT event_id) AS annotated_groups
                     FROM slot_reviews WHERE verdict IS NOT NULL GROUP BY updated_by
                 ), checked AS (
                   SELECT original_by AS reviewer,COUNT(*) AS reviewed_slots,
                          SUM(verdict='accurate') AS accurate_slots,
                          SUM(verdict='inaccurate') AS inaccurate_slots,
                          SUM(pool='goodcase') AS goodcase_reviewed,
                          SUM(pool='badcase') AS badcase_reviewed,
                          SUM(pool='unknown') AS unknown_reviewed
                     FROM slot_rechecks WHERE original_by IS NOT NULL GROUP BY original_by
                 ), people AS (
                   SELECT reviewer FROM identities
                   UNION SELECT updated_by FROM slot_reviews
                    WHERE updated_by IS NOT NULL AND verdict IS NOT NULL
                   UNION SELECT original_by FROM slot_rechecks WHERE original_by IS NOT NULL
                 )
                 SELECT p.reviewer,
                        CASE WHEN i.reviewer IS NULL THEN 0 ELSE 1 END AS is_real_name,
                        COALESCE(a.annotated_slots,0) AS annotated_slots,
                        COALESCE(a.annotated_groups,0) AS annotated_groups,
                        COALESCE(c.reviewed_slots,0) AS reviewed_slots,
                        COALESCE(c.accurate_slots,0) AS accurate_slots,
                        COALESCE(c.inaccurate_slots,0) AS inaccurate_slots,
                        COALESCE(c.goodcase_reviewed,0) AS goodcase_reviewed,
                        COALESCE(c.badcase_reviewed,0) AS badcase_reviewed,
                        COALESCE(c.unknown_reviewed,0) AS unknown_reviewed
                   FROM people p
                   LEFT JOIN identities i ON i.reviewer=p.reviewer COLLATE NOCASE
                   LEFT JOIN annotations a ON a.reviewer=p.reviewer COLLATE NOCASE
                   LEFT JOIN checked c ON c.reviewer=p.reviewer COLLATE NOCASE"""
        daily_sql = """WITH identities AS (
                   SELECT display_name AS reviewer FROM reviewer_invites
                    WHERE display_name IS NOT NULL AND status='bound'
                   UNION
                   SELECT display_name AS reviewer FROM reviewer_master_keys
                    WHERE status='active'
                 ), latest AS (
                   SELECT id,event_id,actor AS reviewer,
                          CAST(json_extract(after_json,'$.slot') AS INTEGER) AS slot,
                          json_extract(after_json,'$.verdict') AS verdict,
                          json_extract(after_json,'$.pool') AS pool,
                          ROW_NUMBER() OVER (
                            PARTITION BY actor,event_id,CAST(json_extract(after_json,'$.slot') AS INTEGER)
                            ORDER BY id DESC
                          ) AS sequence
                     FROM review_events
                    WHERE action='recheck_slot' AND created_at>=? AND created_at<?
                 ), daily AS (
                   SELECT reviewer,COUNT(*) AS reviewed_slots,
                          COUNT(DISTINCT event_id) AS reviewed_groups,
                          SUM(verdict='accurate') AS accurate_slots,
                          SUM(verdict='inaccurate') AS inaccurate_slots,
                          SUM(pool='goodcase') AS goodcase_reviewed,
                          SUM(pool='badcase') AS badcase_reviewed,
                          SUM(pool='unknown') AS unknown_reviewed
                     FROM latest WHERE sequence=1 GROUP BY reviewer
                 )
                 SELECT d.reviewer,
                        CASE WHEN i.reviewer IS NULL THEN 0 ELSE 1 END AS is_real_name,
                        d.reviewed_slots,d.reviewed_groups,d.accurate_slots,d.inaccurate_slots,
                        d.goodcase_reviewed,d.badcase_reviewed,d.unknown_reviewed
                   FROM daily d
                   LEFT JOIN identities i ON i.reviewer=d.reviewer COLLATE NOCASE"""
        daily_unique_sql = """SELECT COUNT(*) FROM (
                   SELECT event_id,
                          CAST(json_extract(after_json,'$.slot') AS INTEGER) AS slot
                     FROM review_events
                    WHERE action='recheck_slot' AND created_at>=? AND created_at<?
                    GROUP BY event_id,slot
                 )"""
        with self.connect() as con:
            rows = [dict(row) for row in con.execute(sql)]
            daily_rows = [dict(row) for row in con.execute(
                daily_sql, (day_start.isoformat(), day_end.isoformat())
            )]
            daily_unique_slots = con.execute(
                daily_unique_sql, (day_start.isoformat(), day_end.isoformat())
            ).fetchone()[0]
            recheck_totals = con.execute("""SELECT COUNT(*) AS reviewed_slots,
                    SUM(verdict='accurate') AS accurate_slots,
                    SUM(verdict='inaccurate') AS inaccurate_slots
                FROM slot_rechecks""").fetchone()
        for row in rows:
            reviewed = row["reviewed_slots"]
            row["accuracy"] = round(row["accurate_slots"] * 100 / reviewed, 2) if reviewed else None
        rows.sort(key=lambda row: (
            row["reviewed_slots"] == 0,
            -(row["accuracy"] if row["accuracy"] is not None else -1),
            -row["reviewed_slots"],
            -row["is_real_name"],
            row["reviewer"].casefold(),
        ))
        rank = 0
        for row in rows:
            if row["reviewed_slots"]:
                rank += 1
                row["rank"] = rank
            else:
                row["rank"] = None
        daily_rows.sort(key=lambda row: (
            -row["reviewed_slots"], -row["reviewed_groups"],
            -row["is_real_name"], row["reviewer"].casefold(),
        ))
        for index, row in enumerate(daily_rows, 1):
            row["rank"] = index
        daily_person_slots = sum(row["reviewed_slots"] for row in daily_rows)
        daily_accurate_person_slots = sum(row["accurate_slots"] for row in daily_rows)
        daily_inaccurate_person_slots = sum(row["inaccurate_slots"] for row in daily_rows)
        cumulative_unique_slots = recheck_totals["reviewed_slots"] or 0
        return {
            "annotators": len(rows),
            "real_annotators": sum(row["is_real_name"] for row in rows),
            "reviewed_slots": cumulative_unique_slots,
            "accurate_slots": recheck_totals["accurate_slots"] or 0,
            "inaccurate_slots": recheck_totals["inaccurate_slots"] or 0,
            "cumulative_unique_rechecked_slots": cumulative_unique_slots,
            "selected_date": selected_day.isoformat(),
            "today": (datetime.now(timezone.utc) + timedelta(hours=8)).date().isoformat(),
            "daily_reviewers": len(daily_rows),
            "daily_reviewed_slots": daily_person_slots,
            "daily_unique_reviewed_slots": daily_unique_slots,
            "daily_review_person_slots": daily_person_slots,
            "daily_cross_reviewer_duplicates": daily_person_slots - daily_unique_slots,
            "daily_accurate_person_slots": daily_accurate_person_slots,
            "daily_inaccurate_person_slots": daily_inaccurate_person_slots,
            "daily_reviewed_groups": sum(row["reviewed_groups"] for row in daily_rows),
            "daily_items": daily_rows,
            "items": rows,
        }

    def random_special_item(self, kind: str, start=1, end=2147483647, actor="") -> dict | None:
        if kind not in ("wrong", "unknown"):
            raise ValueError("invalid special pool")
        verdict = "wrong" if kind == "wrong" else "unsure"
        base = """SELECT s.event_id FROM source_events s
                  JOIN review_groups g USING(event_id)
                  WHERE g.status='submitted' AND s.source_ordinal BETWEEN ? AND ?
                  AND EXISTS(SELECT 1 FROM slot_reviews r
                      WHERE r.event_id=g.event_id AND r.verdict=?)"""
        args = [int(start), int(end), verdict]
        with self.connect() as con:
            row = None
            if actor:
                row = con.execute(
                    base + " AND COALESCE(g.submitted_by,'')!=? ORDER BY RANDOM() LIMIT 1",
                    args + [actor],
                ).fetchone()
            if not row:
                row = con.execute(base + " ORDER BY RANDOM() LIMIT 1", args).fetchone()
        return self.item(row["event_id"]) if row else None

    def _record(self, con, event_id, action, actor, key, before, after):
        stamp = now()
        con.execute("INSERT INTO review_events(event_id,action,actor,idempotency_key,before_json,after_json,created_at) VALUES(?,?,?,?,?,?,?)",
                    (event_id, action, actor, key, json.dumps(before, ensure_ascii=False), json.dumps(after, ensure_ascii=False), stamp))
        return {"event_id": event_id, "action": action, "actor": actor, "idempotency_key": key, "created_at": stamp}

    def _audit(self, event):
        with self.lock:
            with self.audit_path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush(); os.fsync(handle.fileno())

    def claim(self, event_id, actor, key, minutes=20):
        stamp, lease = now(), (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            prior = con.execute("SELECT event_id FROM review_events WHERE idempotency_key=?", (key,)).fetchone()
            if prior:
                if prior["event_id"] != event_id: raise ConflictError("idempotency key reused")
                return self.item(event_id)
            row = con.execute("SELECT * FROM review_groups WHERE event_id=?", (event_id,)).fetchone()
            if not row: raise KeyError(event_id)
            if row["status"] == "submitted": raise ConflictError("already submitted")
            if row["claimed_by"] and row["claimed_by"] != actor and (row["lease_until"] or "") > stamp:
                raise ConflictError("claimed by another reviewer")
            con.execute("UPDATE review_groups SET status='in_progress',claimed_by=?,lease_until=?,updated_at=? WHERE event_id=?", (actor, lease, stamp, event_id))
            event = self._record(con, event_id, "claim", actor, key, dict(row), {"claimed_by": actor, "lease_until": lease})
        self._audit(event)
        return self.item(event_id)

    def save(self, event_id, actor, key, version, slots, submit=False):
        if not isinstance(slots, list) or len(slots) != 5:
            raise ValueError("exactly five slots required")
        stamp = now()
        renewed_lease = (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat()
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            prior = con.execute("SELECT event_id FROM review_events WHERE idempotency_key=?", (key,)).fetchone()
            if prior:
                if prior["event_id"] != event_id: raise ConflictError("idempotency key reused")
                return self.item(event_id)
            group = con.execute("SELECT * FROM review_groups WHERE event_id=?", (event_id,)).fetchone()
            if not group: raise KeyError(event_id)
            revising = group["status"] == "submitted"
            if revising:
                if not submit:
                    raise ConflictError("submitted edits must be resubmitted")
            elif group["claimed_by"] != actor or (group["lease_until"] or "") <= stamp:
                raise ConflictError("live claim required")
            if group["version"] != int(version):
                raise ConflictError(f"version:{group['version']}")
            before = self.item(event_id)
            existing_slots = {
                row["slot"]: row for row in con.execute(
                    "SELECT * FROM slot_reviews WHERE event_id=?", (event_id,)
                )
            }
            for index, slot in enumerate(slots, 1):
                verdict = slot.get("verdict")
                if verdict not in (None, "correct", "wrong", "unsure"):
                    raise ValueError("invalid verdict")
                revised_r = (slot.get("revised_r") or "").strip() or None
                revised_h = slot.get("revised_h")
                if revised_r and revised_h is None:
                    revised_h = 0
                if revised_h not in (None, 0, 1): raise ValueError("invalid revised_h")
                if bool(revised_r) != (revised_h is not None): raise ValueError("correction fields must be paired")
                reason_code = (slot.get("reason_code") or "").strip() or None
                note = (slot.get("note") or "").strip() or None
                old = existing_slots.get(index)
                if verdict is None:
                    if old and old["verdict"] is not None:
                        raise ValueError("cannot clear existing verdict")
                    continue
                incoming = (verdict, revised_r, revised_h, reason_code, note)
                if old:
                    stored = (
                        old["verdict"], old["revised_r"], old["revised_h"],
                        old["reason_code"], old["note"],
                    )
                    if incoming == stored:
                        continue
                con.execute("""INSERT INTO slot_reviews VALUES(?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(event_id,slot) DO UPDATE SET verdict=excluded.verdict,revised_r=excluded.revised_r,
                    revised_h=excluded.revised_h,reason_code=excluded.reason_code,note=excluded.note,
                    updated_by=excluded.updated_by,updated_at=excluded.updated_at""",
                    (event_id, index, verdict, revised_r, revised_h,
                     reason_code, note, actor, stamp))
            if submit and any(slot.get("verdict") not in ("correct", "wrong", "unsure") for slot in slots):
                raise ValueError("all five verdicts required")
            new_version = group["version"] + 1
            status = "submitted" if submit else "in_progress"
            con.execute("""UPDATE review_groups SET status=?,version=?,submitted_by=?,submitted_at=?,updated_at=?,
                        lease_until=? WHERE event_id=?""", (status, new_version, actor if submit else group["submitted_by"], stamp if submit else group["submitted_at"], stamp, stamp if submit else renewed_lease, event_id))
            after = {"status": status, "version": new_version, "slots": slots}
            action = "revise" if revising else ("submit" if submit else "draft")
            event = self._record(con, event_id, action, actor, key, before or {}, after)
        self._audit(event)
        return self.item(event_id)

    def save_recheck(self, event_id, slot, actor, key, verdict, note="", pool=""):
        try:
            slot = int(slot)
        except (TypeError, ValueError):
            raise ValueError("invalid recheck slot")
        if slot not in range(1, 6):
            raise ValueError("invalid recheck slot")
        if verdict not in ("accurate", "inaccurate"):
            raise ValueError("invalid recheck verdict")
        if pool not in ("goodcase", "badcase", "unknown"):
            raise ValueError("invalid recheck pool")
        stamp = now()
        with self.connect() as con:
            con.execute("BEGIN IMMEDIATE")
            prior = con.execute("SELECT event_id FROM review_events WHERE idempotency_key=?", (key,)).fetchone()
            if prior:
                if prior["event_id"] != event_id: raise ConflictError("idempotency key reused")
                return self.item(event_id)
            group = con.execute("SELECT status FROM review_groups WHERE event_id=?", (event_id,)).fetchone()
            if not group: raise KeyError(event_id)
            if group["status"] != "submitted": raise ConflictError("submitted group required")
            original = con.execute(
                "SELECT verdict,revised_r,updated_by FROM slot_reviews WHERE event_id=? AND slot=?",
                (event_id, slot),
            ).fetchone()
            if not original:
                raise ConflictError("original annotation required")
            if original["verdict"] == "unsure":
                actual_pool = "unknown"
            elif original["verdict"] == "wrong" and (original["revised_r"] or "").strip():
                actual_pool = "badcase"
            elif original["verdict"] == "correct":
                actual_pool = "goodcase"
            else:
                raise ConflictError("slot is not eligible for recheck")
            if pool != actual_pool: raise ConflictError("recheck pool changed")
            old = con.execute("SELECT * FROM slot_rechecks WHERE event_id=? AND slot=?", (event_id, slot)).fetchone()
            con.execute("""INSERT INTO slot_rechecks(event_id,slot,verdict,pool,note,reviewed_by,reviewed_at,original_by)
                VALUES(?,?,?,?,?,?,?,?) ON CONFLICT(event_id,slot) DO UPDATE SET verdict=excluded.verdict,
                pool=excluded.pool,note=excluded.note,reviewed_by=excluded.reviewed_by,
                reviewed_at=excluded.reviewed_at,
                original_by=COALESCE(slot_rechecks.original_by,excluded.original_by)""",
                (event_id, slot, verdict, pool, (note or "").strip() or None, actor, stamp, original["updated_by"]))
            after = {"slot": slot, "verdict": verdict, "pool": pool,
                     "note": (note or "").strip() or None, "original_by": original["updated_by"]}
            event = self._record(con, event_id, "recheck_slot", actor, key, dict(old) if old else {}, after)
        self._audit(event)
        return self.item(event_id)
