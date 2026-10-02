"""AI-assisted setup: everything a new country or a pasted source needs
before collection can run unattended.

- detect_site():  a pasted page URL -> CSS selectors for its posts, verified by
                  extracting them with the same parser the collector uses.
- map_api():      a pasted JSON API endpoint -> dot paths for text/author/date.
- draft_country(): a country name -> a starter profile (languages, queries in
                  the local language, brand watchlist) for the user to edit.

Each makes one or two small calls; none runs per document. Every result is
checked mechanically before it is offered, because a selector or mapping that
looks plausible but extracts nothing would otherwise fail silently mid-run.
"""
from __future__ import annotations

import json
import logging
import os
from collections import Counter
from typing import Any
from urllib.parse import urlparse

from . import collectors, llm
from .country import CATEGORIES

log = logging.getLogger(__name__)


class DetectError(llm.UserError):
    """Raised with a message meant for the person using the web app."""


def problem(template: str, **kw: Any) -> dict[str, Any]:
    """A check failure, kept as template + values so it can be translated."""
    return {"template": template, "kw": kw}


def problem_text(p: dict[str, Any], translate=lambda s: s) -> str:
    return translate(p["template"]).format(**p["kw"])


_REPLY_LANG = {"zh": "Simplified Chinese (简体中文)"}


def _in_language(system: str, field: str, lang: str) -> str:
    if lang in _REPLY_LANG:
        return f"{system}\n\nWrite the `{field}` field in {_REPLY_LANG[lang]}."
    return system


def _ask(system: str, user: str, schema: dict, max_tokens: int = 16000) -> dict:
    return llm.complete_json(system, user, schema, model=llm.helper_model(),
                             max_tokens=max_tokens)


# ------------------------------------------------------------- page skeleton
_DROP = ("script, style, noscript, svg, iframe, link, meta, head, template, canvas, "
         "nav, select")
_KEEP_ATTRS = ("id", "class", "datetime", "itemprop", "role", "rel", "href",
               "aria-label")


def skeleton(html: str, max_chars: int = 60000, per_sig: int = 4) -> str:
    """Compact outline of a page: one element per line, repeated siblings collapsed.

    Raw forum HTML is mostly markup; the outline keeps what selector choice
    depends on (tags, classes, data-* attributes, a taste of each node's text)
    at a fraction of the tokens. Collapsing after `per_sig` identical siblings
    keeps a 40-post thread as small as a 4-post one.
    """
    from selectolax.parser import HTMLParser
    tree = HTMLParser(html)
    for n in tree.css(_DROP):
        n.decompose()
    root = tree.body or tree.root
    lines: list[str] = []
    size = 0

    def attrs(node) -> str:
        out = []
        for k, v in (node.attributes or {}).items():
            if k in _KEEP_ATTRS or k.startswith("data-"):
                v = "" if v is None else str(v)
                if k == "href":
                    v = v[:60]
                out.append(f'{k}="{v}"' if v else k)
        return (" " + " ".join(out)) if out else ""

    def walk(node, depth: int) -> None:
        nonlocal size
        seen: Counter = Counter()
        for child in node.iter():
            if size > max_chars:
                return
            if child.tag.startswith(("_", "-")):  # comments, doctype
                continue
            sig = (child.tag, (child.attributes or {}).get("class"))
            seen[sig] += 1
            if seen[sig] == per_sig + 1:
                line = f"{'  ' * depth}... more <{child.tag}> siblings like the above"
                lines.append(line)
                size += len(line)
            if seen[sig] > per_sig:
                continue
            own = " ".join((child.text(deep=False, strip=True) or "").split())[:80]
            line = f"{'  ' * depth}<{child.tag}{attrs(child)}>{(' ' + own) if own else ''}"
            lines.append(line)
            size += len(line)
            walk(child, depth + 1)

    walk(root, 0)
    return "\n".join(lines)


# ----------------------------------------------------------- selector detection
SELECTOR_SCHEMA = {
    "type": "object",
    "properties": {
        "page_kind": {"type": "string", "enum": ["posts", "not_posts"]},
        "post": {"type": "string"},
        "body": {"type": "string"},
        "author": {"type": "string"},
        "date": {"type": "string"},
        "strip": {"type": "string"},
        "next": {"type": "string"},
        "page_param": {"type": "string"},
        "reason": {"type": "string"},
    },
    "required": ["page_kind", "post", "body", "author", "date", "strip", "next",
                 "page_param", "reason"],
    "additionalProperties": False,
}

