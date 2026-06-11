-- knowledge.db —— 只读知识库（内容发布 = 整库重建替换，见 PRD §8.1）
PRAGMA foreign_keys = ON;

CREATE TABLE region (
  id        INTEGER PRIMARY KEY,
  code      TEXT NOT NULL UNIQUE,
  name      TEXT NOT NULL,
  level     TEXT NOT NULL CHECK (level IN ('country','province','city')),
  parent_id INTEGER REFERENCES region(id)
);

CREATE TABLE topic (
  id        INTEGER PRIMARY KEY,
  name      TEXT NOT NULL UNIQUE,
  parent_id INTEGER REFERENCES topic(id),
  sort      INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE legal_source (
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
  version         INTEGER NOT NULL DEFAULT 1,
  prev_version_id INTEGER REFERENCES legal_source(id)
);

CREATE TABLE legal_article (
  id         INTEGER PRIMARY KEY,
  source_id  INTEGER NOT NULL REFERENCES legal_source(id),
  article_no TEXT NOT NULL,
  clause_no  TEXT,
  text       TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active','revised','repealed')),
  verified   INTEGER NOT NULL DEFAULT 0  -- 0 = 条文未经官方源逐字核验，禁止上生产
);
CREATE INDEX idx_article_source ON legal_article(source_id);
CREATE UNIQUE INDEX idx_article_unique ON legal_article(source_id, article_no, ifnull(clause_no,''));

CREATE TABLE article_topic (
  article_id INTEGER NOT NULL REFERENCES legal_article(id),
  topic_id   INTEGER NOT NULL REFERENCES topic(id),
  PRIMARY KEY (article_id, topic_id)
);
CREATE INDEX idx_article_topic_topic ON article_topic(topic_id);

CREATE TABLE region_param (
  id              INTEGER PRIMARY KEY,
  region_id       INTEGER NOT NULL REFERENCES region(id),
  param_key       TEXT NOT NULL,
  value           TEXT NOT NULL CHECK (json_valid(value)),
  period          TEXT NOT NULL,
  basis_source_id INTEGER REFERENCES legal_source(id),
  effective_date  TEXT,
  expire_date     TEXT,
  verified        INTEGER NOT NULL DEFAULT 0,
  verified_by     TEXT,
  verified_at     TEXT
);
CREATE UNIQUE INDEX idx_param_unique ON region_param(region_id, param_key, period);

CREATE TABLE entry (
  id          INTEGER PRIMARY KEY,
  title       TEXT NOT NULL,
  slug        TEXT UNIQUE,
  topic_id    INTEGER REFERENCES topic(id),
  body        TEXT NOT NULL CHECK (json_valid(body)),
  status      TEXT NOT NULL DEFAULT 'draft' CHECK (status IN
    ('draft','in_review','published','needs_recheck','archived')),
  version     INTEGER NOT NULL DEFAULT 1,
  reviewed_by TEXT,
  reviewed_at TEXT,
  basis_date  TEXT,
  recheck_due TEXT
);

CREATE TABLE entry_region (
  entry_id  INTEGER NOT NULL REFERENCES entry(id),
  region_id INTEGER NOT NULL REFERENCES region(id),
  PRIMARY KEY (entry_id, region_id)
);
CREATE INDEX idx_entry_region_region ON entry_region(region_id);

CREATE TABLE entry_citation (
  entry_id   INTEGER NOT NULL REFERENCES entry(id),
  article_id INTEGER NOT NULL REFERENCES legal_article(id),
  quote_text TEXT,
  position   INTEGER,
  PRIMARY KEY (entry_id, article_id)
);

CREATE TABLE template (
  id          INTEGER PRIMARY KEY,
  name        TEXT NOT NULL,
  category    TEXT NOT NULL CHECK (category IN
    ('onboarding','in_service','policy','exit','dispute','special')),
  description TEXT,
  fill_guide  TEXT,
  risk_notes  TEXT
);

CREATE TABLE template_version (
  id           INTEGER PRIMARY KEY,
  template_id  INTEGER NOT NULL REFERENCES template(id),
  region_id    INTEGER NOT NULL REFERENCES region(id),
  file_key     TEXT,
  version      INTEGER NOT NULL DEFAULT 1,
  changelog    TEXT,
  reviewed_by  TEXT,
  published_at TEXT
);

CREATE TABLE dispute_tag (
  id   INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE
);

CREATE TABLE case_record (
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
  file_key       TEXT
);

CREATE TABLE case_tag (
  case_id INTEGER NOT NULL REFERENCES case_record(id),
  tag_id  INTEGER NOT NULL REFERENCES dispute_tag(id),
  PRIMARY KEY (case_id, tag_id)
);

CREATE TABLE case_citation (
  case_id    INTEGER NOT NULL REFERENCES case_record(id),
  article_id INTEGER NOT NULL REFERENCES legal_article(id),
  PRIMARY KEY (case_id, article_id)
);

CREATE TABLE meta (
  key   TEXT PRIMARY KEY,
  value TEXT
);
