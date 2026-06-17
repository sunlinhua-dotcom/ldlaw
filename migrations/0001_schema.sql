-- LDLAWQ D1 schema — 合并 knowledge.db + app.db（执行顺序敏感，勿乱改）
PRAGMA foreign_keys = ON;

-- ======== knowledge ========

CREATE TABLE IF NOT EXISTS region (
  id        INTEGER PRIMARY KEY,
  code      TEXT NOT NULL UNIQUE,
  name      TEXT NOT NULL,
  level     TEXT NOT NULL CHECK (level IN ('country','province','city')),
  parent_id INTEGER REFERENCES region(id)
);

CREATE TABLE IF NOT EXISTS topic (
  id        INTEGER PRIMARY KEY,
  name      TEXT NOT NULL UNIQUE,
  parent_id INTEGER REFERENCES topic(id),
  sort      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS legal_source (
  id              INTEGER PRIMARY KEY,
  title           TEXT NOT NULL,
  doc_no          TEXT,
  issuer          TEXT,
  level           TEXT NOT NULL CHECK (level IN
    ('law','admin_reg','judicial_interp','dept_rule','local_reg','local_rule','normative_doc')),
  region_id       INTEGER NOT NULL REFERENCES region(id),
  publish_date    TEXT,
  effective_date  TEXT,
  expire_date     TEXT,
  status          TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','revised','repealed')),
  full_text       TEXT,
  source_url      TEXT,
  coverage        TEXT NOT NULL DEFAULT 'full' CHECK (coverage IN ('full','partial')),
  version         INTEGER NOT NULL DEFAULT 1,
  prev_version_id INTEGER REFERENCES legal_source(id)
);

CREATE TABLE IF NOT EXISTS legal_article (
  id         INTEGER PRIMARY KEY,
  source_id  INTEGER NOT NULL REFERENCES legal_source(id),
  article_no TEXT NOT NULL,
  clause_no  TEXT,
  text       TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','revised','repealed')),
  verified   INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_article_source ON legal_article(source_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_article_unique ON legal_article(source_id, article_no, ifnull(clause_no,''));

CREATE TABLE IF NOT EXISTS article_topic (
  article_id INTEGER NOT NULL REFERENCES legal_article(id),
  topic_id   INTEGER NOT NULL REFERENCES topic(id),
  PRIMARY KEY (article_id, topic_id)
);

CREATE TABLE IF NOT EXISTS region_param (
  id              INTEGER PRIMARY KEY,
  region_id       INTEGER NOT NULL REFERENCES region(id),
  param_key       TEXT NOT NULL,
  value           TEXT NOT NULL,
  period          TEXT NOT NULL,
  basis_source_id INTEGER REFERENCES legal_source(id),
  effective_date  TEXT,
  expire_date     TEXT,
  verified        INTEGER NOT NULL DEFAULT 0,
  verified_by     TEXT,
  verified_at     TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_param_unique ON region_param(region_id, param_key, period);

CREATE TABLE IF NOT EXISTS entry (
  id          INTEGER PRIMARY KEY,
  title       TEXT NOT NULL,
  slug        TEXT UNIQUE,
  topic_id    INTEGER REFERENCES topic(id),
  body        TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'draft' CHECK (status IN
    ('draft','in_review','published','needs_recheck','archived')),
  version     INTEGER NOT NULL DEFAULT 1,
  reviewed_by TEXT,
  reviewed_at TEXT,
  basis_date  TEXT,
  recheck_due TEXT
);

CREATE TABLE IF NOT EXISTS entry_region (
  entry_id  INTEGER NOT NULL REFERENCES entry(id),
  region_id INTEGER NOT NULL REFERENCES region(id),
  PRIMARY KEY (entry_id, region_id)
);

CREATE TABLE IF NOT EXISTS entry_citation (
  entry_id   INTEGER NOT NULL REFERENCES entry(id),
  article_id INTEGER NOT NULL REFERENCES legal_article(id),
  quote_text TEXT,
  position   INTEGER,
  PRIMARY KEY (entry_id, article_id)
);

CREATE TABLE IF NOT EXISTS template (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  category    TEXT NOT NULL,
  description TEXT,
  fill_guide  TEXT,
  risk_notes  TEXT
);

CREATE TABLE IF NOT EXISTS dispute_tag (
  id   INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS case_record (
  id             INTEGER PRIMARY KEY,
  case_no        TEXT,
  court          TEXT,
  region_id      INTEGER REFERENCES region(id),
  trial_level    TEXT,
  cause          TEXT,
  facts_summary  TEXT,
  gist           TEXT,
  result         TEXT CHECK (result IN ('employer_win','employee_win','partial')),
  decided_date   TEXT,
  source_channel TEXT NOT NULL CHECK (source_channel IN
    ('official_release','licensed_db','partner_lawyer')),
  license_note   TEXT,
  anonymized     INTEGER NOT NULL DEFAULT 1,
  verified       INTEGER NOT NULL DEFAULT 0,
  file_key       TEXT
);

CREATE TABLE IF NOT EXISTS case_tag (
  case_id INTEGER NOT NULL REFERENCES case_record(id),
  tag_id  INTEGER NOT NULL REFERENCES dispute_tag(id),
  PRIMARY KEY (case_id, tag_id)
);

CREATE TABLE IF NOT EXISTS case_citation (
  case_id    INTEGER NOT NULL REFERENCES case_record(id),
  article_id INTEGER NOT NULL REFERENCES legal_article(id),
  PRIMARY KEY (case_id, article_id)
);

CREATE TABLE IF NOT EXISTS meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);

-- FTS5 虚拟表（中文二元组检索）
CREATE VIRTUAL TABLE IF NOT EXISTS fts_article USING fts5(seg);
CREATE VIRTUAL TABLE IF NOT EXISTS fts_case    USING fts5(seg);

-- ======== app ========

CREATE TABLE IF NOT EXISTS org (
  id        INTEGER PRIMARY KEY,
  name      TEXT NOT NULL,
  industry  TEXT,
  size      TEXT,
  region_id INTEGER
);

CREATE TABLE IF NOT EXISTS qa_session (
  id         INTEGER PRIMARY KEY,
  org_id     INTEGER,
  user_id    INTEGER,
  region_id  INTEGER,
  facts      TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS qa_message (
  id             INTEGER PRIMARY KEY,
  session_id     INTEGER NOT NULL REFERENCES qa_session(id),
  role           TEXT NOT NULL CHECK (role IN ('user','assistant')),
  content        TEXT NOT NULL,
  facts          TEXT,
  route          TEXT,
  hit_entry_id   INTEGER,
  calculator_key TEXT,
  citations      TEXT,
  confidence     REAL,
  feedback       TEXT,
  escalated      INTEGER NOT NULL DEFAULT 0,
  created_at     TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_qa_message_session ON qa_message(session_id);

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
