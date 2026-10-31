"""WATERS + sentiment classification.

Emits one MENTION per need discussed, each with its own sentiment and a verbatim
evidence span. A document may produce zero mentions (off-topic) or several.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

import yaml

from . import db, llm

log = logging.getLogger(__name__)

BATCH = int(os.getenv("CLASSIFY_BATCH", "20"))
VALID_CODES = {"W", "A", "T", "E", "R", "S", "COST", "OTHER"}
VALID_SENTIMENT = {"positive", "negative", "neutral", "mixed"}


def load_taxonomy(path: str = "config/waters.yaml") -> dict[str, Any]:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


def system_prompt(tax: dict[str, Any], country_name: str = "Singapore") -> str:
    lines = [
        "You are a market research coder classifying consumer reviews and forum posts",
        f"about water dispensers, water purifiers and jug filters in {country_name}.",
        "",
        "Assign every NEED the text discusses to one of these codes:",
        "",
    ]
    for code, spec in tax["codes"].items():
        kind = spec.get("kind", "")
        kind_note = f" [{kind}]" if kind else ""
        lines.append(f"{code} ({spec['name']}){kind_note}: {spec['definition']}")
        lines.append(f"   Signals: {', '.join(spec['signals'])}")
    lines += [
        "",
        "RULES",
        "1. A document may produce ZERO mentions. Off-topic, spam, or a bare rating",
        "   with no substantive text produces an empty mentions array.",
        "2. A document may produce MULTIPLE mentions, one per distinct need discussed.",
        "3. Each mention carries its own sentiment. Positive and negative mentions can",
        "   coexist in one document.",
        "4. evidence_span MUST be an exact verbatim substring of the source text. If no",
        "   verbatim span supports the code, do not emit the mention.",
        "5. intensity: 1 passing mention, 2 clear opinion, 3 strong or repeated emphasis.",
        "6. confidence 0.0 to 1.0. Below 0.6 flags the mention for human review.",
        "7. Do NOT infer needs the text does not state. Absence of evidence is not a",
        "   mention. Use OTHER rather than forcing a poor fit.",
        "8. COST is a constraint, not a need, but code it so it can be counted.",
        "9. Texts may be in any language or mix languages. Code the meaning, not the",
        "   language. evidence_span stays verbatim in the ORIGINAL language and script;",
        "   never translate it. evidence_span_en is a faithful English translation of",
        "   that span, identical to evidence_span when the span is already English.",
        "10. language: ISO 639-1 code of the document's main language (en, zh, ms, th...).",
        "",
        "OUTPUT",
        "Return ONLY valid JSON matching this shape. No prose, no markdown fences.",
        '{"results":[{"doc_id":123,"language":"en","mentions":[{"waters_code":"R",'
        '"sentiment":"negative","intensity":3,'
        '"evidence_span":"tank started growing mould",'
        '"evidence_span_en":"tank started growing mould","confidence":0.94}]}]}',
    ]
    return "\n".join(lines)


_extract_json = llm.extract_json


def _valid(mention: dict, source_text: str) -> bool:
    if mention.get("waters_code") not in VALID_CODES:
        return False
    if mention.get("sentiment") not in VALID_SENTIMENT:
        return False
    span = (mention.get("evidence_span") or "").strip()
    if not span:
        return False
    # Rule 4: the span must actually appear in the source (whitespace-normalised).
    norm = " ".join(source_text.lower().split())
    return " ".join(span.lower().split()) in norm


# Rule 4 requires verbatim evidence spans, and forum posts are full of double
# quotes. Asked for free-form JSON, the model copies a span like
#   some new filters are "dirty"
# straight into a string without escaping the inner quotes, and the response is
# unparseable. Constraining the response to this schema makes the API escape
# string contents correctly, so a quoted span can no longer corrupt the batch.
RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "doc_id": {"type": "integer"},
                    "language": {"type": "string"},
                    "mentions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "waters_code": {"type": "string",
                                                "enum": sorted(VALID_CODES)},
                                "sentiment": {"type": "string",
                                              "enum": sorted(VALID_SENTIMENT)},
                                "intensity": {"type": "integer"},
                                "evidence_span": {"type": "string"},
                                "evidence_span_en": {"type": "string"},
                                "confidence": {"type": "number"},
                            },
                            "required": ["waters_code", "sentiment", "intensity",
                                         "evidence_span", "evidence_span_en",
                                         "confidence"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["doc_id", "language", "mentions"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def classify_batch(docs: list[tuple[int, str]], tax: dict,
                   country_name: str = "Singapore",
                   languages: dict[int, str] | None = None) -> dict[int, list[dict]]:
    """Returns mentions per doc. Fills `languages` with each doc's detected language."""
    payload = [{"doc_id": i, "text": t[:4000]} for i, t in docs]
    data = llm.complete_json(system_prompt(tax, country_name),
                             json.dumps(payload, ensure_ascii=False), RESULT_SCHEMA,
                             model=llm.classify_model(), max_tokens=8000)
    by_doc = {i: t for i, t in docs}
    out: dict[int, list[dict]] = {i: [] for i, _ in docs}
    for res in data.get("results", []):
        did = res.get("doc_id")
        if did not in by_doc:
            continue
        if languages is not None and res.get("language"):
            languages[did] = res["language"]
        kept = []
        for m in res.get("mentions", []):
            if _valid(m, by_doc[did]):
                kept.append(m)
            else:
                log.debug("dropped invalid mention on doc %s: %s", did, m)
        out[did] = kept
    return out


def run(limit: int | None = None, taxonomy_path: str = "config/waters.yaml",
        dry_run: bool = False, country_name: str = "Singapore") -> dict[str, int]:
    tax = load_taxonomy(taxonomy_path)
    with db.connect() as con:
        pending = db.unclassified(con, limit)
    if not pending:
        return {"documents": 0, "mentions": 0}
    if dry_run:
        return {"documents": len(pending), "mentions": 0, "dry_run": 1}

    if not llm.api_key():
        raise RuntimeError(f"No API key for {llm.describe()}. Add it in Settings.")
    model = llm.classify_model()
    log.info("classifying with %s / %s", llm.describe(), model)

    total_m = 0
    failures = 0
    for start in range(0, len(pending), BATCH):
        chunk = [(r["id"], r["raw_text"]) for r in pending[start:start + BATCH]]
        langs: dict[int, str] = {}
        try:
            result = classify_batch(chunk, tax, country_name, langs)
        except llm.LLMStop:
            raise  # every later batch would fail the same way
        except Exception as exc:
            failures += len(chunk)
            log.error("batch failed at offset %s: %s", start, exc)
            continue
        with db.connect() as con:
            for did, mentions in result.items():
                n = db.insert_mentions(con, did, mentions, model) if mentions else 0
                db.mark_classified(con, did, model, n)
                db.set_language(con, did, langs.get(did))
                total_m += n
        log.info("classified %s/%s documents", min(start + BATCH, len(pending)), len(pending))
    return {"documents": len(pending) - failures, "mentions": total_m,
            "failed": failures}
