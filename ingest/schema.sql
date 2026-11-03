-- Billboard core schema. One row per bill; child tables hang off bill_id.
-- bill_id format: "{congress}-{type}-{number}", e.g. "118-hr-815".

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS bills (
    bill_id            TEXT PRIMARY KEY,
    congress           INTEGER NOT NULL,
    bill_type          TEXT NOT NULL,          -- hr, s, hjres, sjres, hconres, sconres, hres, sres
    number             INTEGER NOT NULL,
    title              TEXT,
    short_title        TEXT,
    origin_chamber     TEXT,
    introduced_date    TEXT,
    update_date        TEXT,
    policy_area        TEXT,
    sponsor_bioguide   TEXT,
    sponsor_name       TEXT,
    sponsor_party      TEXT,
    sponsor_state      TEXT,
    cosponsor_count    INTEGER DEFAULT 0,
    action_count       INTEGER DEFAULT 0,
    latest_action_date TEXT,
    latest_action_text TEXT,
    status             TEXT,                   -- derived; see ingest/status.py
    law_type           TEXT,                   -- "Public Law" / "Private Law"
    law_number         TEXT,
    summary            TEXT,                   -- latest CRS summary (HTML stripped)
    latest_text_url    TEXT,                   -- URL of latest text version (htm/xml)
    word_count         INTEGER,                -- filled by `textstats`
    source             TEXT,                   -- govinfo-bulk | congress-api
    ingested_at        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_bills_congress ON bills(congress);
CREATE INDEX IF NOT EXISTS idx_bills_status ON bills(status);
CREATE INDEX IF NOT EXISTS idx_bills_introduced ON bills(introduced_date);
CREATE INDEX IF NOT EXISTS idx_bills_policy ON bills(policy_area);

CREATE TABLE IF NOT EXISTS actions (
    bill_id      TEXT NOT NULL REFERENCES bills(bill_id) ON DELETE CASCADE,
    seq          INTEGER NOT NULL,
    action_date  TEXT,
    action_code  TEXT,
    action_type  TEXT,
    chamber      TEXT,
    text         TEXT,
    PRIMARY KEY (bill_id, seq)
);

CREATE TABLE IF NOT EXISTS cosponsors (
    bill_id         TEXT NOT NULL REFERENCES bills(bill_id) ON DELETE CASCADE,
    bioguide_id     TEXT NOT NULL,
    name            TEXT,
    party           TEXT,
    state           TEXT,
    sponsorship_date TEXT,
    is_original     INTEGER,
    withdrawn_date  TEXT,
    PRIMARY KEY (bill_id, bioguide_id)
);

CREATE TABLE IF NOT EXISTS subjects (
    bill_id  TEXT NOT NULL REFERENCES bills(bill_id) ON DELETE CASCADE,
    subject  TEXT NOT NULL,
    PRIMARY KEY (bill_id, subject)
);

CREATE INDEX IF NOT EXISTS idx_subjects_subject ON subjects(subject);

-- Edges for the network map. related_id may point at a bill we have not ingested
-- (e.g. from an older Congress), so no FK on it.
CREATE TABLE IF NOT EXISTS related_bills (
    bill_id       TEXT NOT NULL REFERENCES bills(bill_id) ON DELETE CASCADE,
    related_id    TEXT NOT NULL,
    relationship  TEXT NOT NULL,     -- e.g. "Identical bill", "Related bill", "Procedurally-related"
    identified_by TEXT,              -- e.g. "CRS", "House"
    PRIMARY KEY (bill_id, related_id, relationship)
);

CREATE INDEX IF NOT EXISTS idx_related_rel ON related_bills(related_id);

CREATE TABLE IF NOT EXISTS committees (
    bill_id   TEXT NOT NULL REFERENCES bills(bill_id) ON DELETE CASCADE,
    system_code TEXT NOT NULL,
    name      TEXT,
    chamber   TEXT,
    PRIMARY KEY (bill_id, system_code)
);

CREATE TABLE IF NOT EXISTS text_versions (
    bill_id   TEXT NOT NULL REFERENCES bills(bill_id) ON DELETE CASCADE,
    version   TEXT NOT NULL,          -- e.g. "Introduced in House", "Enrolled Bill"
    date      TEXT,
    url       TEXT,
    PRIMARY KEY (bill_id, version, url)
);

-- Filled later by the news step (GDELT); one row per bill per day with coverage.
CREATE TABLE IF NOT EXISTS media_daily (
    bill_id   TEXT NOT NULL REFERENCES bills(bill_id) ON DELETE CASCADE,
    day       TEXT NOT NULL,
    articles  INTEGER NOT NULL,
    tone      REAL,
    PRIMARY KEY (bill_id, day)
);

-- Ingestion bookkeeping so reruns and nightly updates are incremental.
CREATE TABLE IF NOT EXISTS ingest_log (
    key        TEXT PRIMARY KEY,     -- e.g. "bulk:118:hr" or "api:last_update"
    value      TEXT,
    updated_at TEXT NOT NULL
);

-- Last-modified stamp govinfo reported for each BILLSTATUS file we have ingested.
-- Nightly sync refetches only files whose stamp changed.
CREATE TABLE IF NOT EXISTS file_state (
    path          TEXT PRIMARY KEY,   -- e.g. "118/hr/BILLSTATUS-118hr815.xml"
    last_modified TEXT NOT NULL
);

-- Stage 2 (site) additions ---------------------------------------------------

-- Headlines for a bill from the GDELT DOC API (links only, never article text).
CREATE TABLE IF NOT EXISTS media_articles (
    bill_id   TEXT NOT NULL REFERENCES bills(bill_id) ON DELETE CASCADE,
    url       TEXT NOT NULL,
    title     TEXT,
    domain    TEXT,
    seen_date TEXT,
    PRIMARY KEY (bill_id, url)
);

-- What the coverage job did for each bill: the query it ran, when, and whether the
-- name was judged too generic to count (M3 match check).
CREATE TABLE IF NOT EXISTS media_state (
    bill_id     TEXT PRIMARY KEY REFERENCES bills(bill_id) ON DELETE CASCADE,
    query       TEXT,
    generic     INTEGER NOT NULL DEFAULT 0,
    fetched_at  TEXT,
    status      TEXT                          -- ok | empty | error
);

-- Passage-odds history (B3). One row each time an active bill's stage or band changes,
-- so a bill page can say "rated Typical at introduction, Strong after passing the House".
CREATE TABLE IF NOT EXISTS odds_history (
    bill_id  TEXT NOT NULL REFERENCES bills(bill_id) ON DELETE CASCADE,
    day      TEXT NOT NULL,
    stage    INTEGER NOT NULL,   -- 0 introduced, 1 reported, 2 passed one chamber, 3 passed both
    p        REAL NOT NULL,
    band     INTEGER NOT NULL,   -- 0 long shot, 1 typical, 2 better than most, 3 strong
    PRIMARY KEY (bill_id, day)
);

-- Cached network-map layouts (N3), keyed by the bill whose neighborhood it is.
-- sig changes when the neighborhood's nodes, links or dot sizes change.
CREATE TABLE IF NOT EXISTS net_layout (
    key  TEXT PRIMARY KEY,
    sig  TEXT NOT NULL,
    xy   TEXT NOT NULL
);
