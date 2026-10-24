"""Collectors for the easy and moderate source tiers.

Easy tier (official APIs): Reddit, YouTube, and any custom JSON API whose key
the user supplies.
Moderate tier (static HTML): forums, review sites.

Every collector inherits BaseCollector, which enforces robots.txt, a per-domain rate
limit, an identifying User-Agent and on-disk caching. Marketplace collectors are
deliberately NOT included: Shopee and Lazada require defeating bot detection, which
breaches their terms. Use `manual_import` plus the hand-recorded shelf audit instead.
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import re
import time
import urllib.robotparser as robotparser
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import (parse_qsl, quote_plus, urlencode, urljoin,
                          urlparse, urlunparse)

log = logging.getLogger(__name__)

CACHE_DIR = Path(os.getenv("WATERS_CACHE", "data/raw"))


def user_agent() -> str:
    """Identifies the crawler honestly: who runs it and how to reach them.

    Read on every request so a change saved in the web app applies at once.
    """
    org = re.sub(r"[^A-Za-z0-9._-]+", "-", os.getenv("ORG_NAME", "").strip()) or "Unnamed"
    contact = os.getenv("RESEARCH_CONTACT", "").strip() or "no contact given"
    return f"{org}-ConsumerResearch/1.0 (consumer sentiment research; contact {contact})"

DEFAULT_DELAY = float(os.getenv("REQUEST_DELAY", "2.0"))


# --------------------------------------------------------------------------- base
class RobotsBlocked(RuntimeError):
    """Raised when robots.txt disallows a URL. Never caught and retried."""


# Marketplace review pages sit behind bot detection; collecting them at scale
# means defeating it, which breaches their terms. Refused for every country.
MARKETPLACE_HOSTS = (
    "shopee.", "lazada.", "amazon.", "tokopedia.", "bukalapak.", "tiki.vn",
    "taobao.", "tmall.", "jd.com", "aliexpress.", "alibaba.", "rakuten.",
    "ebay.", "qoo10.", "coupang.", "flipkart.", "noon.com", "temu.",
    "blibli.", "zalora.", "carousell.", "pinduoduo.", "yangkeduo.", "suning.",
    "vip.com", "dangdang.",
)


def is_marketplace(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return any(m in host for m in MARKETPLACE_HOSTS)


class BaseCollector:
    platform = "base"

    def __init__(self, delay: float = DEFAULT_DELAY, use_cache: bool = True):
        self.delay = delay
        self.use_cache = use_cache
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, robotparser.RobotFileParser] = {}
        CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # -- politeness ---------------------------------------------------------
    def robots_ok(self, url: str) -> bool:
        host = urlparse(url).netloc
        if host not in self._robots:
            rp = robotparser.RobotFileParser()
            rp.set_url(f"{urlparse(url).scheme}://{host}/robots.txt")
            try:
                rp.read()
            except Exception as exc:  # unreachable robots.txt: assume allowed, log it
                log.warning("robots.txt unreadable for %s (%s), proceeding", host, exc)
                rp = None
            self._robots[host] = rp
        rp = self._robots[host]
        return True if rp is None else rp.can_fetch(user_agent(), url)

    def crawl_delay(self, url: str) -> float:
        """The site's own Crawl-delay when it is stricter than ours.

        robots.txt is authoritative on pace, not just on paths. Falls back to
        the configured delay when robots is absent, unreadable or silent.
        """
        rp = self._robots.get(urlparse(url).netloc)
        if rp is None:
            return self.delay
        try:
            declared = rp.crawl_delay(user_agent())
        except Exception:
            return self.delay
        return max(self.delay, float(declared)) if declared else self.delay

    def throttle(self, url: str) -> None:
        host = urlparse(url).netloc
        delay = self.crawl_delay(url)
        elapsed = time.time() - self._last_hit.get(host, 0.0)
        if elapsed < delay:
            time.sleep(delay - elapsed)
        self._last_hit[host] = time.time()

    # -- caching ------------------------------------------------------------
    def _cache_path(self, key: str) -> Path:
        return CACHE_DIR / f"{self.platform}_{hashlib.sha256(key.encode()).hexdigest()[:24]}.txt"

    def fetch(self, url: str) -> str:
        """GET with robots check, rate limit, cache and retry."""
        cp = self._cache_path(url)
        if self.use_cache and cp.exists():
            return cp.read_text(encoding="utf-8", errors="replace")
        if not self.robots_ok(url):
            raise RobotsBlocked(f"robots.txt disallows {url}")
        import httpx
        self.throttle(url)
        last_exc = None
        for attempt in range(3):
            try:
                r = httpx.get(url, headers={"User-Agent": user_agent()},
                              timeout=30, follow_redirects=True)
                if r.status_code in (403, 429):
                    log.warning("blocked (%s) on %s, stopping this source",
                                r.status_code, url)
                    raise RobotsBlocked(f"HTTP {r.status_code} on {url}")
                r.raise_for_status()
                cp.write_text(r.text, encoding="utf-8")
                return r.text
            except RobotsBlocked:
                raise
            except Exception as exc:
                last_exc = exc
                time.sleep(2 ** attempt)
        raise RuntimeError(f"fetch failed for {url}: {last_exc}")

    def collect(self, *a, **kw) -> Iterator[dict[str, Any]]:
        raise NotImplementedError


# ------------------------------------------------------------------------- reddit
class RedditCollector(BaseCollector):
    """Official API via PRAW. Requires REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET."""
    platform = "reddit"

    def _client(self):
        import praw
        cid = os.getenv("REDDIT_CLIENT_ID")
        sec = os.getenv("REDDIT_CLIENT_SECRET")
        if not cid or not sec:
            raise RuntimeError(
                "REDDIT_CLIENT_ID / REDDIT_CLIENT_SECRET missing. Register a 'script' "
                "app at https://www.reddit.com/prefs/apps and put the values in .env"
            )
        return praw.Reddit(client_id=cid, client_secret=sec, user_agent=user_agent(),
                           check_for_async=False)

    def collect(self, subreddits: list[str], queries: list[str],
                limit_per_query: int = 50, comments: bool = True
                ) -> Iterator[dict[str, Any]]:
        reddit = self._client()
        for sub in subreddits:
            sr = reddit.subreddit(sub)
            for q in queries:
                log.info("reddit r/%s :: %s", sub, q)
                try:
                    results = sr.search(q, limit=limit_per_query, sort="relevance")
                except Exception as exc:
                    log.error("reddit search failed r/%s %s: %s", sub, q, exc)
                    continue
                for post in results:
                    body = f"{post.title}\n\n{post.selftext or ''}".strip()
                    yield {
                        "raw_text": body,
                        "url": f"https://reddit.com{post.permalink}",
                        "author": str(post.author) if post.author else None,
                        "posted_at": datetime.fromtimestamp(
                            post.created_utc, tz=timezone.utc).isoformat(timespec="seconds"),
                        "rating": float(post.score),
                        "source_name": f"r/{sub}",
                    }
                    if not comments:
                        continue
                    try:
                        post.comments.replace_more(limit=0)
                        for c in post.comments.list():
                            if not getattr(c, "body", "").strip():
                                continue
                            yield {
                                "raw_text": c.body,
                                "url": f"https://reddit.com{post.permalink}",
                                "author": str(c.author) if c.author else None,
                                "posted_at": datetime.fromtimestamp(
                                    c.created_utc, tz=timezone.utc).isoformat(timespec="seconds"),
                                "rating": float(getattr(c, "score", 0)),
                                "source_name": f"r/{sub}",
                            }
                    except Exception as exc:
                        log.warning("comment fetch failed: %s", exc)
                    time.sleep(self.delay)


# ------------------------------------------------------------------------ youtube
class YouTubeStop(RobotsBlocked):
    """The key cannot be used any more this run: quota, invalid key, API disabled."""


class VideoSkipped(RuntimeError):
    """One video's comments cannot be read (turned off, removed, private)."""


