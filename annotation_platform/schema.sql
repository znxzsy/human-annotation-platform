CREATE TABLE IF NOT EXISTS source_events (
  event_id TEXT PRIMARY KEY, source_ordinal INTEGER NOT NULL UNIQUE,
  source_shard TEXT NOT NULL, page_id TEXT NOT NULL, request_id TEXT NOT NULL,
  duplicate_request_id INTEGER NOT NULL CHECK(duplicate_request_id IN (0,1)),
  slot_indices_json TEXT NOT NULL, image_ref TEXT NOT NULL,
  model_raw_content TEXT NOT NULL, parse_status TEXT NOT NULL,
  parsed_slots_json TEXT NOT NULL, source_sha256 TEXT NOT NULL,
  source_titles_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX IF NOT EXISTS idx_source_queue ON source_events(source_ordinal,event_id);
CREATE INDEX IF NOT EXISTS idx_source_request ON source_events(request_id);
CREATE TABLE IF NOT EXISTS source_slot_types (
  event_id TEXT NOT NULL REFERENCES source_events(event_id),
  slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 5),
  question_type TEXT NOT NULL,
  PRIMARY KEY(event_id,slot)
);
CREATE INDEX IF NOT EXISTS idx_source_slot_types_question_type ON source_slot_types(question_type);
CREATE TABLE IF NOT EXISTS review_groups (
  event_id TEXT PRIMARY KEY REFERENCES source_events(event_id),
  status TEXT NOT NULL DEFAULT 'unreviewed' CHECK(status IN ('unreviewed','in_progress','submitted','reopened')),
  claimed_by TEXT, lease_until TEXT, version INTEGER NOT NULL DEFAULT 0,
  submitted_by TEXT, submitted_at TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS slot_reviews (
  event_id TEXT NOT NULL REFERENCES source_events(event_id), slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 5),
  verdict TEXT CHECK(verdict IN ('correct','wrong','unsure')), revised_r TEXT,
  revised_h INTEGER CHECK(revised_h IN (0,1)),
  reason_code TEXT CHECK(reason_code IN ('math_error','visual_misread','slot_alignment','format_error','image_blurred','ungradable','no_handwriting','other')),
  note TEXT, updated_by TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY(event_id,slot)
);
CREATE TABLE IF NOT EXISTS review_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT, event_id TEXT NOT NULL REFERENCES source_events(event_id),
  action TEXT NOT NULL, actor TEXT NOT NULL, idempotency_key TEXT NOT NULL UNIQUE,
  before_json TEXT NOT NULL, after_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS group_rechecks (
  event_id TEXT PRIMARY KEY REFERENCES source_events(event_id),
  verdict TEXT NOT NULL CHECK(verdict IN ('accurate','inaccurate')),
  pool TEXT NOT NULL CHECK(pool IN ('goodcase','badcase')),
  note TEXT, reviewed_by TEXT NOT NULL, reviewed_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_group_rechecks_pool_verdict ON group_rechecks(pool,verdict);
CREATE TABLE IF NOT EXISTS slot_rechecks (
  event_id TEXT NOT NULL REFERENCES source_events(event_id),
  slot INTEGER NOT NULL CHECK(slot BETWEEN 1 AND 5),
  verdict TEXT NOT NULL CHECK(verdict IN ('accurate','inaccurate')),
  pool TEXT NOT NULL CHECK(pool IN ('goodcase','badcase','unknown')),
  note TEXT, reviewed_by TEXT NOT NULL, reviewed_at TEXT NOT NULL,
  original_by TEXT,
  final_verdict TEXT CHECK(final_verdict IN ('correct','wrong','unsure')),
  final_r TEXT,
  final_h INTEGER CHECK(final_h IN (0,1)),
  final_reason_code TEXT CHECK(final_reason_code IN (
    'math_error','visual_misread','slot_alignment','format_error',
    'image_blurred','ungradable','no_handwriting','other'
  )),
  PRIMARY KEY(event_id,slot)
);
CREATE INDEX IF NOT EXISTS idx_slot_rechecks_pool_verdict ON slot_rechecks(pool,verdict);
CREATE TABLE IF NOT EXISTS reviewer_invites (
  code_hash TEXT PRIMARY KEY,
  code_tail TEXT NOT NULL,
  display_name TEXT,
  status TEXT NOT NULL DEFAULT 'unused' CHECK(status IN ('unused','bound','disabled')),
  created_at TEXT NOT NULL,
  bound_at TEXT,
  last_login_at TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_reviewer_invites_display_name
  ON reviewer_invites(display_name COLLATE NOCASE) WHERE display_name IS NOT NULL;
CREATE TABLE IF NOT EXISTS reviewer_master_keys (
  code_hash TEXT PRIMARY KEY,
  code_tail TEXT NOT NULL,
  display_name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled')),
  created_at TEXT NOT NULL,
  last_login_at TEXT,
  use_count INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS auth_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  action TEXT NOT NULL,
  display_name TEXT,
  code_tail TEXT,
  client_ip TEXT,
  user_agent TEXT,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_auth_events_created ON auth_events(created_at);
CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
