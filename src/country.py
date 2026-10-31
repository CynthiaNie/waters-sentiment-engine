"""Country profiles: everything that differs from one market to the next.

One YAML file per country in config/countries/ holds the search queries, the
brand watchlist, the languages, the YouTube region and every extra source
(pasted websites, custom APIs, Reddit, a manual CSV). The WATERS taxonomy in
config/waters.yaml is shared: it is the instrument, and changing it per country
would make the countries incomparable.

Each country gets its own database and output folder, so a Thai run can never
leak documents into the Singapore analysis. Singapore keeps the original paths
(data/waters.db, outputs/) through the `paths` block in sg.yaml.
"""
from __future__ import annotations

import os
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

COUNTRY_DIR = Path(os.getenv("WATERS_COUNTRIES", "config/countries"))
DEFAULT_CODE = os.getenv("WATERS_COUNTRY", "sg")
CATEGORIES = ["countertop_dispenser", "purifier", "jug_filter"]

DEFAULTS: dict[str, Any] = {
    "languages": ["en"],
    "currency": "",
    # keep_global: also analyse videos the market check finds about the category
    # with no sign of any one country. Off by default, as in the first Singapore study.
    "youtube": {"enabled": True, "region": "", "max_videos": 8, "max_comments": 100,
                "keep_global": False},
    "queries": [],
    "products": [],
    "category_terms": {c: [] for c in CATEGORIES},
    "reddit": {"enabled": False, "subreddits": [], "limit_per_query": 25,
               "comments": True},
    "websites": [],
    "custom_apis": [],
    "manual_csv": "",
    "notes": "",
}

_CODE_RE = re.compile(r"^[a-z][a-z0-9_-]{1,15}$")


def valid_code(code: str) -> bool:
    return bool(_CODE_RE.match(code or ""))


def _path(code: str) -> Path:
    if not valid_code(code):
        raise ValueError(f"invalid country code {code!r}: use letters, e.g. 'sg', 'my'")
    return COUNTRY_DIR / f"{code}.yaml"


def list_countries() -> list[dict[str, str]]:
    out = []
    for f in sorted(COUNTRY_DIR.glob("*.yaml")):
        try:
            spec = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            continue
        out.append({"code": f.stem, "name": spec.get("name", f.stem.upper())})
    return out


def _merge(base: dict, over: dict) -> dict:
    out = deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def load(code: str | None = None) -> dict[str, Any]:
    code = (code or DEFAULT_CODE).lower()
    p = _path(code)
    if not p.exists():
        known = ", ".join(c["code"] for c in list_countries()) or "none"
        raise FileNotFoundError(f"no profile for '{code}' (known: {known}). "
                                f"Create one in the web app or config/countries/.")
    prof = _merge(DEFAULTS, yaml.safe_load(p.read_text(encoding="utf-8")) or {})
    prof["code"] = code
    prof.setdefault("name", code.upper())
    if not prof["youtube"].get("region"):
        prof["youtube"]["region"] = code.upper()[:2]
    return prof


def save(profile: dict[str, Any]) -> Path:
    p = _path(profile["code"])
    p.parent.mkdir(parents=True, exist_ok=True)
    _archive(profile["code"])
    # Comments are not preserved: the web app owns the file once it saves it.
    p.write_text(yaml.safe_dump(_body(profile), sort_keys=False, allow_unicode=True,
                                width=100), encoding="utf-8")
    return p


# --------------------------------------------------------------------- history
# Every save first copies the file it replaces, because the web app saves on
# each change and has no undo. The oldest copy is kept for good: it is the
# setup as it was before the web app first changed it.
KEEP_VERSIONS = 100


def _history_dir(code: str) -> Path:
    return COUNTRY_DIR / ".history" / code


def _archive(code: str) -> None:
    p = _path(code)
    if not p.exists():
        return
    d = _history_dir(code)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{datetime.now():%Y%m%d-%H%M%S-%f}.yaml").write_bytes(p.read_bytes())
    old = sorted(d.glob("*.yaml"))
    for f in old[1:len(old) - KEEP_VERSIONS + 1]:
        f.unlink()


def history(code: str) -> list[Path]:
    """Earlier versions of a country's file, newest first."""
    return sorted(_history_dir(code).glob("*.yaml"), reverse=True)


def restore(code: str, version: Path) -> None:
    """Put an earlier version back. The current file is archived first, so a
    restore can itself be undone."""
    _archive(code)
    _path(code).write_bytes(version.read_bytes())