# YouTube answers 403 both for "comments are turned off on this video" and for
# "the quota is used up", so the status code alone cannot tell a skip from a
# stop. The reason inside the error body can.
_YT_STOP = {
    "quotaExceeded": "YouTube daily quota used up. It resets at midnight US Pacific "
                     "time; comments collected so far are kept.",
    "dailyLimitExceeded": "YouTube daily quota used up. It resets at midnight US "
                          "Pacific time; comments collected so far are kept.",
    "rateLimitExceeded": "YouTube is limiting how fast this key may call it. Try again "
                         "later; comments collected so far are kept.",
    "userRateLimitExceeded": "YouTube is limiting how fast this key may call it. Try "
                             "again later; comments collected so far are kept.",
    "keyInvalid": "YouTube rejected the API key. Check it in Settings.",
    "keyExpired": "The YouTube API key has expired. Create a new one in Google Cloud "
                  "Console.",
    "accessNotConfigured": "YouTube Data API v3 is not enabled for this key's Google "
                           "Cloud project. Enable it in Google Cloud Console.",
    "ipRefererBlocked": "The YouTube API key is restricted to other IP addresses or "
                        "websites. Loosen the restriction in Google Cloud Console.",
}
_YT_SKIP = {
    "commentsDisabled": "comments are turned off",
    "videoNotFound": "video removed or private",
    "forbidden": "comments not accessible",
}


