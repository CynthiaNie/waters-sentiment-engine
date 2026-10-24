"""Market check: keep YouTube videos that speak for the country being studied.

YouTube search ignores market boundaries. A search run for Singapore with
regionCode=SG still returns American pitcher reviews and Indian RO explainers,
and their comments describe other markets. Counted as Singapore opinion they
distort the need shares (they inflate Wellbeing and the jug-filter brands).

Every YouTube video is judged once by the AI model from its title, channel,
description and a sample of its comments, and gets one verdict:

    target_market     about this country, or its commenters are from it
    other_market      made for or discussed by one other country
    global_no_signal  about the category, with no sign of any one market
    wrong_category    shares the words but not the product (air purifiers...)
    off_topic         not about consumer water products

The model only judges; `keep()` decides. Only target_market is kept, plus
global_no_signal when the profile sets youtube.keep_global. Excluded videos are
flagged with the reason, never deleted, so the sampling frame stays auditable.
Other sources (pasted websites, APIs, Reddit, CSV) were chosen by a person and
are not checked.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from . import db, llm

log = logging.getLogger(__name__)

BATCH = 10           # videos per model call
SAMPLE = 12          # comments shown per video
SAMPLE_CHARS = 250   # characters kept per comment

VERDICTS = ("target_market", "other_market", "global_no_signal", "wrong_category",
            "off_topic")

SCHEMA = {
    "type": "object",
    "properties": {
        "videos": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "verdict": {"type": "string", "enum": list(VERDICTS)},
                    "market": {"type": "string"},
                    "reason": {"type": "string"},
                },
                "required": ["id", "verdict", "market", "reason"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["videos"],
    "additionalProperties": False,
}

SYSTEM = """You check which YouTube videos belong in a consumer study of water \
dispensers, water purifiers and filter jugs in {country}. YouTube search ignores \
market boundaries, so a search run for {country} also returns videos made for other \
countries. Their comments describe other markets and must not be counted as \
{country} opinion.

For each video give one verdict:
- target_market: the video is about {country}, or a meaningful share of its comments \
come from {country}. Signals: a {country} creator, channel or media outlet; \
{country} places, retailers, housing or regulators; prices in {currency}; brands \
sold mainly there; local language or slang.
- other_market: made for, or discussed by, consumers in one other country. Signals: \
another currency (US$, RM, ₹, A$, £), retailers or brands sold only there (e.g. \
Walmart, Costco, Target, Kmart, ZeroWater, PUR), other places, units or regulators. \
Put that country in `market`.
- global_no_signal: about the category, but with no sign of {country} or of any one \
other market, e.g. a general science explainer with comments from everywhere.
- wrong_category: shares the words but is about another product: air purifiers, \
aquariums, pool or shower filters, industrial or commercial equipment.
- off_topic: not about consumer water products at all.