def delete(profile: dict[str, Any], extra: list[Path] = ()) -> Path:
    """Remove a country from the app by moving everything it owns to
    backups/<code>_<time>/, so a deletion can be undone by moving it back."""
    code = profile["code"]
    owned = {_path(code): _path(code).name, _history_dir(code): "history",
             **{x: x.name for x in extra}, **_data(profile, whole_folder=True)}
    return _move(owned, Path("backups") / f"{code}_{datetime.now():%Y%m%d-%H%M%S}")


def clear_data(profile: dict[str, Any]) -> Path:
    """Move a country's database and results to backups/, keeping its settings
    and hand-collected CSV, so the next run starts from nothing."""
    return _move(_data(profile), Path("backups") /
                 f"{profile['code']}_data_{datetime.now():%Y%m%d-%H%M%S}")


def _data(profile: dict[str, Any], whole_folder: bool = False) -> dict[Path, str]:
    """The database and results a country owns, with their names in a backup.
    whole_folder also takes the rest of the database folder (the hand-collected
    CSV). A folder is moved whole only when it is this country's own; a `paths`
    override may point at a shared folder, and then only the database goes."""
    code, p = profile["code"], paths(profile)
    owned = {}
    db_file = p["db"]
    if whole_folder and db_file.parent.name == code:
        owned[db_file.parent] = "data"
    else:
        for s in ("", "-wal", "-shm"):
            owned[db_file.with_name(db_file.name + s)] = f"data/{db_file.name}{s}"
    if p["outputs"].name == code:
        owned[p["outputs"]] = "outputs"
    return owned


def _move(owned: dict[Path, str], dest: Path) -> Path:
    for src, name in owned.items():
        if src.exists():
            (dest / name).parent.mkdir(parents=True, exist_ok=True)
            src.rename(dest / name)
    return dest


def same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """True when saving either would write the same settings, so the web app can
    save on every change without rewriting a file that did not change."""
    return _body(a) == _body(b)


def _body(profile: dict[str, Any]) -> dict[str, Any]:
    profile = deepcopy(profile)
    # Keep the file in the short hand-written form: a query in the primary
    # language is a plain string, and empty aliases are left out.
    primary = (profile.get("languages") or ["en"])[0]
    profile["queries"] = [
        q["text"] if isinstance(q, dict) and q.get("lang") in (None, "", primary) else q
        for q in profile.get("queries") or []]
    for prod in profile.get("products") or []:
        if not prod.get("aliases"):
            prod.pop("aliases", None)
    # Drop keys that only restate a default, so hand-edited files stay short.
    return {k: v for k, v in profile.items() if k not in DEFAULTS or v != DEFAULTS[k]}


def paths(profile: dict[str, Any]) -> dict[str, Path]:
    code = profile["code"]
    over = profile.get("paths") or {}
    return {
        "db": Path(over.get("db", f"data/countries/{code}/waters.db")),
        "outputs": Path(over.get("outputs", f"outputs/{code}")),
        "manual_csv": Path(profile.get("manual_csv")
                           or f"data/countries/{code}/manual/reviews.csv"),
    }


def activate(profile: dict[str, Any]) -> None:
    """Point the storage and export modules at this country's files."""
    from . import db, export
    p = paths(profile)
    db.DB_PATH = p["db"]
    export.OUT_DIR = p["outputs"]


def queries(profile: dict[str, Any]) -> list[tuple[str, str | None]]:
    """Queries as (text, language). Plain strings use the primary language."""
    primary = (profile.get("languages") or ["en"])[0]
    out = []
    for q in profile.get("queries") or []:
        if isinstance(q, dict):
            if (q.get("text") or "").strip():
                out.append((q["text"].strip(), q.get("lang") or primary))
        elif str(q).strip():
            out.append((str(q).strip(), primary))
    return out


def find_website(profile: dict[str, Any], site: str) -> dict[str, Any] | None:
    return next((w for w in profile.get("websites") or [] if w.get("name") == site), None)


def add_website(profile: dict[str, Any], url: str, detected: dict[str, Any]
                ) -> dict[str, Any]:
    """Record a detected page. Pages from one site share one entry and selectors."""
    site = find_website(profile, detected["name"])
    if site is None:
        site = {"name": detected["name"], "enabled": True, "urls": [], "max_pages": 20}
        profile.setdefault("websites", []).append(site)
    if url not in site["urls"]:
        site["urls"].append(url)
    site["selectors"] = detected["selectors"]
    site["page_param"] = detected.get("page_param") or ""
    return site