def youtube_error(r, path: str) -> None:
    """Raise YouTubeStop or VideoSkipped for an error response from the Data API."""
    try:
        err = r.json().get("error", {})
    except ValueError:
        err = {}
    reasons = [e.get("reason", "") for e in err.get("errors") or []]
    reasons += [d.get("reason", "") for d in err.get("details") or []]
    message = err.get("message", "") or r.text[:200]
    if "API_KEY_INVALID" in reasons or "API key not valid" in message:
        reasons.insert(0, "keyInvalid")
    for reason in reasons:
        if reason in _YT_STOP:
            raise YouTubeStop(_YT_STOP[reason])
    if path == "commentThreads" and r.status_code in (403, 404):
        reason = next((x for x in reasons if x in _YT_SKIP), "")
        raise VideoSkipped(_YT_SKIP.get(reason) or f"HTTP {r.status_code} {message}")
    if r.status_code in (400, 401, 403):
        raise YouTubeStop(f"YouTube refused the request (HTTP {r.status_code}: "
                          f"{', '.join(filter(None, reasons)) or message}).")
    r.raise_for_status()


class YouTubeCollector(BaseCollector):
    """Official Data API v3. Requires YOUTUBE_API_KEY."""
    platform = "youtube"
    BASE = "https://www.googleapis.com/youtube/v3"

    def _key(self) -> str:
        k = os.getenv("YOUTUBE_API_KEY")
        if not k:
            raise RuntimeError(
                "YOUTUBE_API_KEY missing. Create one in Google Cloud Console, enable "
                "'YouTube Data API v3', and put it in .env"
            )
        return k

    def _get(self, path: str, params: dict) -> dict:
        import httpx
        params = {**params, "key": self._key()}
        self.throttle(self.BASE)
        r = httpx.get(f"{self.BASE}/{path}", params=params,
                      headers={"User-Agent": user_agent()}, timeout=30)
        if r.status_code >= 400:
            youtube_error(r, path)
        return r.json()

    # YouTube wants script variants for Chinese rather than bare "zh".
    LANG_ALIASES = {"zh": "zh-Hans", "zh-cn": "zh-Hans", "zh-tw": "zh-Hant",
                    "zh-hk": "zh-Hant"}

    def collect(self, queries: list, max_videos: int = 10,
                max_comments: int = 100, region: str = "SG",
                language: str | None = "en") -> Iterator[dict[str, Any]]:
        """queries: plain strings, or (text, language) pairs from a profile.

        A video whose comments cannot be read is skipped; a problem with the key
        or quota raises YouTubeStop, because every later call would fail too.
        """
        skipped: dict[str, str] = {}
        for entry in queries:
            q, lang = entry if isinstance(entry, (tuple, list)) else (entry, language)
            log.info("youtube [%s/%s] :: %s", region, lang or "-", q)
            params = {"part": "snippet", "q": q, "type": "video",
                      "maxResults": min(max_videos, 50), "regionCode": region}
            if lang:
                params["relevanceLanguage"] = self.LANG_ALIASES.get(lang.lower(), lang)
            try:
                search = self._get("search", params)
            except YouTubeStop:
                raise
            except Exception as exc:
                log.error("youtube search failed %s: %s", q, exc)
                continue
            for item in search.get("items", []):
                vid = item["id"]["videoId"]
                title = item["snippet"]["title"]
                if vid in skipped:
                    continue
                page, fetched = None, 0
                while fetched < max_comments:
                    params = {"part": "snippet,replies", "videoId": vid,
                              "maxResults": 100, "textFormat": "plainText",
                              "order": "relevance"}
                    if page:
                        params["pageToken"] = page
                    try:
                        data = self._get("commentThreads", params)
                    except VideoSkipped as exc:
                        skipped[vid] = str(exc)
                        log.info("skipped video %s (%s): %s", vid, title[:60], exc)
                        break
                    except YouTubeStop:
                        raise
                    except Exception as exc:
                        log.warning("comments unavailable for %s: %s", vid, exc)
                        break
                    for th in data.get("items", []):
                        top = th["snippet"]["topLevelComment"]["snippet"]
                        yield {
                            "raw_text": top.get("textDisplay", ""),
                            "url": f"https://youtube.com/watch?v={vid}",
                            "author": top.get("authorDisplayName"),
                            "posted_at": top.get("publishedAt"),
                            "rating": float(top.get("likeCount", 0)),
                            "source_name": f"youtube:{title[:60]}",
                        }
                        fetched += 1
                        for rep in th.get("replies", {}).get("comments", []):
                            rs = rep["snippet"]
                            yield {
                                "raw_text": rs.get("textDisplay", ""),
                                "url": f"https://youtube.com/watch?v={vid}",
                                "author": rs.get("authorDisplayName"),
                                "posted_at": rs.get("publishedAt"),
                                "rating": float(rs.get("likeCount", 0)),
                                "source_name": f"youtube:{title[:60]}",
                            }
                            fetched += 1
                    page = data.get("nextPageToken")
                    if not page:
                        break
        if skipped:
            from collections import Counter
            log.info("youtube: %s video(s) skipped: %s", len(skipped), ", ".join(
                f"{n} {why}" for why, n in Counter(skipped.values()).most_common()))