SELECTOR_SYSTEM = """You configure a polite research scraper that collects user-written \
posts (forum replies, product reviews, comments) from public web pages.

You receive an outline of one page's HTML: one element per line, indented by depth, \
showing tag, id, class, data-* and a few other attributes, then the first words of the \
element's own text. Runs of identical siblings are collapsed into "... more siblings".

Return CSS selectors:
- post: matches each individual post/review container, exactly one match per post.
  It must not match the whole thread or a wrapper around all posts.
- body: relative to post, the element holding the poster's own text.
- author: relative to post, the poster's display name ("" if absent).
- date: relative to post, prefer a <time> element ("" if absent).
- strip: comma-separated selectors inside a post to delete before reading the text:
  quoted replies, signatures, "click to expand", buttons, ads ("" if none).
- next: selects the <a> linking to the NEXT page of this same thread or review list
  ("" if none visible).
- page_param: if pagination evidently uses a query parameter such as ?page=2, its
  name; otherwise "".
- page_kind: "not_posts" if the page holds no user-written posts (a search or
  listing page, a login wall, or content rendered by JavaScript so the outline is
  empty). Then leave the selectors "".

Selectors are evaluated by a CSS3 engine: tag, .class, #id, [attr], [attr="v"],
descendant and child (>) combinators, :nth-child. Do NOT use :has(), :contains(),
XPath or jQuery extensions. Prefer stable semantic class names or data-* attributes
over auto-generated hashes such as .css-1x2y3z. Explain your choice in `reason`."""


def check_selectors(html: str, sel: dict[str, str]) -> tuple[list[dict], list[str]]:
    """Extract with the collector's own parser and list anything that looks wrong."""
    try:
        posts = collectors.parse_posts(html, sel)
    except Exception as exc:  # malformed selector
        return [], [problem("selector error: {error}", error=str(exc))]
    problems = []
    if len(posts) < 2:
        problems.append(problem("only {n} post(s) extracted; expected several",
                                n=len(posts)))
    bodies = [p["raw_text"] for p in posts]
    if posts and len(set(bodies)) < 0.6 * len(bodies):
        problems.append(problem("most extracted posts are identical: post selector "
                                "is too broad"))
    if posts and sum(len(b) for b in bodies) / len(bodies) < 15:
        problems.append(problem("extracted bodies are very short: body selector may "
                                "be wrong"))
    return posts, problems


def site_name(url: str) -> str:
    host = urlparse(url).netloc.lower()
    return host[4:] if host.startswith("www.") else host


def detect_site(url: str, known: dict[str, Any] | None = None,
                lang: str = "en") -> dict[str, Any]:
    """Fetch one pasted page and work out how to read its posts.

    `known` is the profile's saved entry for the same site; when its selectors
    still extract cleanly no model call is made. Returns name, selectors,
    page_param, a sample of extracted posts and any remaining problems.
    """
    if not url.lower().startswith(("http://", "https://")):
        raise DetectError("Paste a full address starting with http:// or https://.")
    if collectors.is_marketplace(url):
        raise DetectError(
            "Marketplace review pages (Shopee, Lazada, Amazon and similar) are excluded: "
            "collecting them means defeating bot detection, which breaches their terms. "
            "Record those reviews by hand and upload them as a CSV instead.")
    col = collectors.BaseCollector()
    try:
        html = col.fetch(url)
    except collectors.RobotsBlocked as exc:
        raise DetectError("This site does not permit automated collection ({error}). "
                          "It cannot be used.", error=str(exc))
    except Exception as exc:
        raise DetectError("Could not load the page: {error}", error=str(exc))

    if known and known.get("selectors"):
        posts, problems = check_selectors(html, known["selectors"])
        if not problems:
            return {"name": site_name(url), "selectors": known["selectors"],
                    "page_param": known.get("page_param") or "", "sample": posts[:5],
                    "problems": [], "reason": "reused this site's saved selectors"}

    outline = skeleton(html)
    if len(outline) < 200:
        raise DetectError("The page came back almost empty. It is probably built by "
                          "JavaScript in the browser, which this collector does not run. "
                          "Use the manual CSV upload for this site.")
    user = f"Page URL: {url}\n\nOutline:\n{outline}"
    system = _in_language(SELECTOR_SYSTEM, "reason", lang)
    ans = _ask(system, user, SELECTOR_SCHEMA)
    if ans["page_kind"] == "not_posts":
        raise DetectError("No user posts found on this page. {reason} Paste the "
                          "address of a single thread or review page.",
                          reason=ans["reason"])

    def as_sel(a: dict) -> dict[str, str]:
        return {k: a[k] for k in ("post", "body", "author", "date", "strip", "next")
                if a.get(k)}

    sel = as_sel(ans)
    posts, problems = check_selectors(html, sel)
    if problems:
        # One retry with the evidence. A second opinion that sees what the first
        # selectors actually extracted fixes most over- or under-broad choices.
        preview = json.dumps([p["raw_text"][:150] for p in posts[:3]], ensure_ascii=False)
        found = "; ".join(problem_text(p) for p in problems)
        retry = (f"{user}\n\nYour previous selectors were {json.dumps(sel)}.\n"
                 f"Testing them found: {found}.\n"
                 f"First extracted bodies: {preview}\nReturn corrected selectors.")
        ans = _ask(system, retry, SELECTOR_SCHEMA)
        sel = as_sel(ans)
        posts, problems = check_selectors(html, sel)
    return {"name": site_name(url), "selectors": sel, "page_param": ans["page_param"],
            "sample": posts[:5], "problems": problems, "reason": ans["reason"]}


