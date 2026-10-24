"""Export analysis frames to a formatted Excel workbook plus CSVs."""
from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from . import analyse, db, llm, scope

OUT_DIR = Path(os.getenv("WATERS_OUT", "outputs"))
HDR_FILL = PatternFill("solid", fgColor="1F3864")
HDR_FONT = Font(name="Arial", size=10, bold=True, color="FFFFFF")
BODY_FONT = Font(name="Arial", size=10)


def _readme_frame(profile: dict | None = None) -> pd.DataFrame:
    profile = profile or {"name": "Singapore", "code": "sg", "languages": ["en"]}
    with db.connect() as con:
        c = db.counts(con)
        spot = db.spot_summary(con)
        checked = scope.summary(con)
        models = [r["m"] for r in con.execute(
            "SELECT DISTINCT model_version m FROM mentions ORDER BY m")]
        srcs = con.execute(
            "SELECT s.platform, COUNT(d.id) n FROM sources s "
            "LEFT JOIN documents d ON d.source_id = s.id "
            "WHERE s.in_scope = 1 GROUP BY s.platform").fetchall()
        in_scope = con.execute(
            "SELECT COUNT(*) n FROM documents d JOIN sources s ON s.id = d.source_id "
            "WHERE s.in_scope = 1").fetchone()["n"]
        excluded = con.execute(
            "SELECT s.exclusion_note note, COUNT(d.id) n FROM sources s "
            "JOIN documents d ON d.source_id = s.id "
            "WHERE s.in_scope = 0 GROUP BY s.exclusion_note ORDER BY n DESC").fetchall()
        # Date range must describe the analysed corpus, not the discarded one.
        span = con.execute(
            "SELECT MIN(d.posted_at) a, MAX(d.posted_at) b FROM documents d "
            "JOIN sources s ON s.id = d.source_id "
            "WHERE s.in_scope = 1 AND d.posted_at IS NOT NULL").fetchone()
        langs = con.execute(
            "SELECT COALESCE(d.language, 'not yet detected') l, COUNT(*) n "
            "FROM documents d JOIN sources s ON s.id = d.source_id "
            "WHERE s.in_scope = 1 GROUP BY l ORDER BY n DESC").fetchall()
    n_excluded = sum(r["n"] for r in excluded)
    rows = [
        ("Prepared by", os.getenv("ORG_NAME", "") or "not set"),
        ("Country", f"{profile['name']} ({profile['code']})"),
        ("Search languages", ", ".join(profile.get("languages") or ["en"])),
        ("Generated", datetime.now(timezone.utc).isoformat(timespec="seconds")),
        ("Documents collected", c["documents"]),
        ("Documents in scope", in_scope),
        ("Documents excluded", n_excluded),
        ("Documents classified", c["classified_docs"]),
        ("Mentions coded (in scope)", c["mentions_in_scope"]),
        ("Products in watchlist", c["products"]),
        ("Content date range", f"{span['a']} to {span['b']}" if span and span["a"] else "n/a"),
        ("Minimum cell size", analyse.MIN_CELL),
        ("", ""),
        ("SOURCE TIERS (in scope)", ""),
    ]
    rows += [(f"Documents from {r['platform']}", r["n"]) for r in srcs]
    rows += [("", ""), ("CONTENT LANGUAGES (in scope)", "")]
    rows += [(f"Documents in {r['l']}", r["n"]) for r in langs]
    if excluded:
        rows += [("", ""), ("EXCLUDED FROM ANALYSIS", f"{n_excluded} documents")]
        rows += [(str(r["note"] or "unspecified"), r["n"]) for r in excluded]
    rows += [
        ("", ""),
        ("METHOD", ""),
        ("Analysis unit", "Mention, not document. One document may yield 0..n mentions, "
                          "each with its own need code and sentiment."),
        ("Classification", "LLM-assisted coding against the WATERS taxonomy, with a "
                           "verbatim evidence span required for every mention."),
        ("AI model(s) used", ", ".join(m for m in models if m) or "none yet"),
        ("Languages", "Text is coded in its original language. evidence_span is "
                      "verbatim in that language and is what the hallucination "
                      "check verifies; evidence_span_en is a model translation for "
                      "readability only."),
        ("", ""),
        ("ETHICS AND TERMS", ""),
        ("Robots.txt", "Checked and respected on every domain. Collection halts on any "
                       "403, 429 or disallow."),
        ("Rate limiting", f"{os.getenv('REQUEST_DELAY','2.0')}s minimum per domain, "
                          "exponential backoff on error. A site's own robots.txt "
                          "Crawl-delay is honoured where it asks for more "
                          "(e.g. HardwareZone declares 10s)."),
        ("Anti-bot measures", "Never circumvented. No CAPTCHA solving, no authentication "
                              "bypass. Marketplace review pages are excluded from "
                              "automated collection for this reason."),
        ("Personal data", "Usernames are salted-hashed at ingestion and never stored in "
                          "raw form."),
        ("Sources used", "Official APIs and public static pages only. See SOURCE "
                         "TIERS above for what was actually collected; a platform "
                         "absent from that list contributed nothing."),
        ("Sampling frame", "Search returns some material from outside the "
                           f"{profile['name']} market or outside the category that no "
                           "query string excludes. Any source flagged out of scope is "
                           "listed above with its reason, not deleted."),
    ]
    if checked["videos"]:
        rows += [
            ("", ""),
            ("MARKET CHECK (YouTube)", ""),
            ("Videos collected", checked["videos"]),
            ("Videos excluded", checked["excluded"]),
            ("Videos not yet checked", checked["unchecked"]),
            ("Judged by", ", ".join(checked["models"]) or "not run"),
            ("How", "YouTube search ignores market boundaries. The AI model judged each "
                    "video from its title, channel, description and a sample of "
                    f"comments; only videos about {profile['name']}, or whose "
                    "commenters are from there, are analysed. No person reviewed "
                    "these verdicts unless judged_by says 'manual'. Every video, "
                    "kept or not, is listed with its reason on the Market_Check "
                    "sheet."),
        ]
    rows += [("", ""), ("QUALITY SPOT-CHECK", "")]
    if spot["checked"]:
        rows += [
            ("Quotes checked by a person", spot["checked"]),
            ("Judged correct", f"{spot['correct']} ({spot['pct_correct']}%)"),
            ("Last checked", spot["last_checked"]),
            ("How to read this", "A reviewer saw each quote with the need and sentiment "
                                 "the model gave it and marked it right or wrong. Below "
                                 "about 80% correct, treat the figures with caution."),
        ]
    else:
        rows += [("Status", "Not checked. Figures are model output that no person has "
                            "reviewed. Use the spot-check on the website's Results tab.")]
    return pd.DataFrame(rows, columns=["Field", "Value"])