# --------------------------------------------------------------------------- forum
def parse_posts(html: str, selectors: dict[str, str]) -> list[dict[str, Any]]:
    """Extract posts from one page with CSS selectors.

    Shared by the collector and by selector auto-detection, so a detected
    configuration is previewed with exactly the code that will collect with it.
    """
    from selectolax.lexbor import LexborHTMLParser as HTMLParser
    tree = HTMLParser(html)
    strip_sel = selectors.get("strip")
    out = []
    for node in tree.css(selectors["post"]):
        # Quoted text belongs to the person being quoted, not to this poster.
        # Left in, it reattributes their words and counts the same opinion once
        # per quote, biasing volume toward whatever gets quoted most.
        if strip_sel:
            for junk in node.css(strip_sel):
                junk.decompose()

        def pick(key: str) -> str | None:
            sel = selectors.get(key)
            if not sel:
                return None
            el = node.css_first(sel)
            if el is None:
                return None
            # <time datetime> is exact and machine-readable; the visible text is
            # often "Yesterday at 3:42 PM", which parses to NaT and drops the
            # row from the time series.
            if key == "date" and el.attributes.get("datetime"):
                return el.attributes["datetime"]
            return el.text(strip=True)

        text = pick("body")
        if not text:
            continue
        out.append({"raw_text": text, "author": pick("author"),
                    "posted_at": pick("date")})
    return out


def next_page_url(html: str, next_selector: str, current: str) -> str | None:
    from selectolax.lexbor import LexborHTMLParser as HTMLParser
    el = HTMLParser(html).css_first(next_selector)
    href = el.attributes.get("href") if el is not None else None
    return urljoin(current, href) if href else None


def with_param(url: str, key: str, value: Any) -> str:
    """Set one query parameter, keeping any the URL already carries."""
    parts = urlparse(url)
    q = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k != key]
    q.append((key, str(value)))
    return urlunparse(parts._replace(query=urlencode(q)))


class ForumCollector(BaseCollector):
    """Generic static-HTML collector driven by CSS selectors in the profile.

    Selectors come from auto-detection (`src.autodetect`) or by hand with
    `python -m src.cli inspect <url>`. Pagination follows a `next` link selector
    when one is configured, else increments `page_param`, else stops at the
    pasted page.
    """
    platform = "forum"

    def collect(self, name: str, thread_urls: list[str], selectors: dict[str, str],
                max_pages: int = 5, page_param: str | None = "page"
                ) -> Iterator[dict[str, Any]]:
        for base_url in thread_urls:
            if is_marketplace(base_url):
                log.warning("skipping marketplace URL %s (use the manual CSV)", base_url)
                continue
            prev_sig = None
            url = base_url
            for page in range(1, max_pages + 1):
                try:
                    html = self.fetch(url)
                except RobotsBlocked as exc:
                    log.warning("stopping %s: %s", name, exc)
                    return
                except Exception as exc:
                    log.error("fetch error %s: %s", url, exc)
                    break
                posts = parse_posts(html, selectors)
                if not posts:
                    break
                # Many forums clamp an out-of-range page to the last one rather
                # than serving an empty page, so "no posts" never arrives and we
                # would re-fetch the tail max_pages times. Stop when a page
                # repeats the one before it.
                sig = hashlib.sha256("".join(p["raw_text"] for p in posts
                                             ).encode("utf-8")).hexdigest()
                if sig == prev_sig:
                    log.info("%s: page %s repeats page %s, thread exhausted",
                             name, page, page - 1)
                    break
                prev_sig = sig
                for p in posts:
                    yield {**p, "url": url, "rating": None, "source_name": name}
                if selectors.get("next"):
                    url = next_page_url(html, selectors["next"], url)
                    if not url:
                        break
                elif page_param:
                    url = with_param(base_url, page_param, page + 1)
                else:
                    break


