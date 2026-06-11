-- app.db —— 业务读写库（WAL 模式 + Litestream 备份，见 PRD §8.1）
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS org (
  id        INTEGER PRIMARY KEY,
  name      TEXT NOT NULL,
  industry  TEXT,
  size      TEXT,
  region_id INTEGER
);

CREATE TABLE IF NOT EXISTS org_user (
  id            INTEGER PRIMARY KEY,
  org_id        INTEGER REFERENCES org(id),
  name          TEXT,
  role          TEXT,
  phone         TEXT,
  wechat_openid TEXT
);

CREATE TABLE IF NOT EXISTS subscription (
  id                INTEGER PRIMARY KEY,
  org_id            INTEGER REFERENCES org(id),
  plan              TEXT,
  seats             INTEGER,
  start_date        TEXT,
  end_date          TEXT,
  channel_lawyer_id INTEGER
);

CREATE TABLE IF NOT EXISTS qa_session (
  id         INTEGER PRIMARY KEY,
  org_id     INTEGER,
  user_id    INTEGER,
  region_id  INTEGER,
  facts      TEXT CHECK (facts IS NULL OR json_valid(facts)),  -- 多轮要素累积（T2.4）
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS qa_message (
  id             INTEGER PRIMARY KEY,
  session_id     INTEGER NOT NULL REFERENCES qa_session(id),
  role           TEXT NOT NULL CHECK (role IN ('user','assistant')),
  content        TEXT NOT NULL,
  facts          TEXT CHECK (facts IS NULL OR json_valid(facts)),
  route          TEXT CHECK (route IS NULL OR route IN ('entry_hit','calculator','rag','refuse','clarify')),
  hit_entry_id   INTEGER,
  calculator_key TEXT,
  citations      TEXT CHECK (citations IS NULL OR json_valid(citations)),
  confidence     REAL,
  feedback       TEXT,
  escalated      INTEGER NOT NULL DEFAULT 0,
  created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_qa_message_session ON qa_message(session_id);

CREATE TABLE IF NOT EXISTS lawyer (
  id          INTEGER PRIMARY KEY,
  name        TEXT,
  license_no  TEXT,
  firm        TEXT,
  association TEXT,
  specialties TEXT,
  status      TEXT NOT NULL DEFAULT 'active',
  rating      REAL
);

CREATE TABLE IF NOT EXISTS lawyer_region (
  lawyer_id INTEGER NOT NULL,
  region_id INTEGER NOT NULL,
  PRIMARY KEY (lawyer_id, region_id)
);

CREATE TABLE IF NOT EXISTS referral (
  id                INTEGER PRIMARY KEY,
  session_id        INTEGER,
  org_id            INTEGER,
  lawyer_id         INTEGER,
  question_brief    TEXT,
  consent_at        TEXT,
  status            TEXT NOT NULL DEFAULT 'pending' CHECK (status IN
    ('pending','accepted','in_progress','closed','timeout')),
  first_response_at TEXT,
  outcome           TEXT,
  fee_note          TEXT,
  created_at        TEXT
);

CREATE TABLE IF NOT EXISTS review_log (
  id          INTEGER PRIMARY KEY,
  object_type TEXT CHECK (object_type IN ('entry','template','case','param')),
  object_id   INTEGER,
  action      TEXT CHECK (action IN ('submit','approve','reject','recheck')),
  reviewer_id INTEGER,
  comment     TEXT,
  created_at  TEXT
);

CREATE TABLE IF NOT EXISTS watch_task (
  id               INTEGER PRIMARY KEY,
  source           TEXT,
  found_title      TEXT,
  url              TEXT,
  detected_at      TEXT,
  status           TEXT NOT NULL DEFAULT 'new' CHECK (status IN
    ('new','triaged','ingested','dismissed')),
  linked_source_id INTEGER,
  impact_object_ids TEXT
);

CREATE TABLE IF NOT EXISTS eval_item (
  id               INTEGER PRIMARY KEY,
  question         TEXT NOT NULL,
  region_id        INTEGER,
  topic_id         INTEGER,
  gold_answer      TEXT,
  gold_citations   TEXT,
  author_lawyer_id INTEGER,
  difficulty       TEXT
);

CREATE TABLE IF NOT EXISTS eval_run (
  id                        INTEGER PRIMARY KEY,
  run_at                    TEXT,
  system_version            TEXT,
  score_overall             REAL,
  score_by_topic            TEXT,
  fabricated_citation_count INTEGER
);

CREATE TABLE IF NOT EXISTS template_download (
  id                  INTEGER PRIMARY KEY,
  template_version_id INTEGER NOT NULL,
  org_user_id         INTEGER,
  downloaded_at       TEXT
);