Rules:
1. Judge only from the evidence given. Comments count as much as the title: a \
generic title whose commenters quote {currency} prices is target_market.
2. A video made for another country stays other_market even if it reviews brands \
that are also sold in {country}.
3. When the evidence is thin and points nowhere, use global_no_signal, not \
target_market.
4. market: the English name of the country the video is for; {country} for \
target_market; empty for the other verdicts.
5. reason: one short English sentence naming the concrete signals you used.
6. Return one entry per video, using its id."""


def keep(verdict: str | None, keep_global: bool = False) -> bool:
    if verdict == "target_market":
        return True
    return verdict == "global_no_signal" and keep_global


def exclusion_note(verdict: str, market: str, country_name: str) -> str:
    """Short label the workbook groups exclusions by."""
    if verdict == "other_market":
        return f"other market: {market.strip() or 'unspecified'}"
    return {"global_no_signal": f"global, no {country_name} signal",
            "wrong_category": "wrong category",
            "off_topic": "off-topic"}.get(verdict, verdict)


# ----------------------------------------------------------------- evidence
_VID = re.compile(r"[?&]v=([\w-]{11})")


def pending(con, recheck: bool = False) -> list[dict[str, Any]]:
    """YouTube sources still to judge. A person's decision is never revisited,
    and neither is a video excluded by hand before this check existed."""
    where = ("s.scope_by IS NOT 'manual' AND NOT (s.in_scope = 0 AND "
             "s.scope_checked_at IS NULL)" if recheck else
             "s.scope_checked_at IS NULL AND s.in_scope = 1")
    rows = con.execute(
        "SELECT s.id, s.name, MIN(d.url) url, COUNT(d.id) n FROM sources s "
        "JOIN documents d ON d.source_id = s.id "
        f"WHERE s.platform = 'youtube' AND {where} GROUP BY s.id ORDER BY s.id"
    ).fetchall()
    out = []
    for r in rows:
        m = _VID.search(r["url"] or "")
        sample = con.execute(
            "SELECT raw_text FROM documents WHERE source_id = ? "
            "ORDER BY rating DESC, LENGTH(raw_text) DESC LIMIT ?",
            (r["id"], SAMPLE)).fetchall()
        out.append({"id": r["id"], "video_id": m.group(1) if m else None,
                    "title": r["name"].removeprefix("youtube:"), "comments": r["n"],
                    "sample": [" ".join(x["raw_text"].split())[:SAMPLE_CHARS]
                               for x in sample]})
    return out


def video_details(video_ids: list[str]) -> dict[str, dict[str, str]]:
    """Full title, channel, description and channel country from the Data API.

    About 1 quota unit per 50 videos. Without a key or network the check still
    runs on the stored title and comments, which carry most of the signal.
    """
    from . import collectors
    ids = [v for v in video_ids if v]
    if not ids:
        return {}
    yt = collectors.YouTubeCollector()
    out: dict[str, dict[str, str]] = {}
    try:
        for i in range(0, len(ids), 50):
            data = yt._get("videos", {"part": "snippet", "id": ",".join(ids[i:i + 50]),
                                      "maxResults": 50})
            for it in data.get("items", []):
                sn = it["snippet"]
                out[it["id"]] = {"title": sn.get("title", ""),
                                 "channel": sn.get("channelTitle", ""),
                                 "channel_id": sn.get("channelId", ""),
                                 "description": " ".join(
                                     (sn.get("description") or "").split())[:500],
                                 "language": sn.get("defaultAudioLanguage")
                                 or sn.get("defaultLanguage") or ""}
        chans = sorted({d["channel_id"] for d in out.values() if d["channel_id"]})
        country_of: dict[str, str] = {}
        for i in range(0, len(chans), 50):
            data = yt._get("channels", {"part": "snippet", "id": ",".join(chans[i:i + 50]),
                                        "maxResults": 50})
            for it in data.get("items", []):
                country_of[it["id"]] = it["snippet"].get("country", "")
        for d in out.values():
            d["channel_country"] = country_of.get(d.pop("channel_id"), "")
    except Exception as exc:
        log.warning("video details unavailable (%s); judging from titles and "
                    "comments only", exc)
    return out


# --------------------------------------------------------------------- run
def judge(videos: list[dict[str, Any]], country_name: str, currency: str) -> dict[int, dict]:
    """One model call for a batch. Returns verdicts by source id."""
    system = SYSTEM.format(country=country_name,
                           currency=currency or f"the {country_name} currency")
    payload = []
    for v in videos:
        item = {"id": v["id"], "title": v["title"]}
        item.update({k: v["details"][k] for k in ("title", "channel", "channel_country",
                                                  "language", "description")
                     if v.get("details", {}).get(k)})
        item["comment_count"] = v["comments"]
        item["sample_comments"] = v["sample"]
        payload.append(item)
    data = llm.complete_json(system, json.dumps(payload, ensure_ascii=False), SCHEMA,
                             model=llm.helper_model(), max_tokens=4000)
    wanted = {v["id"] for v in videos}
    return {r["id"]: r for r in data.get("videos", [])
            if r.get("id") in wanted and r.get("verdict") in VERDICTS}


def apply(con, country_name: str, keep_global: bool = False) -> None:
    """Set in_scope and the exclusion note from stored verdicts. No model call,
    so changing keep_global takes effect at once."""
    rows = con.execute("SELECT id, scope_verdict, scope_reason FROM sources "
                       "WHERE scope_verdict IS NOT NULL AND scope_by IS NOT 'manual'"
                       ).fetchall()
    for r in rows:
        market = r["scope_reason"].split(" | ", 1)[0] if r["scope_reason"] else ""
        kept = keep(r["scope_verdict"], keep_global)
        con.execute("UPDATE sources SET in_scope = ?, exclusion_note = ? WHERE id = ?",
                    (int(kept), None if kept else
                     exclusion_note(r["scope_verdict"], market, country_name), r["id"]))


def run(profile: dict[str, Any], recheck: bool = False) -> dict[str, int]:
    """Judge every unchecked YouTube video, then apply the keep rule."""
    name = profile.get("name", "Singapore")
    keep_global = bool(profile.get("youtube", {}).get("keep_global", False))
    with db.connect() as con:
        todo = pending(con, recheck)
    result = {"checked": 0, "kept": 0, "excluded": 0, "failed": 0}
    if todo:
        if not llm.api_key():
            raise RuntimeError(f"No API key for {llm.describe()}. Add it in Settings.")
        log.info("market check: %s YouTube video(s) for %s with %s / %s", len(todo),
                 name, llm.describe(), llm.helper_model())
        details = video_details([v["video_id"] for v in todo])
        for v in todo:
            v["details"] = details.get(v["video_id"], {})
        model = f"ai:{llm.helper_model()}"
        for start in range(0, len(todo), BATCH):
            batch = todo[start:start + BATCH]
            try:
                verdicts = judge(batch, name, profile.get("currency", ""))
            except Exception as exc:
                log.error("market check failed for %s video(s): %s", len(batch), exc)
                result["failed"] += len(batch)
                continue
            with db.connect() as con:
                for v in batch:
                    got = verdicts.get(v["id"])
                    if not got:
                        result["failed"] += 1
                        continue
                    # The market goes first so apply() can rebuild the note later.
                    reason = f"{got['market'].strip()} | {got['reason'].strip()}"
                    con.execute(
                        "UPDATE sources SET scope_verdict = ?, scope_reason = ?, "
                        "scope_by = ?, scope_checked_at = ? WHERE id = ?",
                        (got["verdict"], reason, model, db.now(), v["id"]))
                    result["checked"] += 1
                    kept = keep(got["verdict"], keep_global)
                    result["kept" if kept else "excluded"] += 1
                    log.info("%s  %-16s %s :: %s", "keep" if kept else "drop",
                             got["verdict"], v["title"][:50], got["reason"][:120])
        if result["failed"]:
            log.warning("market check: %s video(s) not judged; they stay in the "
                        "analysis and are retried on the next run", result["failed"])
    with db.connect() as con:
        apply(con, name, keep_global)
    return result


def summary(con) -> dict[str, Any]:
    """Counts for the workbook and the web app."""
    r = con.execute(
        "SELECT COUNT(*) videos, SUM(in_scope = 0) excluded, "
        "SUM(scope_checked_at IS NULL AND in_scope = 1) unchecked, "
        "GROUP_CONCAT(DISTINCT scope_by) models FROM sources WHERE platform = 'youtube'"
    ).fetchone()
    return {"videos": r["videos"] or 0, "excluded": r["excluded"] or 0,
            "unchecked": r["unchecked"] or 0,
            "models": sorted(m for m in (r["models"] or "").split(",") if m)}


def frame(con):
    """One row per YouTube video with its verdict, for the workbook and web app."""
    import pandas as pd
    return pd.read_sql_query("""
        SELECT s.id AS source_id, s.name AS video, s.in_scope, s.scope_verdict AS verdict,
               s.exclusion_note,
               CASE WHEN INSTR(s.scope_reason, ' | ') > 0
                    THEN SUBSTR(s.scope_reason, INSTR(s.scope_reason, ' | ') + 3)
                    ELSE s.scope_reason END AS reason,
               s.scope_by AS judged_by, s.scope_checked_at AS judged_at,
               COUNT(d.id) AS comments, MIN(d.url) AS url
        FROM sources s LEFT JOIN documents d ON d.source_id = s.id
        WHERE s.platform = 'youtube'
        GROUP BY s.id ORDER BY s.in_scope DESC, comments DESC
    """, con)