# ---------------------------------------------------------------------- custom api
def dig(obj: Any, path: str | None) -> Any:
    """Follow a dot path such as "data.reviews" or "items.0.text". "" is obj."""
    if not path:
        return obj
    for part in path.split("."):
        if isinstance(obj, list) and part.isdigit():
            idx = int(part)
            obj = obj[idx] if idx < len(obj) else None
        elif isinstance(obj, dict):
            obj = obj.get(part)
        else:
            return None
        if obj is None:
            return None
    return obj


class CustomAPICollector(BaseCollector):
    """Any JSON API the user holds a key for, mapped by dot paths.

    `endpoint` may contain {query}; it is then called once per profile query.
    The key is read from an environment variable, never from the profile, so a
    profile can be shared without leaking credentials. robots.txt governs
    crawlers, not keyed APIs, so it is not consulted, but the rate limit and the
    halt-on-403/429 rule still apply. Only the first page of each response is
    read.
    """
    platform = "api"

    def get_json(self, url: str, auth: dict | None = None, key: str | None = None):
        import httpx
        headers = {"User-Agent": user_agent(), "Accept": "application/json"}
        if key and auth and auth.get("type") == "header":
            prefix = auth.get("prefix") or ""
            headers[auth.get("name") or "Authorization"] = f"{prefix}{key}"
        elif key and auth and auth.get("type") == "query":
            url = with_param(url, auth.get("name") or "key", key)
        self.throttle(url)
        r = httpx.get(url, headers=headers, timeout=30, follow_redirects=True)
        if r.status_code in (401, 403, 429):
            raise RobotsBlocked(f"HTTP {r.status_code} from API")
        r.raise_for_status()
        return r.json()

    @staticmethod
    def build_urls(endpoint: str, queries: list) -> list[str]:
        if "{query}" not in endpoint:
            return [endpoint]
        texts = [q[0] if isinstance(q, (tuple, list)) else q for q in queries]
        return [endpoint.replace("{query}", quote_plus(t)) for t in texts]

    @staticmethod
    def items(data: Any, mapping: dict[str, str]) -> list[dict[str, Any]]:
        rows = dig(data, mapping.get("items"))
        if not isinstance(rows, list):
            return []
        out = []
        for it in rows:
            text = dig(it, mapping.get("text"))
            if not isinstance(text, str) or not text.strip():
                continue
            title = dig(it, mapping.get("title")) if mapping.get("title") else None
            if isinstance(title, str) and title.strip():
                text = f"{title.strip()}\n\n{text}"
            rating = dig(it, mapping.get("rating")) if mapping.get("rating") else None
            try:
                rating = float(rating) if rating not in (None, "") else None
            except (TypeError, ValueError):
                rating = None
            author = dig(it, mapping.get("author")) if mapping.get("author") else None
            date = dig(it, mapping.get("date")) if mapping.get("date") else None
            link = dig(it, mapping.get("url")) if mapping.get("url") else None
            out.append({"raw_text": text, "author": str(author) if author else None,
                        "posted_at": str(date) if date not in (None, "") else None,
                        "rating": rating, "url": str(link) if link else ""})
        return out

    def collect(self, name: str, endpoint: str, mapping: dict[str, str],
                queries: list, auth: dict | None = None, key_env: str | None = None
                ) -> Iterator[dict[str, Any]]:
        key = os.getenv(key_env) if key_env else None
        if key_env and not key:
            raise RuntimeError(f"{key_env} is not set. Add the API key in the web "
                               f"app's Settings tab or in .env")
        for url in self.build_urls(endpoint, queries):
            log.info("api %s :: %s", name, url.split("?")[0])
            try:
                data = self.get_json(url, auth, key)
            except RobotsBlocked:
                raise
            except Exception as exc:
                log.error("api %s failed: %s", name, exc)
                continue
            for item in self.items(data, mapping):
                yield {**item, "url": item["url"] or url.split("?")[0],
                       "source_name": name}


