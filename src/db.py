"""SQLite storage layer for the WATERS sentiment pipeline.

The analysis unit is the MENTION, not the document. One review can discuss several
needs with different sentiment attached to each, so `documents` holds raw text and
`mentions` holds one row per need discussed.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

DB_PATH = Path(os.getenv("WATERS_DB", "data/waters.db"))

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS sources (
    id             INTEGER PRIMARY KEY,
    name           TEXT NOT NULL,
    platform       TEXT NOT NULL,
    url            TEXT,
    collected_at   TEXT,
    robots_allowed INTEGER DEFAULT 1,
    -- 0 excludes the source from classification and analysis without deleting
    -- it. Search returns off-market and off-category material that no query
    -- string filters out, and an auditable flag beats a silent DELETE when the
    -- sampling frame has to be defended.
    in_scope       INTEGER NOT NULL DEFAULT 1,
    exclusion_note TEXT,
    -- Market check (src/scope.py). The verdict is kept apart from in_scope so
    -- the keep/drop rule can change without asking the model again. scope_by
    -- is the model that judged, or 'manual' for a person's decision, which an
    -- automatic re-check never overwrites.
    scope_verdict    TEXT,
    scope_reason     TEXT,
    scope_by         TEXT,
    scope_checked_at TEXT,
    UNIQUE(name, platform)
);

CREATE TABLE IF NOT EXISTS products (
    id          INTEGER PRIMARY KEY,
    brand       TEXT NOT NULL,
    model       TEXT,
    category    TEXT CHECK(category IN
                  ('countertop_dispenser','purifier','jug_filter','unknown')),
    price_sgd   REAL,
    listing_url TEXT,
    UNIQUE(brand, model)
);

CREATE TABLE IF NOT EXISTS documents (
    id           INTEGER PRIMARY KEY,
    source_id    INTEGER REFERENCES sources(id),
    product_id   INTEGER REFERENCES products(id),
    content_hash TEXT UNIQUE NOT NULL,
    author_hash  TEXT,
    url          TEXT,
    posted_at    TEXT,
    rating       REAL,
    language     TEXT DEFAULT 'en',
    raw_text     TEXT NOT NULL,
    collected_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS mentions (
    id            INTEGER PRIMARY KEY,
    document_id   INTEGER NOT NULL REFERENCES documents(id),
    waters_code   TEXT NOT NULL CHECK(waters_code IN
                    ('W','A','T','E','R','S','COST','OTHER')),
    sentiment     TEXT NOT NULL CHECK(sentiment IN
                    ('positive','negative','neutral','mixed')),
    intensity     INTEGER CHECK(intensity BETWEEN 1 AND 3),
    evidence_span TEXT NOT NULL,
    -- English rendering of evidence_span for non-English sources. The verbatim
    -- original stays in evidence_span and is what the hallucination guard checks.
    evidence_span_en TEXT,
    confidence    REAL,
    model_version TEXT,
    classified_at TEXT
);

-- Quality spot-check: a person reads a random coded quote and says whether the
-- need and sentiment the model gave it are right. One verdict per mention.
CREATE TABLE IF NOT EXISTS spot_checks (
    id          INTEGER PRIMARY KEY,
    mention_id  INTEGER NOT NULL UNIQUE REFERENCES mentions(id),
    verdict     TEXT NOT NULL CHECK(verdict IN ('correct','wrong')),
    checked_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS classified_docs (
    document_id INTEGER PRIMARY KEY REFERENCES documents(id),
    model_version TEXT,
    classified_at TEXT,
    mention_count INTEGER DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_doc_source   ON documents(source_id);
CREATE INDEX IF NOT EXISTS idx_doc_product  ON documents(product_id);
CREATE INDEX IF NOT EXISTS idx_men_doc      ON mentions(document_id);
CREATE INDEX IF NOT EXISTS idx_men_code     ON mentions(waters_code);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def content_hash(text: str, url: str = "") -> str:
    """Dedup key. Normalises whitespace and case so trivial edits still collide.

    Deliberately hashes TEXT ONLY: the same review reposted at two URLs is one
    document, not two, so mention volume is never inflated by crossposts. The
    url argument is accepted for backward compatibility and ignored.
    """
    norm = " ".join((text or "").lower().split())
    return hashlib.sha256(norm.encode()).hexdigest()


def author_hash(username: str | None) -> str | None:
    """Salted pseudonymisation. Raw usernames are never stored (PDPA / ethics)."""
    if not username:
        return None
    salt = os.getenv("AUTHOR_SALT", "")
    if not salt:
        raise RuntimeError(
            "AUTHOR_SALT is not set. Set a random value in .env before collecting, "
            "so author pseudonyms are not reversible."
        )
    return hashlib.sha256((salt + username).encode()).hexdigest()[:32]


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    p = Path(path or DB_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init(path: Path | None = None) -> None:
    with connect(path) as con:
        con.executescript(SCHEMA)
        # CREATE TABLE IF NOT EXISTS leaves databases made before these columns
        # existed untouched, so add them here rather than requiring a re-collect.
        have = {r["name"] for r in con.execute("PRAGMA table_info(sources)")}
        for col, ddl in (("in_scope", "INTEGER NOT NULL DEFAULT 1"),
                         ("exclusion_note", "TEXT"), ("scope_verdict", "TEXT"),
                         ("scope_reason", "TEXT"), ("scope_by", "TEXT"),
                         ("scope_checked_at", "TEXT")):
            if col not in have:
                con.execute(f"ALTER TABLE sources ADD COLUMN {col} {ddl}")
        have = {r["name"] for r in con.execute("PRAGMA table_info(mentions)")}
        if "evidence_span_en" not in have:
            con.execute("ALTER TABLE mentions ADD COLUMN evidence_span_en TEXT")


def upsert_source(con: sqlite3.Connection, name: str, platform: str,
                  url: str = "", robots_allowed: bool = True) -> int:
    con.execute(
        "INSERT OR IGNORE INTO sources(name, platform, url, collected_at, robots_allowed)"
        " VALUES(?,?,?,?,?)",
        (name, platform, url, now(), int(robots_allowed)),
    )
    row = con.execute(
        "SELECT id FROM sources WHERE name=? AND platform=?", (name, platform)
    ).fetchone()
    return row["id"]


def upsert_product(con: sqlite3.Connection, brand: str, model: str | None,
                   category: str = "unknown", price_sgd: float | None = None,
                   listing_url: str | None = None) -> int:
    con.execute(
        "INSERT OR IGNORE INTO products(brand, model, category, price_sgd, listing_url)"
        " VALUES(?,?,?,?,?)",
        (brand, model, category, price_sgd, listing_url),
    )
    row = con.execute(
        "SELECT id FROM products WHERE brand=? AND (model IS ? OR model=?)",
        (brand, model, model),
    ).fetchone()
    return row["id"] if row else None


def insert_document(con: sqlite3.Connection, *, source_id: int, raw_text: str,
                    url: str = "", author: str | None = None,
                    posted_at: str | None = None, rating: float | None = None,
                    product_id: int | None = None,
                    language: str | None = None) -> int | None:
    # language is unknown at collection; the classifier detects and records it.
    """Returns the new document id, or None if it was a duplicate."""
    if not raw_text or not raw_text.strip():
        return None
    ch = content_hash(raw_text, url)
    cur = con.execute(
        "INSERT OR IGNORE INTO documents"
        "(source_id, product_id, content_hash, author_hash, url, posted_at,"
        " rating, language, raw_text, collected_at)"
        " VALUES(?,?,?,?,?,?,?,?,?,?)",
        (source_id, product_id, ch, author_hash(author), url, posted_at,
         rating, language, raw_text.strip(), now()),
    )
    return cur.lastrowid if cur.rowcount else None


def insert_mentions(con: sqlite3.Connection, document_id: int,
                    mentions: Iterable[dict[str, Any]], model_version: str) -> int:
    rows = [
        (document_id, m["waters_code"], m["sentiment"], m.get("intensity", 2),
         m["evidence_span"], m.get("evidence_span_en") or m["evidence_span"],
         m.get("confidence"), model_version, now())
        for m in mentions
    ]
    con.executemany(
        "INSERT INTO mentions(document_id, waters_code, sentiment, intensity,"
        " evidence_span, evidence_span_en, confidence, model_version, classified_at)"
        " VALUES(?,?,?,?,?,?,?,?,?)", rows,
    )
    return len(rows)


def set_language(con: sqlite3.Connection, document_id: int, language: str | None) -> None:
    if language:
        con.execute("UPDATE documents SET language=? WHERE id=?",
                    (language.lower()[:8], document_id))


def mark_classified(con: sqlite3.Connection, document_id: int,
                    model_version: str, mention_count: int) -> None:
    con.execute(
        "INSERT OR REPLACE INTO classified_docs"
        "(document_id, model_version, classified_at, mention_count) VALUES(?,?,?,?)",
        (document_id, model_version, now(), mention_count),
    )


def unclassified(con: sqlite3.Connection, limit: int | None = None) -> list[sqlite3.Row]:
    sql = ("SELECT d.id, d.raw_text FROM documents d "
           "LEFT JOIN classified_docs c ON c.document_id = d.id "
           "JOIN sources s ON s.id = d.source_id "
           "WHERE c.document_id IS NULL AND s.in_scope = 1 ORDER BY d.id")
    if limit:
        sql += f" LIMIT {int(limit)}"
    return con.execute(sql).fetchall()


def counts(con: sqlite3.Connection) -> dict[str, int]:
    out = {}
    for t in ("sources", "products", "documents", "mentions", "classified_docs",
              "spot_checks"):
        out[t] = con.execute(f"SELECT COUNT(*) c FROM {t}").fetchone()["c"]
    # Mentions coded before their source was excluded stay in the table but are
    # not analysed; this is the figure the analysis rests on.
    out["mentions_in_scope"] = con.execute(
        "SELECT COUNT(*) c FROM mentions m JOIN documents d ON d.id = m.document_id "
        "JOIN sources s ON s.id = d.source_id WHERE s.in_scope = 1").fetchone()["c"]
    return out


# ------------------------------------------------------------- spot check
def spot_sample(con: sqlite3.Connection, n: int = 20) -> list[dict[str, Any]]:
    """Random coded mentions nobody has checked yet, with their source text."""
    rows = con.execute(
        "SELECT m.id AS mention_id, m.waters_code, m.sentiment, m.evidence_span, "
        "COALESCE(m.evidence_span_en, m.evidence_span) AS evidence_span_en, "
        "d.raw_text, COALESCE(p.brand, '') AS brand "
        "FROM mentions m JOIN documents d ON d.id = m.document_id "
        "JOIN sources s ON s.id = d.source_id "
        "LEFT JOIN products p ON p.id = d.product_id "
        "LEFT JOIN spot_checks c ON c.mention_id = m.id "
        "WHERE c.id IS NULL AND s.in_scope = 1 ORDER BY RANDOM() LIMIT ?",
        (n,)).fetchall()
    return [dict(r) for r in rows]


def record_spot(con: sqlite3.Connection, mention_id: int, verdict: str) -> None:
    con.execute("INSERT OR REPLACE INTO spot_checks(mention_id, verdict, checked_at) "
                "VALUES(?,?,?)", (mention_id, verdict, now()))


def spot_summary(con: sqlite3.Connection) -> dict[str, Any]:
    r = con.execute("SELECT COUNT(*) n, SUM(verdict='correct') ok, MAX(checked_at) last "
                    "FROM spot_checks").fetchone()
    n, ok = r["n"] or 0, r["ok"] or 0
    return {"checked": n, "correct": ok,
            "pct_correct": round(100 * ok / n, 1) if n else None,
            "last_checked": r["last"]}


def clear_classification(con: sqlite3.Connection) -> None:
    """Forget every AI result so the documents are classified again. Collected
    posts and the market check verdicts stay."""
    for table in ("spot_checks", "mentions", "classified_docs"):
        con.execute(f"DELETE FROM {table}")


def copy_to(dest: Path, path: Path | None = None) -> None:
    """A consistent copy of the database, including writes still in the WAL."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    src, out = sqlite3.connect(Path(path or DB_PATH)), sqlite3.connect(dest)
    try:
        src.backup(out)
    finally:
        src.close()
        out.close()