# ---------------------------------------------------------------- api mapping
MAPPING_SCHEMA = {
    "type": "object",
    "properties": {k: {"type": "string"} for k in
                   ("items", "text", "title", "author", "date", "url", "rating", "reason")},
    "required": ["items", "text", "title", "author", "date", "url", "rating", "reason"],
    "additionalProperties": False,
}

MAPPING_SYSTEM = """You map a JSON API response to the fields of a review/comment \
collector. Return dot paths ("data.reviews", "snippet.text"; list indices as numbers):
- items: path from the response root to the LIST of reviews/comments/posts ("" if the
  root itself is that list).
- text: path inside one item to the user-written text. Required.
- title, author, date, url, rating: paths inside one item, or "" if absent.
Pick the field with the consumer's own words for text, not a product description.
Explain briefly in reason."""


def _shrink(obj: Any, depth: int = 0) -> Any:
    """First few elements of every list, long strings cut: enough to see shape."""
    if depth > 8:
        return "..."
    if isinstance(obj, list):
        return [_shrink(x, depth + 1) for x in obj[:3]] + (
            [f"... {len(obj) - 3} more"] if len(obj) > 3 else [])
    if isinstance(obj, dict):
        return {k: _shrink(v, depth + 1) for k, v in list(obj.items())[:40]}
    if isinstance(obj, str) and len(obj) > 200:
        return obj[:200] + "..."
    return obj


def map_api(endpoint: str, queries: list, auth: dict | None = None,
            key: str | None = None, lang: str = "en") -> dict[str, Any]:
    """Call the pasted endpoint once and work out where the reviews are."""
    col = collectors.CustomAPICollector()
    url = col.build_urls(endpoint, queries[:1] or ["water dispenser"])[0]
    try:
        data = col.get_json(url, auth, key)
    except collectors.RobotsBlocked as exc:
        raise DetectError("The API refused the request ({error}). Check the key and "
                          "where it goes (header or query parameter).", error=str(exc))
    except Exception as exc:
        raise DetectError("Could not call the API or it did not return JSON: {error}",
                          error=str(exc))
    # The key is never sent to the model: only the response body is.
    sample = json.dumps(_shrink(data), ensure_ascii=False)[:40000]
    ans = _ask(_in_language(MAPPING_SYSTEM, "reason", lang), f"Response:\n{sample}",
               MAPPING_SCHEMA, max_tokens=4000)
    mapping = {k: ans[k] for k in ("items", "text", "title", "author", "date", "url",
                                   "rating")}
    items = col.items(data, mapping)
    problems = [] if items else [problem("no items with text were found using this "
                                         "mapping")]
    return {"mapping": mapping, "sample": items[:5], "problems": problems,
            "reason": ans["reason"]}


# ---------------------------------------------------------------- country draft
DRAFT_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {"type": "string"},
        "name": {"type": "string"},
        "languages": {"type": "array", "items": {"type": "string"}},
        "currency": {"type": "string"},
        "youtube_region": {"type": "string"},
        "queries": {"type": "array", "items": {
            "type": "object",
            "properties": {"text": {"type": "string"}, "lang": {"type": "string"}},
            "required": ["text", "lang"], "additionalProperties": False}},
        "products": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "brand": {"type": "string"},
                "category": {"type": "string", "enum": CATEGORIES},
                "models": {"type": "array", "items": {"type": "string"}},
                "aliases": {"type": "array", "items": {"type": "string"}}},
            "required": ["brand", "category", "models", "aliases"],
            "additionalProperties": False}},
        "category_terms": {
            "type": "object",
            "properties": {c: {"type": "array", "items": {"type": "string"}}
                           for c in CATEGORIES},
            "required": CATEGORIES, "additionalProperties": False},
        "subreddits": {"type": "array", "items": {"type": "string"}},
        "suggested_sites": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "string"},
    },
    "required": ["code", "name", "languages", "currency", "youtube_region", "queries",
                 "products", "category_terms", "subreddits", "suggested_sites", "notes"],
    "additionalProperties": False,
}