# ------------------------------------------------------------------- manual import
class ManualImportCollector(BaseCollector):
    """Reads hand-collected data.

    CSV columns: source, product, author, date, rating, text
    Also accepts a directory of saved .html files parsed with the given selectors.
    """
    platform = "manual"

    def collect(self, path: str, selectors: dict[str, str] | None = None
                ) -> Iterator[dict[str, Any]]:
        p = Path(path)
        if p.is_file() and p.suffix.lower() == ".csv":
            with p.open(newline="", encoding="utf-8-sig") as fh:
                for row in csv.DictReader(fh):
                    if not (row.get("text") or "").strip():
                        continue
                    rating = row.get("rating")
                    yield {
                        "raw_text": row["text"],
                        "url": row.get("url", ""),
                        "author": row.get("author") or None,
                        "posted_at": row.get("date") or None,
                        "rating": float(rating) if rating not in (None, "") else None,
                        "source_name": row.get("source") or "manual_csv",
                        "product_hint": row.get("product"),
                    }
            return
        if p.is_dir() and selectors:
            from selectolax.lexbor import LexborHTMLParser as HTMLParser
            for f in sorted(p.glob("*.html")):
                tree = HTMLParser(f.read_text(encoding="utf-8", errors="replace"))
                for node in tree.css(selectors["post"]):
                    el = node.css_first(selectors["body"])
                    if not el:
                        continue
                    yield {
                        "raw_text": el.text(strip=True),
                        "url": f.name,
                        "author": None,
                        "posted_at": None,
                        "rating": None,
                        "source_name": f"manual_html:{f.stem}",
                    }


# ---------------------------------------------------------------- product matching
# Letters that count as "inside a word" for brand matching. Deliberately Latin
# only: \b treats Chinese and Thai characters as word characters too, so
# r"\b小米\b" never matches inside unspaced text like 我买了小米净水器.
_WORDCH = "0-9A-Za-z\u00C0-\u024F\u1E00-\u1EFF"


def term_pattern(term: str) -> re.Pattern:
    body = r"\s*".join(re.escape(tok) for tok in term.split())
    return re.compile(rf"(?<![{_WORDCH}]){body}(?![{_WORDCH}])", re.IGNORECASE)


CATEGORY_HINTS = [
    (re.compile(r"\b(jug|pitcher|brita|filter jug)\b", re.I), "jug_filter"),
    (re.compile(r"\b(dispenser|instant hot|hot ?and ?cold|tankless|countertop)\b", re.I),
     "countertop_dispenser"),
    (re.compile(r"\b(purifier|under.?sink|reverse osmosis|\bRO\b)\b", re.I), "purifier"),
]


def build_matcher(products: list[dict[str, Any]],
                  category_terms: dict[str, list[str]] | None = None):
    """Returns fn(text) -> (brand, model, category) or (None, None, category_guess).

    Brands keep one canonical (usually Latin) name across countries so they can
    be compared; `aliases` lists local-script names and other spellings that
    resolve to the same brand. `category_terms` adds local-language category
    words to the English hints.
    """
    compiled = []
    for prod in products:
        brand = prod["brand"]
        cat = prod.get("category", "unknown")
        # Always include the bare brand as a fallback, plus one pattern per model.
        # Sorting by length below means "novita W29" wins over bare "novita".
        needles: list[tuple[str, str | None]] = [(brand, None)]
        needles += [(f"{brand} {m}", m) for m in (prod.get("models") or []) if m]
        needles += [(a, None) for a in (prod.get("aliases") or []) if a]
        for needle, model in needles:
            compiled.append((term_pattern(needle), brand, model, cat, len(needle)))
    compiled.sort(key=lambda x: -x[4])  # longest (most specific) match wins

    hints = list(CATEGORY_HINTS)
    for cat in ("jug_filter", "countertop_dispenser", "purifier"):
        for term in (category_terms or {}).get(cat) or []:
            if term and term.strip():
                hints.append((term_pattern(term.strip()), cat))

    def match(text: str):
        for pattern, brand, model, cat, _ in compiled:
            if pattern.search(text or ""):
                return brand, model, cat
        for pattern, cat in hints:
            if pattern.search(text or ""):
                return None, None, cat
        return None, None, "unknown"

    return match