def documents_frame() -> pd.DataFrame:
    """Every document collected, in scope or not, with its full text.

    Raw_Mentions carries only the evidence span, so without this the corpus the
    analysis rests on is locked inside the SQLite file. Excluded documents are
    included, flagged, with the reason attached: a reader checking the sampling
    decision needs to see what was dropped, not just the count.
    """
    with db.connect() as con:
        return pd.read_sql_query("""
            SELECT d.id AS document_id,
                   s.name          AS source_name,
                   s.platform,
                   s.in_scope,
                   s.exclusion_note,
                   s.scope_verdict,
                   COALESCE(p.brand, 'Unattributed') AS brand,
                   COALESCE(p.category, 'unknown')   AS category,
                   d.language, d.posted_at, d.rating, d.url, d.author_hash,
                   LENGTH(d.raw_text) AS text_length,
                   (SELECT COUNT(*) FROM mentions m
                     WHERE m.document_id = d.id) AS mentions_coded,
                   d.raw_text
            FROM documents d
            JOIN sources s ON s.id = d.source_id
            LEFT JOIN products p ON p.id = d.product_id
            ORDER BY s.in_scope DESC, s.platform, d.posted_at
        """, con)


def _style(ws) -> None:
    for cell in ws[1]:
        cell.fill = HDR_FILL
        cell.font = HDR_FONT
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for row in ws.iter_rows(min_row=2):
        for cell in row:
            cell.font = BODY_FONT
            cell.alignment = Alignment(vertical="top", wrap_text=True)
    for i, col in enumerate(ws.columns, start=1):
        longest = max((len(str(c.value)) for c in col if c.value is not None), default=8)
        ws.column_dimensions[get_column_letter(i)].width = min(max(longest + 2, 10), 55)
    ws.freeze_panes = "A2"
    if ws.max_row > 1:
        ws.auto_filter.ref = ws.dimensions


def run(write_csv: bool = True, profile: dict | None = None) -> str:
    frames = analyse.run_all()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    code = f"{profile['code']}_" if profile else ""
    xlsx = OUT_DIR / f"waters_sentiment_{code}{stamp}.xlsx"

    with pd.ExcelWriter(xlsx, engine="openpyxl") as xw:
        _readme_frame(profile).to_excel(xw, sheet_name="README", index=False)
        with db.connect() as con:
            checks = scope.frame(con)
        if not checks.empty:
            checks.to_excel(xw, sheet_name="Market_Check", index=False)
        for name, df in frames.items():
            df.to_excel(xw, sheet_name=name[:31], index=False)
        for ws in xw.book.worksheets:
            _style(ws)

    if write_csv:
        csv_dir = OUT_DIR / "csv"
        csv_dir.mkdir(exist_ok=True)
        for name, df in frames.items():
            df.to_csv(csv_dir / f"{name}.csv", index=False)
        # CSV only: full post text across thousands of rows would bloat the
        # workbook without adding anything analysable to it.
        documents_frame().to_csv(csv_dir / "Raw_Documents.csv", index=False)
        if not checks.empty:
            checks.to_csv(csv_dir / "Market_Check.csv", index=False)
    return str(xlsx)