DRAFT_SYSTEM = """You prepare market-research profiles for a consumer sentiment study \
of countertop water dispensers, water purifiers and water filter jugs, run for a \
water-appliance brand. The study collects public consumer discussion (YouTube \
comments, forums, review sites) in one country and codes what people care about.

For the requested country return:
- code: ISO 3166-1 alpha-2, lowercase. youtube_region: the same, uppercase.
- languages: ISO 639-1 codes of the languages consumers there actually use online
  when discussing home appliances, most used first.
- currency: ISO 4217 code.
- queries: 15-25 search queries, each tagged with its language. Mix: the category in
  general ("water dispenser" in the local phrasing), the leading brands + category,
  known pain points (mould in the tank, filter replacement cost, plastic taste,
  safety of tap water there, hot water for baby formula), and jug filters as the
  cheaper substitute. Write each query the way a local consumer would type it;
  include English queries only where English is genuinely used there.
- products: 10-20 brands actually sold in that country across the three categories
  (category is one of countertop_dispenser, purifier, jug_filter). Always include
  at least two jug_filter brands. `brand` is the common Latin-script name
  so brands compare across countries; put local-script names and other spellings in
  `aliases`. List `models` only when you are confident the model name is real and
  sold there; otherwise leave the list empty, the user will fill it in.
- category_terms: local-language words for each category, used to tag posts that name
  no brand. Empty lists for English.
- subreddits: subreddit names (without r/) where people from that country discuss
  home purchases, only ones you are confident exist; may be empty.
- suggested_sites: names of local forums, blogs or review sites where such
  discussion happens publicly, for the user to look up and paste threads from.
  Names, not URLs. Exclude marketplaces (Shopee, Lazada, Amazon and the like),
  which the study does not collect from, and anything behind a login such as
  Facebook groups.
- notes: two or three sentences on what the user should check, e.g. brands you were
  unsure of or market facts that may be out of date.

This is a draft that a researcher will edit before any collection runs. Prefer
leaving something out over guessing."""

_EXAMPLE = {
    "code": "sg", "languages": ["en"],
    "queries": [{"text": "water dispenser Singapore", "lang": "en"},
                {"text": "water dispenser mould tank", "lang": "en"},
                {"text": "Brita jug Singapore", "lang": "en"}],
    "products": [{"brand": "novita", "category": "countertop_dispenser",
                  "models": ["W29", "HydroCube"], "aliases": []},
                 {"brand": "Brita", "category": "jug_filter",
                  "models": ["Marella"], "aliases": []}],
}


def draft_country(name: str, lang: str = "en") -> dict[str, Any]:
    user = (f"Country: {name}\n\nFor reference, part of the Singapore profile:\n"
            f"{json.dumps(_EXAMPLE, ensure_ascii=False)}")
    d = _ask(_in_language(DRAFT_SYSTEM, "notes", lang), user, DRAFT_SCHEMA)
    code = (d["code"] or "").strip().lower()
    suggested = ", ".join(d["suggested_sites"])
    notes = d["notes"].strip()
    if suggested:
        notes += (f"\n值得粘贴帖子的网站：{suggested}。" if lang == "zh"
                  else f"\nSites worth pasting threads from: {suggested}.")
    return {
        "code": code,
        "name": d["name"] or name,
        "languages": [l.lower() for l in d["languages"]] or ["en"],
        "currency": d["currency"].upper(),
        "youtube": {"enabled": True, "region": (d["youtube_region"] or code).upper(),
                    "max_videos": 8, "max_comments": 100},
        "queries": d["queries"],
        "products": d["products"],
        "category_terms": d["category_terms"],
        "reddit": {"enabled": False, "subreddits": d["subreddits"],
                   "limit_per_query": 25, "comments": True},
        "websites": [],
        "custom_apis": [],
        "notes": (f"由 AI 起草；采集前请检查品牌和搜索词。\n{notes}"
                  if lang == "zh" else
                  f"AI-drafted; review brands and queries before collecting.\n{notes}"),
    }
