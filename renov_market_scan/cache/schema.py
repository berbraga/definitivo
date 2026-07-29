"""SQLite DDL for the layered cache.

raw_search is the only layer whose regeneration costs money, so it is written
before any processing and is never overwritten by a reprocessing pass.
"""

SCHEMA_SQL = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS raw_search (
    search_key    TEXT    NOT NULL,
    source        TEXT    NOT NULL,
    phrase_index  INTEGER NOT NULL,
    collected_on  TEXT    NOT NULL,
    payload       TEXT    NOT NULL,
    status        TEXT    NOT NULL,
    PRIMARY KEY (search_key, source, phrase_index, collected_on)
);

CREATE TABLE IF NOT EXISTS listing (
    search_key         TEXT    NOT NULL,
    source             TEXT    NOT NULL,
    collected_on       TEXT    NOT NULL,
    title              TEXT    NOT NULL,
    price_brl          REAL,
    condition          TEXT    NOT NULL,
    url                TEXT    NOT NULL,
    captured_at        TEXT    NOT NULL,
    cited_text         TEXT    NOT NULL DEFAULT '',
    flag_5g_divergent  INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS listing_by_day
    ON listing (collected_on, search_key);
"""
