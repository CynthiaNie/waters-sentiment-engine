"""WATERS Sentiment Engine: the web front end, in English or Chinese.

    python -m src.cli web        (or: streamlit run webapp.py)

Runs locally. Pick a country, check its brands and search terms, optionally
paste extra websites or an API, then run collection, classification and
export. Every action goes through the same code as the command line; long runs
happen in a background process.
"""
from __future__ import annotations

import gettext
import json
import os
import secrets
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pandas as pd
import pycountry
import streamlit as st
import yaml
from dotenv import set_key, unset_key

from src import analyse, autodetect, classify, country, db, jobs, llm, scope
from src.i18n import CODE_NAMES_ZH, CODE_DEFS_ZH, SENTIMENT_ZH, ZH

st.set_page_config(page_title="WATERS Sentiment Engine", page_icon="💧", layout="wide")

ENV_PATH = Path(".env")
BAR_COLOR = "#2a78d6"
CATEGORY_LABELS = {"countertop_dispenser": "Countertop dispenser",
                   "purifier": "Purifier", "jug_filter": "Jug filter"}
LIMITS = [0, 50, 100, 200, 500, 1000, 2000, 5000]
AUTH_NAMES = {"header": ["Authorization", "X-API-Key", "Api-Key", "X-Auth-Token"],
              "query": ["key", "api_key", "apikey", "apiKey", "token", "access_token"]}
_NAME_FIX = {"VN": "Vietnam", "KR": "South Korea", "KP": "North Korea", "LA": "Laos",
             "RU": "Russia", "IR": "Iran", "SY": "Syria", "TZ": "Tanzania",
             "BO": "Bolivia", "VE": "Venezuela", "MD": "Moldova", "BN": "Brunei",
             "TW": "Taiwan", "HK": "Hong Kong", "MO": "Macau"}


# ------------------------------------------------------------------ language
def env_set(key: str, value: str) -> None:
    """Write to .env (local only, never shared) and to this process."""
    ENV_PATH.touch(exist_ok=True)
    set_key(str(ENV_PATH), key, value, quote_mode="never")
    os.environ[key] = value


def env_clear(keys: list[str], widgets: list[str], msg: str) -> None:
    """Remove saved values from .env and this process. The fields that set them
    are emptied too, or autosave would write the typed value straight back."""
    for k in keys:
        if ENV_PATH.exists():
            unset_key(str(ENV_PATH), k, quote_mode="never")
        os.environ.pop(k, None)
    for w in widgets:
        st.session_state.pop(w, None)
    st.session_state["_toast"] = t(msg)
    st.rerun()


if "ui_lang" not in st.session_state:
    st.session_state["ui_lang"] = os.getenv("UI_LANGUAGE", "en")
LANG = st.session_state["ui_lang"]


def t(text: str, /, **kw) -> str:
    """Interface text in the chosen language; English is the key and fallback.
    `text` is positional-only so a placeholder may be called {text} too."""
    out = ZH.get(text, text) if LANG == "zh" else text
    return out.format(**kw) if kw else out


def err(exc: Exception) -> str:
    if isinstance(exc, llm.UserError):
        return t(exc.template).format(**exc.kw)
    return str(exc)


def _zh(domain: str):
    try:
        return gettext.translation(domain, pycountry.LOCALES_DIR, languages=["zh_CN"]).gettext
    except OSError:
        return lambda s: s


_ZH_COUNTRY, _ZH_LANG, _ZH_CUR = _zh("iso3166-1"), _zh("iso639-3"), _zh("iso4217")


def country_label(a2: str) -> str:
    c = pycountry.countries.get(alpha_2=(a2 or "").upper())
    if c is None:
        return a2
    if LANG == "zh":
        return _ZH_COUNTRY(c.name)
    return _NAME_FIX.get(c.alpha_2) or getattr(c, "common_name", None) or c.name


def lang_label(code: str) -> str:
    l = pycountry.languages.get(alpha_2=code) if len(code or "") == 2 else None
    if not l:
        return code
    name = l.name.replace(" (macrolanguage)", "")
    if LANG == "zh":
        name = _ZH_LANG(name) if _ZH_LANG(name) != name else _ZH_LANG(l.name)
    return f"{name} ({code})"


def currency_label(code: str) -> str:
    c = pycountry.currencies.get(alpha_3=code) if code else None
    if not c:
        return code or "—"
    return f"{code} · {_ZH_CUR(c.name) if LANG == 'zh' else c.name}"


def need_label(code: str) -> str:
    if LANG == "zh":
        return f"{CODE_NAMES_ZH.get(code, code)} ({code})"
    return f"{analyse.CODE_NAMES.get(code, code)} ({code})"


def sentiment_label(s: str) -> str:
    return SENTIMENT_ZH.get(s, s) if LANG == "zh" else s


@st.cache_data
def code_lists() -> dict[str, list[str]]:
    return {"countries": [c.alpha_2 for c in pycountry.countries],
            "languages": [l.alpha_2 for l in pycountry.languages if hasattr(l, "alpha_2")],
            "currencies": sorted(c.alpha_3 for c in pycountry.currencies)}


CODES = code_lists()
COUNTRY_OPTS = sorted(CODES["countries"], key=country_label)
# In Chinese, languages with a Chinese name come first; the rest keep English names.
LANG_OPTS = sorted(CODES["languages"],
                   key=lambda c: (LANG == "zh" and lang_label(c)[:1].isascii(), lang_label(c)))


def with_current(options: list[str], *current) -> list[str]:
    """Options plus any value already saved, so nothing silently drops."""
    extra = [c for cur in current for c in (cur if isinstance(cur, list) else [cur])
             if c and c not in options]
    return options + extra


# ------------------------------------------------------------------ helpers
def has(key: str) -> bool:
    return bool(os.getenv(key, "").strip())


def split_csv(text) -> list[str]:
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return []
    return [x.strip() for x in str(text).split(",") if x.strip()]


def save(prof: dict, msg: str = "Saved.") -> None:
    country.save(prof)
    st.session_state["_flash"] = t(msg)
    st.rerun()


# Settings save as soon as a widget changes. The rerun shows the stored value at
# once, before the next edit, so a table edit is never lost or applied twice.
def autosave(old: dict, new: dict, msg: str) -> None:
    if not country.same(old, new):
        country.save(new)
        st.session_state["_toast"] = t(msg)
        st.rerun()


def env_autosave(want: dict[str, str], have: dict[str, str], msg: str) -> None:
    if want != have:
        for k, v in want.items():
            env_set(k, v)
        st.session_state["_toast"] = t(msg)
        st.rerun()


def db_counts(prof: dict) -> dict | None:
    if not country.paths(prof)["db"].exists():
        return None
    db.init()  # idempotent; brings databases from older versions up to date
    with db.connect() as con:
        c = db.counts(con)
        c["unclassified"] = len(db.unclassified(con))
        c["spot"] = db.spot_summary(con)
    return c


# ------------------------------------------------------------------ sidebar
profiles = country.list_countries()
if not profiles:
    st.error(t("No country profiles found in config/countries/."))
    st.stop()
codes = [c["code"] for c in profiles]
prof_names = {c["code"]: c["name"] for c in profiles}


def profile_label(c: str) -> str:
    name = country_label(c) if pycountry.countries.get(alpha_2=c.upper()) else prof_names[c]
    return f"{name} ({c})"


# Switching the interface language relabels select boxes, and Streamlit then treats
# them as new widgets and forgets their value. Keep the choices that are stored
# nowhere else and give them back.
_sticky = st.session_state.setdefault("_sticky", {})
# A reload starts a new session; the country and section are kept in the URL.
if "country" not in st.session_state and st.query_params.get("country") in codes:
    st.session_state["country"] = st.query_params["country"]
for k in ["country"] + [f"{c}_{w}" for c in codes
                        for w in ("run_lim", "run_clim", "clear_what")] + ["section"]:
    if k in st.session_state:
        _sticky[k] = st.session_state[k]
    elif k in _sticky:
        st.session_state[k] = _sticky[k]

if "_switch_to" in st.session_state:
    st.session_state["country"] = st.session_state.pop("_switch_to")
if st.session_state.get("country") not in codes:
    st.session_state["country"] = "sg" if "sg" in codes else codes[0]

with st.sidebar:
    picked = st.radio("Language / 语言", ["en", "zh"], horizontal=True,
                      index=["en", "zh"].index(LANG) if LANG in ("en", "zh") else 0,
                      format_func={"en": "English", "zh": "中文"}.get, key="ui_lang_pick")
    if picked != LANG:
        st.session_state["ui_lang"] = picked
        env_set("UI_LANGUAGE", picked)  # remembered next time the app opens
        st.rerun()

    st.title(t("💧 WATERS engine"))
    code = st.selectbox(t("Country"), codes, format_func=profile_label, key="country")

    with st.expander(t("➕ Add a country")):
        pick = st.selectbox(
            t("Country"), [c for c in COUNTRY_OPTS if c.lower() not in codes],
            index=None, placeholder=t("Choose or type to search…"),
            format_func=country_label, key="new_country_pick")
        st.caption(t("The AI drafts the languages, local search terms and a brand "
                     "list. You review and edit it before anything is collected."))
        c1, c2 = st.columns(2)
        new = None
        if c1.button(t("Draft with AI"), disabled=pick is None, type="primary"):
            with st.spinner(t("Drafting a profile for {name}…", name=country_label(pick))):
                try:
                    # The model is given the English name; notes come back in both
                    # interface languages.
                    new = autodetect.draft_country(
                        _NAME_FIX.get(pick) or pycountry.countries.get(alpha_2=pick).name)
                except Exception as exc:
                    st.error(err(exc))
        if c2.button(t("Start empty"), disabled=pick is None,
                     help=t("No AI call: you fill in brands and search terms.")):
            new = {"queries": [], "products": [], "languages": ["en"],
                   "youtube": {"enabled": True}}
        if new is not None:
            # The picked country decides code, name and region, whatever the draft says.
            c = pycountry.countries.get(alpha_2=pick)
            new.update(code=pick.lower(), name=_NAME_FIX.get(pick)
                       or getattr(c, "common_name", None) or c.name)
            new.setdefault("youtube", {})["region"] = pick
            country.save(new)
            st.session_state["_switch_to"] = new["code"]
            st.session_state["_flash"] = t(
                "Created {name}. Review the brands and search terms below before "
                "collecting.", name=country_label(pick))
            st.rerun()

    prof = country.load(code)
    country.activate(prof)
    cname = profile_label(code).rsplit(" (", 1)[0]

    st.divider()
    counts = db_counts(prof)
    if counts:
        c1, c2 = st.columns(2)
        c1.metric(t("Documents"), f"{counts['documents']:,}")
        c2.metric(t("Mentions"), f"{counts['mentions_in_scope']:,}")
        st.caption(t("{n} documents waiting to be classified",
                     n=f"{counts['unclassified']:,}"))
    else:
        st.caption(t("No data collected for {name} yet.", name=cname))

    st.divider()
    st.caption(t("**Setup** (in Settings)"))
    for ok, label in ((has(llm.key_env()), t("AI provider: {p}", p=llm.describe())),
                      (has("YOUTUBE_API_KEY"), t("YouTube key")),
                      (has("ORG_NAME") and has("RESEARCH_CONTACT"),
                       t("Organisation name and contact")),
                      (has("AUTHOR_SALT"), t("Author salt"))):
        st.caption(f"{'✅' if ok else '⚠️'} {label}")

# Always present, so the tabs below keep their position and the open tab survives
# the rerun after a save (a new element above them resets them to the first tab).
flash = st.empty()
if "_flash" in st.session_state:
    flash.success(st.session_state.pop("_flash"))
if "_toast" in st.session_state:
    st.toast(st.session_state.pop("_toast"), icon="✅")

st.header(cname)
_notes = country.notes(prof)
if any(_notes.values()):
    st.info(_notes.get(LANG) or next(v for v in _notes.values() if v))

# A selector rather than st.tabs: the open tab of st.tabs is known only to the
# browser, so it reset to the first one whenever the language changed.
SECTIONS = {"setup": "1 · Country setup", "sources": "2 · Sources", "run": "3 · Run",
            "results": "4 · Results", "settings": "⚙ Settings"}
if "section" not in st.session_state:
    st.session_state["section"] = (st.query_params.get("section")
                                   if st.query_params.get("section") in SECTIONS
                                   else "setup")


def _keep_section() -> None:
    # Clicking the selected button again would leave nothing selected.
    if st.session_state["section"] is None:
        st.session_state["section"] = st.session_state.get("_last_section", "setup")


section = st.segmented_control(t("Section"), list(SECTIONS), key="section",
                               format_func=lambda s: t(SECTIONS[s]),
                               on_change=_keep_section, label_visibility="collapsed")
st.session_state["_last_section"] = section
st.query_params["country"], st.query_params["section"] = code, section


@st.fragment(run_every="2s")
def job_watch():
    """Runs on every section. The page only redraws on a click, so when a
    background run ends, redraw it to bring counts and Results up to date."""
    s = jobs.status(code)
    if s and s["running"]:
        st.session_state[f"_watching_{code}"] = True
    elif st.session_state.pop(f"_watching_{code}", False):
        st.rerun(scope="app")


job_watch()

# ============================================================== country setup
if section == "setup":
    st.subheader(t("Market"))
    c1, c2, c3 = st.columns([4, 2, 2])
    langs = c1.multiselect(
        t("Languages (main one first)"), with_current(LANG_OPTS, prof["languages"]),
        default=prof["languages"], format_func=lang_label, key=f"{code}_langs",
        help=t("Used to search YouTube in the right language. Posts in any "
               "language are coded either way."))
    reg_opts = with_current(COUNTRY_OPTS, prof["youtube"]["region"].upper())
    region = c2.selectbox(t("YouTube region"), reg_opts,
                          index=reg_opts.index(prof["youtube"]["region"].upper()),
                          format_func=country_label, key=f"{code}_region")
    cur_opts = with_current(CODES["currencies"], prof.get("currency") or "")
    currency = c3.selectbox(t("Currency"), cur_opts,
                            index=cur_opts.index(prof["currency"])
                            if prof.get("currency") in cur_opts else None,
                            format_func=currency_label, key=f"{code}_cur")

    st.subheader(t("Search terms"))
    st.caption(t("Used for YouTube, Reddit and any API with {query} in it. Write them "
                 "the way local consumers would type them."))
    qrows = [{"text": q, "lang": l} for q, l in country.queries(prof)]
    qdf = st.data_editor(
        pd.DataFrame(qrows or [{"text": "", "lang": prof["languages"][0]}]),
        num_rows="dynamic", key=f"{code}_queries", hide_index=True,
        column_config={
            "text": st.column_config.TextColumn(t("Query"), width="large"),
            "lang": st.column_config.SelectboxColumn(
                t("Language"), width="medium", required=True,
                options=with_current(list(langs or prof["languages"]),
                                     [l for _, l in country.queries(prof)]))})

    st.subheader(t("Brand watchlist"))
    st.caption(t("Brand names in Latin script so countries compare; local-script "
                 "names and other spellings go in Aliases. Separate lists with commas."))
    prows = [{"brand": p["brand"], "category": p.get("category", "countertop_dispenser"),
              "models": ", ".join(p.get("models") or []),
              "aliases": ", ".join(p.get("aliases") or [])} for p in prof["products"]]
    pdf = st.data_editor(
        pd.DataFrame(prows or [{"brand": "", "category": "countertop_dispenser",
                                "models": "", "aliases": ""}]),
        num_rows="dynamic", key=f"{code}_products", hide_index=True,
        column_config={
            "brand": st.column_config.TextColumn(t("Brand")),
            "category": st.column_config.SelectboxColumn(
                t("Category"), options=country.CATEGORIES, required=True),
            "models": st.column_config.TextColumn(t("Models"), width="large"),
            "aliases": st.column_config.TextColumn(t("Aliases"))})
    st.caption(t("Categories: countertop_dispenser = countertop dispenser, purifier = "
                 "purifier, jug_filter = filter jug."))

    with st.expander(t("Local words for each category (tags posts that name no brand)")):
        st.caption(t("Click × to remove a word; type a new one and press Enter to add it."))
        terms = {}
        for cat in country.CATEGORIES:
            cur = prof["category_terms"].get(cat) or []
            terms[cat] = st.multiselect(t(CATEGORY_LABELS[cat]), cur, default=cur,
                                        accept_new_options=True,
                                        key=f"{code}_terms_{cat}")

    st.caption(t("Changes are saved automatically."))
    new = deepcopy(prof)
    new["languages"] = list(langs) or ["en"]
    new["youtube"]["region"] = region
    new["currency"] = currency or ""
    new["queries"] = [
        {"text": str(r["text"]).strip(),
         "lang": (str(r["lang"]).strip() if pd.notna(r["lang"]) and str(r["lang"]).strip()
                  else new["languages"][0])}
        for _, r in qdf.iterrows() if pd.notna(r["text"]) and str(r["text"]).strip()]
    new["products"] = [
        {"brand": str(r["brand"]).strip(), "category": r["category"],
         "models": split_csv(r["models"]), "aliases": split_csv(r["aliases"])}
        for _, r in pdf.iterrows() if pd.notna(r["brand"]) and str(r["brand"]).strip()]
    new["category_terms"] = {c: [x.strip() for x in v if x.strip()]
                             for c, v in terms.items()}
    with st.expander(t("Notes shown at the top of the page")):
        st.caption(t("Each language shows its own text; a blank one falls back to the "
                     "other. Clear both to hide the box."))
        cur = country.notes(prof)
        typed = {"en": st.text_area("English", cur["en"], key=f"{code}_notes_en"),
                 "zh": st.text_area("中文", cur["zh"], key=f"{code}_notes_zh")}
    # Only touched when edited, so a profile with old one-string notes is not
    # rewritten just by opening it.
    if {k: v.strip() for k, v in typed.items()} != {k: v.strip() for k, v in cur.items()}:
        new["notes"] = ({k: v.strip() for k, v in typed.items()}
                        if any(v.strip() for v in typed.values()) else "")
    autosave(prof, new, "Country setup saved.")

    with st.expander(t("Restore an earlier version")):
        versions = {v.name: v for v in country.history(code)}
        if not versions:
            st.caption(t("No earlier versions yet. One is kept every time this "
                         "country's settings change."))
        else:
            st.caption(t("Covers this tab and the Sources tab. The current settings are "
                         "kept as a version too, so a restore can be undone."))

            def version_label(name: str) -> str:
                spec = yaml.safe_load(versions[name].read_text(encoding="utf-8")) or {}
                when = datetime.strptime(name[:15], "%Y%m%d-%H%M%S")
                label = t("{when} · {q} search terms, {b} brands",
                          when=f"{when:%Y-%m-%d %H:%M}", q=len(spec.get("queries") or []),
                          b=len(spec.get("products") or []))
                return label + (t(" (earliest)") if name == list(versions)[-1] else "")

            pick = st.selectbox(t("Version"), list(versions), format_func=version_label,
                                key=f"{code}_version")
            if st.button(t("Restore this version"), key=f"{code}_restore"):
                country.restore(code, versions[pick])
                # Widgets keep their own values across reruns; drop them so they
                # show the restored settings instead of saving the old ones back.
                for k in list(st.session_state):
                    if str(k).startswith(f"{code}_") and not str(k).startswith(f"{code}_run"):
                        del st.session_state[k]
                st.session_state["_toast"] = t("Restored the version from {when}.",
                                               when=version_label(pick).split(" · ")[0])
                st.rerun()

    with st.expander(t("Delete this country")):
        st.caption(t("Moves the settings, earlier versions, database, results and log of "
                     "{name} to the backups folder. Nothing is erased: move the files "
                     "back to undo.", name=cname))
        if len(codes) == 1:
            st.caption(t("The last country cannot be deleted."))
        elif jobs.running(code):
            st.caption(t("Stop the running job first."))
        sure = st.checkbox(t("Yes, delete {name}", name=cname), key=f"{code}_del_sure")
        if st.button(t("Delete country"), key=f"{code}_delete",
                     disabled=not sure or len(codes) == 1 or jobs.running(code)):
            dest = country.delete(prof, extra=[jobs.log_path(code)])
            jobs.forget(code)
            st.session_state["_switch_to"] = next(c for c in codes if c != code)
            st.session_state["_flash"] = t("Deleted {name}. Its files are in {dir}.",
                                           name=cname, dir=dest.as_posix())
            st.rerun()

# ==================================================================== sources
if section == "sources":
    # ---------------------------------------------------------------- youtube
    st.subheader(t("YouTube (default)"))
    st.caption(t("Changes are saved automatically."))
    if not has("YOUTUBE_API_KEY"):
        st.warning(t("No YouTube API key yet. Add it in Settings."))
    c1, c2, c3 = st.columns(3)
    yt_on = c1.toggle(t("Collect from YouTube"), prof["youtube"].get("enabled", True),
                      key=f"{code}_yt_on")
    yt_vid = c2.slider(t("Videos per query"), 1, 50,
                       int(prof["youtube"].get("max_videos", 8)), key=f"{code}_yt_v")
    yt_com = c3.slider(t("Comments per video"), 10, 1000,
                       int(prof["youtube"].get("max_comments", 100)), step=10,
                       key=f"{code}_yt_c")
    st.caption(t("{q} queries × {v} videos. Each search uses 100 of the key's 10,000 "
                 "free daily quota units.", q=len(country.queries(prof)), v=yt_vid))
    st.caption(t("YouTube search also returns videos made for other countries. Before "
                 "classifying, the AI checks each video and leaves out those that are "
                 "not about {name} or whose commenters are elsewhere. Nothing is "
                 "deleted: the workbook lists every video with the reason.", name=cname))
    yt_glob = st.toggle(t("Also keep videos with no sign of any one country"),
                        bool(prof["youtube"].get("keep_global", False)),
                        key=f"{code}_yt_glob",
                        help=t("For example general science explainers with comments "
                               "from everywhere. Off by default: they say little about "
                               "this market."))
    new = deepcopy(prof)
    new["youtube"].update(enabled=yt_on, max_videos=int(yt_vid),
                          max_comments=int(yt_com), keep_global=bool(yt_glob))
    if (bool(yt_glob) != bool(prof["youtube"].get("keep_global", False))
            and country.paths(prof)["db"].exists()):
        with db.connect() as con:  # stored verdicts, so no AI call is needed
            scope.apply(con, prof["name"], bool(yt_glob))
    autosave(prof, new, "YouTube settings saved.")

    st.divider()
    # --------------------------------------------------------------- websites
    st.subheader(t("Other websites"))
    st.caption(t("Paste forum threads or review pages, one per line. The AI works out "
                 "how to read each site once; robots.txt and rate limits are always "
                 "respected, and marketplaces (Shopee, Lazada, Amazon, Taobao, JD…) are "
                 "refused. Pages built by JavaScript can't be read: use the CSV upload "
                 "below."))
    urls_text = st.text_area(t("Page addresses"), key=f"{code}_paste_urls",
                             placeholder="https://forum.example.com/threads/water-dispenser.123/")
    det_key = f"{code}_detected"
    if st.button(t("Read these pages"), key=f"{code}_detect_btn",
                 disabled=not urls_text.strip()):
        results = []
        for url in [u.strip() for u in urls_text.splitlines() if u.strip()]:
            with st.spinner(t("Reading {url}…", url=url)):
                try:
                    res = autodetect.detect_site(
                        url, country.find_website(prof, autodetect.site_name(url)),
                        lang=LANG)
                    results.append({"url": url, "result": res})
                except Exception as exc:
                    results.append({"url": url, "error": err(exc)})
        st.session_state[det_key] = results

    for i, item in enumerate(st.session_state.get(det_key, [])):
        with st.container(border=True):
            st.markdown(f"**{item['url']}**")
            if "error" in item:
                st.error(item["error"])
                continue
            res = item["result"]
            if res["problems"]:
                st.warning(t("Could not read this page reliably: ") + "; ".join(
                    autodetect.problem_text(p, t) for p in res["problems"]))
            else:
                st.success(t("Found {n}+ posts on {site}. Check the sample below looks "
                             "like real posts.", n=len(res["sample"]), site=res["name"]))
            if res["sample"]:
                st.dataframe(pd.DataFrame(res["sample"])[["author", "posted_at", "raw_text"]],
                             hide_index=True)
            with st.expander(t("How it reads this site")):
                st.caption(res["reason"])
                st.json({**res["selectors"], "page_param": res["page_param"]})
            if not res["problems"] and st.button(t("Add to sources"),
                                                 key=f"{code}_add_site_{i}"):
                country.add_website(prof, item["url"], res)
                st.session_state[det_key] = [x for x in st.session_state[det_key]
                                             if x is not item]
                save(prof, "Website added.")

    for w in prof.get("websites") or []:
        with st.expander(f"{'✅' if w.get('enabled', True) else '⏸'} {w['name']} · "
                         + t("{n} page(s)", n=len(w.get("urls") or []))):
            k = f"{code}_site_{w['name']}"
            on = st.toggle(t("Collect from this site"), w.get("enabled", True),
                           key=k + "_on")
            pages = st.slider(t("Max pages per thread"), 1, 200,
                              min(int(w.get("max_pages", 20)), 200), key=k + "_pages")
            urls = st.text_area(t("Pages"), "\n".join(w.get("urls") or []), key=k + "_urls",
                                height=120)
            sel = st.text_area(t("Selectors (advanced)"),
                               json.dumps(w.get("selectors") or {}, indent=1,
                                          ensure_ascii=False), key=k + "_sel", height=150)
            c1, c2 = st.columns(2)
            if c1.button(t("Save"), key=k + "_save"):
                try:
                    w["selectors"] = json.loads(sel)
                except json.JSONDecodeError as exc:
                    st.error(t("Selectors are not valid JSON: {error}", error=exc))
                    st.stop()
                w.update(enabled=on, max_pages=int(pages),
                         urls=[u.strip() for u in urls.splitlines() if u.strip()])
                save(prof, "Website saved.")
            if c2.button(t("Remove site"), key=k + "_rm"):
                prof["websites"] = [x for x in prof["websites"] if x is not w]
                save(prof, "Website removed. Documents already collected are kept.")

    st.divider()
    # -------------------------------------------------------------------- apis
    st.subheader(t("APIs"))
    with st.expander(t("Custom API (any JSON API you have a key for)"),
                     expanded=bool(st.session_state.get(f"{code}_api_test"))):
        st.caption(t("Paste the endpoint. Put {query} where the search term goes and it "
                     "is called once per search term. The key is stored only in the "
                     "local .env file, never in the country profile."))
        endpoint = st.text_input(t("Endpoint"), key=f"{code}_api_ep",
                                 placeholder="https://api.example.com/v1/reviews?q={query}")
        api_name = autodetect.site_name(endpoint.strip()) if endpoint.strip() else ""
        c1, c2, c3 = st.columns(3)
        where = c1.selectbox(t("Key goes in"), ["header", "query", "none"],
                             key=f"{code}_api_where",
                             format_func={"header": t("a request header"),
                                          "query": t("a URL parameter"),
                                          "none": t("no key needed")}.get)
        aname = c2.selectbox(t("Header / parameter name"), AUTH_NAMES.get(where, ["—"]),
                             accept_new_options=True, key=f"{code}_api_aname_{where}",
                             disabled=where == "none",
                             help=t("Pick one, or type the name the API's docs give."))
        prefix = c3.selectbox(t("Value prefix"), ["Bearer ", "Token ", ""],
                              format_func=lambda v: v.strip() or t("none"),
                              key=f"{code}_api_prefix", disabled=where != "header")
        api_key = st.text_input(t("API key"), type="password", key=f"{code}_api_key",
                                disabled=where == "none")
        auth = None if where == "none" else {"type": where, "name": aname or "key",
                                             "prefix": prefix if where == "header" else ""}
        if st.button(t("Test and map fields"), key=f"{code}_api_testbtn",
                     disabled=not endpoint.strip()):
            with st.spinner(t("Calling the API and mapping its fields…")):
                try:
                    st.session_state[f"{code}_api_test"] = autodetect.map_api(
                        endpoint.strip(), country.queries(prof), auth, api_key or None,
                        lang=LANG)
                except Exception as exc:
                    st.session_state.pop(f"{code}_api_test", None)
                    st.error(err(exc))
        test = st.session_state.get(f"{code}_api_test")
        if test:
            if test["problems"]:
                st.warning("; ".join(autodetect.problem_text(p, t)
                                     for p in test["problems"]))
            if test["sample"]:
                st.dataframe(pd.DataFrame(test["sample"]), hide_index=True)
            st.caption(test["reason"])
            mapping = st.text_area(t("Field mapping"), json.dumps(test["mapping"], indent=1),
                                   key=f"{code}_api_map")
            if st.button(t("Save API"), type="primary", key=f"{code}_api_save",
                         disabled=bool(test["problems"])):
                slug = "".join(ch if ch.isalnum() else "_" for ch in api_name).strip("_")
                key_env = None
                if where != "none":
                    key_env = f"API_KEY_{code.upper()}_{slug.upper()}"
                    env_set(key_env, api_key)
                prof["custom_apis"] = [a for a in prof["custom_apis"]
                                       if a["name"] != api_name]
                prof["custom_apis"].append({
                    "name": api_name, "enabled": True, "endpoint": endpoint.strip(),
                    "auth": auth, "key_env": key_env, "mapping": json.loads(mapping)})
                st.session_state.pop(f"{code}_api_test", None)
                save(prof, "API added.")

    for a in prof.get("custom_apis") or []:
        c1, c2, c3 = st.columns([4, 1, 1])
        c1.markdown(f"{'✅' if a.get('enabled', True) else '⏸'} **{a['name']}** · "
                    f"`{a['endpoint'].split('?')[0]}`"
                    + ("" if not a.get("key_env") or has(a["key_env"])
                       else " · ⚠️ " + t("key missing")))
        if c2.button(t("Pause") if a.get("enabled", True) else t("Resume"),
                     key=f"{code}_api_tog_{a['name']}"):
            a["enabled"] = not a.get("enabled", True)
            save(prof)
        if c3.button(t("Remove"), key=f"{code}_api_rm_{a['name']}"):
            prof["custom_apis"] = [x for x in prof["custom_apis"] if x is not a]
            if a.get("key_env") and ENV_PATH.exists():
                unset_key(str(ENV_PATH), a["key_env"], quote_mode="never")
                os.environ.pop(a["key_env"], None)
            save(prof, "API removed.")

    with st.expander(t("Reddit (official API)")):
        rd = prof["reddit"]
        rd_on = st.toggle(t("Collect from Reddit"), rd.get("enabled", False),
                          key=f"{code}_rd_on")
        subs = st.multiselect(t("Subreddits (type a name and press Enter to add)"),
                              rd.get("subreddits") or [],
                              default=rd.get("subreddits") or [],
                              accept_new_options=True, key=f"{code}_rd_subs")
        c1, c2 = st.columns(2)
        rid = c1.text_input(t("Client ID"), type="password", key=f"{code}_rd_id",
                            placeholder=t("set (hidden)") if has("REDDIT_CLIENT_ID") else "")
        rsec = c2.text_input(t("Client secret"), type="password", key=f"{code}_rd_sec",
                             placeholder=t("set (hidden)") if has("REDDIT_CLIENT_SECRET")
                             else "")
        st.caption(t("Register a 'script' app at reddit.com/prefs/apps to get these."))
        # A blank key field keeps the stored value.
        want = {k: v.strip() for k, v in (("REDDIT_CLIENT_ID", rid),
                                          ("REDDIT_CLIENT_SECRET", rsec)) if v.strip()}
        env_autosave(want, {k: os.getenv(k, "") for k in want}, "Reddit settings saved.")
        if ((has("REDDIT_CLIENT_ID") or has("REDDIT_CLIENT_SECRET"))
                and st.button(t("Clear Reddit credentials"), key=f"{code}_rd_clear")):
            env_clear(["REDDIT_CLIENT_ID", "REDDIT_CLIENT_SECRET"],
                      [f"{c}_rd_{w}" for c in codes for w in ("id", "sec")], "Key cleared.")
        new = deepcopy(prof)
        new["reddit"].update(enabled=rd_on, subreddits=[x.strip().removeprefix("r/")
                                                        for x in subs if x.strip()])
        autosave(prof, new, "Reddit settings saved.")

    st.divider()
    # ------------------------------------------------------------------ manual
    st.subheader(t("Hand-collected reviews (CSV)"))
    csv_path = country.paths(prof)["manual_csv"]
    n_rows = len(pd.read_csv(csv_path)) if csv_path.exists() else 0
    st.caption(t("For marketplaces and anything that can't be collected automatically. "
                 "Columns: source, product, author, date, rating, text, url. Currently "
                 "{n} row(s). Uploads are added to it.", n=n_rows))
    up = st.file_uploader(t("Upload CSV"), type="csv", key=f"{code}_csv")
    if up is not None and st.button(t("Add these rows"), key=f"{code}_csv_add"):
        new_rows = pd.read_csv(up)
        if "text" not in new_rows.columns:
            st.error(t("The CSV needs a 'text' column."))
        else:
            csv_path.parent.mkdir(parents=True, exist_ok=True)
            old = pd.read_csv(csv_path) if csv_path.exists() else pd.DataFrame()
            pd.concat([old, new_rows], ignore_index=True).to_csv(csv_path, index=False)
            st.session_state["_flash"] = t("Added {n} rows. Run Collect to load them.",
                                           n=len(new_rows))
            st.rerun()

# ======================================================================== run
if section == "run":
    plan = []
    if prof["youtube"].get("enabled", True):
        plan.append("YouTube")
    plan += [w["name"] for w in prof.get("websites") or [] if w.get("enabled", True)]
    plan += [a["name"] for a in prof.get("custom_apis") or [] if a.get("enabled", True)]
    if prof["reddit"].get("enabled"):
        plan.append("Reddit")
    if country.paths(prof)["manual_csv"].exists():
        plan.append("CSV")
    st.caption(t("Sources for {name}: {list}", name=cname,
                 list=", ".join(plan) or t("none enabled")))

    missing = [label for ok, label in (
        (has(llm.key_env()), t("AI provider key")),
        (has("AUTHOR_SALT"), t("Author salt")),
        (has("ORG_NAME"), t("Organisation name")),
        (has("RESEARCH_CONTACT"), t("Contact email"))) if not ok]
    if missing:
        st.warning(t("Set these in Settings before running: {list}",
                     list=", ".join(missing)))

    busy = jobs.running(code)
    src_labels = {"all": t("All enabled sources"), "youtube": t("YouTube only"),
                  "web": t("Pasted websites only"), "api": t("Custom APIs only"),
                  "reddit": t("Reddit only"), "manual": t("Hand-collected CSV only")}
    c1, c2, c3 = st.columns(3)
    source = c1.selectbox(t("Collect from"), list(src_labels), format_func=src_labels.get,
                          key=f"{code}_run_src")
    lim = c2.selectbox(t("Max items per source"), LIMITS,
                       format_func=lambda n: f"{n:,}" if n else t("No limit"),
                       key=f"{code}_run_lim", help=t("Use a small number for a test run."))
    clim = c3.selectbox(t("Max documents to classify"), LIMITS,
                        format_func=lambda n: f"{n:,}" if n else t("All waiting"),
                        key=f"{code}_run_clim")
    if counts:
        st.caption(t("{n} documents are waiting for classification (about 20 per AI "
                     "call).", n=f"{counts['unclassified']:,}"))
    st.caption(t("Classify first runs the market check on new YouTube videos, so "
                 "videos made for other countries are left out before any are "
                 "classified."))

    b1, b2, b3, b4 = st.columns(4)
    actions = [
        (b1, "1 · Collect", ["collect", "--source", source] + (["--limit", str(lim)] if lim else [])),
        (b2, "2 · Classify", ["classify"] + (["--limit", str(clim)] if clim else [])),
        (b3, "3 · Export workbook", ["export"]),
        (b4, "Run all three", ["full"] + (["--limit", str(lim)] if lim else [])
         + (["--classify-limit", str(clim)] if clim else [])),
    ]
    for col, label, args in actions:
        if col.button(t(label), key=f"{code}_run_{label}", disabled=busy or bool(missing),
                      type="primary" if label == "Run all three" else "secondary"):
            jobs.start(code, label, args)
            st.rerun()

    @st.fragment(run_every="2s")
    def job_panel():
        s = jobs.status(code)
        if s is None:
            tail = jobs.log_tail(code)
            if tail:
                st.caption(t("Log of the last run"))
                st.code(tail, language=None)
            return
        if s["running"]:
            st.info(t("⏳ {job} running for {s} s…", job=t(s["label"]),
                      s=int(s["elapsed"])))
            if st.button(t("Stop"), key=f"{code}_stop"):
                jobs.stop(code)
        elif s["returncode"] == 0:
            st.success(t("✅ {job} finished.", job=t(s["label"])))
        else:
            st.error(t("{job} stopped (exit code {rc}). See the log below.",
                       job=t(s["label"]), rc=s["returncode"]))
        st.caption(t("The log is in English."))
        st.code(jobs.log_tail(code), language=None)

    job_panel()

    with st.expander(t("Clear data")):
        if not counts:
            st.caption(t("No data collected for {name} yet.", name=cname))
        else:
            labels = {
                "classification": t("Only the AI classification ({m} mentions). Collected "
                                    "posts stay; {d} documents will need classifying "
                                    "again, which uses the AI again.",
                                    m=f"{counts['mentions']:,}",
                                    d=f"{counts['classified_docs']:,}"),
                "all": t("All collected posts and results for {name}. Settings, search "
                         "terms and brands stay.", name=cname)}
            what = st.radio(t("What to clear"), list(labels), format_func=labels.get,
                            key=f"{code}_clear_what")
            st.caption(t("A copy goes to the backups folder first, so this can be undone."))
            if busy:
                st.caption(t("Stop the running job first."))
            sure = st.checkbox(t("Yes, clear it"), key=f"{code}_clear_sure")
            if st.button(t("Clear data"), key=f"{code}_clear", disabled=not sure or busy):
                stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
                if what == "classification":
                    dest = Path("backups") / f"{code}_classification_{stamp}"
                    db.copy_to(dest / country.paths(prof)["db"].name)
                    with db.connect() as con:
                        db.clear_classification(con)
                    msg = "Cleared the AI classification. A copy of the database is in {dir}."
                else:
                    dest = country.clear_data(prof)
                    msg = "Cleared all data for {name}. The files are in {dir}."
                st.session_state.pop(f"{code}_clear_sure", None)
                st.session_state["_flash"] = t(msg, name=cname, dir=dest.as_posix())
                st.rerun()

# ==================================================================== results
if section == "results":
    if not counts or not counts["mentions_in_scope"]:
        st.info(t("No classified mentions yet. Collect and classify on the Run tab."))
    else:
        df = analyse._frame()
        c1, c2, c3, c4 = st.columns(4)
        c1.metric(t("Documents"), f"{counts['documents']:,}")
        c2.metric(t("Classified"), f"{counts['classified_docs']:,}")
        c3.metric(t("Mentions"), f"{counts['mentions_in_scope']:,}")
        spot = counts["spot"]
        c4.metric(t("Spot-check"), f"{spot['pct_correct']}%" if spot["checked"]
                  else t("not done"),
                  help=t("Share of checked quotes a person judged correct."))

        import altair as alt
        vol = analyse.mention_volume(df)["overall"].copy()
        vol["label"] = [need_label(c) for c in analyse.CODE_ORDER]
        st.markdown(f"**{t('Mentions per WATERS need')}**")
        chart = (alt.Chart(vol).mark_bar(color=BAR_COLOR, cornerRadiusEnd=4, size=22)
                 .encode(x=alt.X("n:Q", title=t("Mentions")),
                         y=alt.Y("label:N", sort=None, title=None),
                         tooltip=[alt.Tooltip("label:N", title=t("Need")),
                                  alt.Tooltip("n:Q", title=t("Mentions")),
                                  alt.Tooltip("share_pct:Q", title=t("Share %"))])
                 .properties(height=280))
        st.altair_chart(chart)

        st.markdown(f"**{t('Net sentiment by brand and need')}** "
                    + t("(cells under n={n} suppressed)", n=analyse.MIN_CELL))
        st.dataframe(analyse.net_sentiment(df), hide_index=True)

        st.markdown(f"**{t('Evidence (verbatim quote, with English translation)')}**")
        st.dataframe(analyse.evidence_appendix(df).head(200), hide_index=True)

        with db.connect() as con:
            checked, checks = scope.summary(con), scope.frame(con)
        if checked["videos"]:
            with st.expander(t("Market check: {kept} of {n} YouTube videos kept",
                               kept=checked["videos"] - checked["excluded"],
                               n=checked["videos"])):
                st.caption(t("Judged by the AI from each video's title, channel and "
                             "comments. Comments from excluded videos are not analysed "
                             "but stay in the database."))
                if checked["unchecked"]:
                    st.warning(t("{n} video(s) not checked yet; they are still counted. "
                                 "Run Classify to check them.", n=checked["unchecked"]))
                st.dataframe(checks[["video", "in_scope", "verdict", "exclusion_note",
                                     "reason", "comments", "url"]], hide_index=True,
                             column_config={"url": st.column_config.LinkColumn("url")})

        # ------------------------------------------------------- spot check
        st.divider()
        st.subheader(t("Quality spot-check"))
        st.caption(t("Read a few random quotes and say whether the AI got the need and "
                     "the sentiment right. The share it got right is printed in the "
                     "workbook. Aim for at least 20 quotes; below about 80% correct, "
                     "treat the figures with caution."))
        if spot["checked"]:
            st.markdown(t("So far: **{ok} of {n} correct ({pct}%)**.", ok=spot["correct"],
                          n=spot["checked"], pct=spot["pct_correct"]))
        q_key, i_key, r_key = f"{code}_spot_q", f"{code}_spot_i", f"{code}_spot_r"
        queue = st.session_state.get(q_key)
        if not queue:
            if st.button(t("Check 20 random quotes"), type="primary", key=f"{code}_spot_go"):
                with db.connect() as con:
                    sample = db.spot_sample(con, 20)
                if sample:
                    st.session_state[q_key] = sample
                    st.session_state[i_key] = 0
                    st.session_state[r_key] = {"correct": 0, "wrong": 0}
                    st.rerun()
                st.info(t("Every quote has already been checked."))
        else:
            i = st.session_state[i_key]
            if i >= len(queue):
                r = st.session_state[r_key]
                n = r["correct"] + r["wrong"]
                st.success(t("Round finished: {ok} of {n} correct.", ok=r["correct"], n=n))
                if st.button(t("Done"), key=f"{code}_spot_done"):
                    for k in (q_key, i_key, r_key):
                        st.session_state.pop(k, None)
                    st.rerun()
            else:
                m = queue[i]
                st.progress(i / len(queue), text=t("Quote {i} of {n}", i=i + 1, n=len(queue)))
                with st.container(border=True):
                    st.markdown(t("The AI says this quote is about **{need}** and is "
                                  "**{sentiment}**:", need=need_label(m["waters_code"]),
                                  sentiment=sentiment_label(m["sentiment"])))
                    st.markdown(f"> {m['evidence_span']}")
                    if m["evidence_span_en"].strip() != m["evidence_span"].strip():
                        st.caption(t("English: {text}", text=m["evidence_span_en"]))
                    with st.expander(t("What this need means")):
                        st.write(CODE_DEFS_ZH.get(m["waters_code"], "") if LANG == "zh"
                                 else " ".join(classify.load_taxonomy()["codes"]
                                               [m["waters_code"]]["definition"].split()))
                    with st.expander(t("Full post")):
                        st.write(m["raw_text"][:3000])
                st.markdown(t("Is the need **and** the sentiment right?"))
                c1, c2, c3 = st.columns(3)

                def answer(verdict: str | None) -> None:
                    if verdict:
                        with db.connect() as con:
                            db.record_spot(con, m["mention_id"], verdict)
                        st.session_state[r_key][verdict] += 1
                    st.session_state[i_key] += 1

                c1.button(t("✓ Correct"), key=f"{code}_spot_ok_{i}", type="primary",
                          on_click=answer, args=("correct",))
                c2.button(t("✗ Wrong"), key=f"{code}_spot_no_{i}",
                          on_click=answer, args=("wrong",))
                c3.button(t("Skip"), key=f"{code}_spot_skip_{i}",
                          on_click=answer, args=(None,))

    st.divider()
    st.subheader(t("Workbooks"))
    out_dir = country.paths(prof)["outputs"]
    books = sorted(out_dir.glob("waters_sentiment_*.xlsx"), reverse=True) if out_dir.exists() else []
    if not books:
        st.caption(t("None yet. Use “Export workbook” on the Run tab."))
    for b in books[:5]:
        st.download_button(f"⬇ {b.name}", b.read_bytes(), file_name=b.name,
                           key=f"{code}_dl_{b.name}")

# =================================================================== settings
if section == "settings":
    st.caption(t("Stored in the local .env file on this computer only. Leave a key "
                 "field blank to keep the current value."))
    st.caption(t("Changes are saved automatically."))

    # ----------------------------------------------------------- organisation
    st.subheader(t("Organisation"))
    st.caption(t("Websites see who is collecting. Use your real organisation and a "
                 "monitored email address."))
    c1, c2 = st.columns(2)
    org = c1.text_input(t("Organisation name"), os.getenv("ORG_NAME", ""), key="set_org",
                        placeholder="e.g. Acme Research")
    contact = c2.text_input(t("Contact email"), os.getenv("RESEARCH_CONTACT", ""),
                            key="set_contact")
    st.caption(t("Websites will see: {ua}", ua=f"`{org or '…'}-ConsumerResearch/1.0 "
                                                f"(consumer sentiment research; contact "
                                                f"{contact or '…'})`"))
    env_autosave({"ORG_NAME": org.strip(), "RESEARCH_CONTACT": contact.strip()},
                 {k: os.getenv(k, "") for k in ("ORG_NAME", "RESEARCH_CONTACT")}, "Saved.")

    st.divider()
    # ------------------------------------------------------------ ai provider
    st.subheader(t("AI provider"))
    st.caption(t("Used to classify posts and to set up countries, websites and APIs. Any "
                 "listed provider works; pick one your organisation is allowed to use."))
    presets = list(llm.PROVIDERS)
    preset = st.selectbox(t("Provider"), presets, index=presets.index(llm.preset())
                          if llm.preset() in presets else 0,
                          format_func=lambda p: llm.PROVIDERS[p]["label"], key="set_preset")
    spec = llm.PROVIDERS[preset]
    same = preset == llm.preset()
    if spec["kind"] == "openai_compatible":
        base = st.text_input(t("Endpoint address"),
                             (llm.base_url() if same else spec["base_url"]),
                             disabled=preset != "custom", key=f"set_base_{preset}",
                             help=t("Filled in for known providers. For 'Other', paste "
                                    "the base address ending before /chat/completions."))
    else:
        base = ""
    key_name = "ANTHROPIC_API_KEY" if spec["kind"] == "anthropic" else "LLM_API_KEY"
    key_val = st.text_input(t("API key"), type="password", key=f"set_key_{preset}",
                            placeholder=t("set (hidden)") if same and has(key_name) else "")
    if same and has(key_name) and st.button(t("Clear saved key"), key="set_key_clear"):
        env_clear([key_name], [f"set_key_{p}" for p in presets], "Key cleared.")
    c1, c2 = st.columns(2)
    cur_cls = (os.getenv("CLASSIFY_MODEL") if same else "") or (spec["models"] or [""])[0]
    cls_opts = with_current(list(spec["models"]), cur_cls)
    cls_model = c1.selectbox(
        t("Classification model"), cls_opts,
        index=cls_opts.index(cur_cls) if cur_cls in cls_opts else None,
        accept_new_options=True, key=f"set_cls_{preset}",
        placeholder=t("Type the model name from the provider's docs"),
        help=t("Keep the same model across countries and over time, or results stop "
               "being comparable."))
    auto = t("Automatic")
    cur_help = (os.getenv("HELPER_MODEL") if same else "") or auto
    help_opts = with_current([auto] + list(spec["models"]), cur_help)
    help_model = c2.selectbox(t("Setup helper model"), help_opts,
                              index=help_opts.index(cur_help), accept_new_options=True,
                              key=f"set_help_{preset}",
                              help=t("Reads websites, maps APIs and drafts countries. "
                                     "Automatic: the provider's recommended setup model, "
                                     "or the classification model if it has none."))
    want = {"LLM_PRESET": preset,
            "LLM_BASE_URL": base.strip() if preset == "custom" else "",
            "CLASSIFY_MODEL": (cls_model or "").strip(),
            "HELPER_MODEL": "" if help_model in (auto, None) else help_model.strip()}
    # Compared with what is in effect, so opening the app on a fresh .env, where
    # the defaults apply, writes nothing.
    have = {"LLM_PRESET": llm.preset(), "LLM_BASE_URL": os.getenv("LLM_BASE_URL", ""),
            "CLASSIFY_MODEL": llm.classify_model(),
            "HELPER_MODEL": os.getenv("HELPER_MODEL", "")}
    if key_val.strip():  # a blank key field keeps the stored key
        want[key_name], have[key_name] = key_val.strip(), os.getenv(key_name, "")
    changed_model = (want["LLM_PRESET"], want["CLASSIFY_MODEL"]) != \
        (have["LLM_PRESET"], have["CLASSIFY_MODEL"])
    env_autosave(want, have,
                 "Changing provider or model: results are no longer comparable with "
                 "earlier runs. Do a new spot-check after classifying."
                 if changed_model else "AI provider saved.")
    if st.button(t("Test connection"), key="set_ai_test"):
        with st.spinner(t("Testing…")):
            try:
                st.success(t("Connected: {p} / {m}", p=llm.describe(), m=llm.ping()))
            except Exception as exc:
                st.error(err(exc))

    st.divider()
    # ---------------------------------------------------------------- youtube
    st.subheader("YouTube")
    st.caption(t("YouTube is blocked in mainland China. Run collection from a network "
                 "with approved international access (e.g. an overseas office)."))
    yt_key = st.text_input(t("YouTube Data API v3 key"), type="password", key="set_yt",
                           placeholder=t("set (hidden)") if has("YOUTUBE_API_KEY") else "",
                           help=t("Google Cloud Console → enable YouTube Data API v3 → "
                                  "Credentials"))
    if yt_key.strip():  # a blank key field keeps the stored key
        env_autosave({"YOUTUBE_API_KEY": yt_key.strip()},
                     {"YOUTUBE_API_KEY": os.getenv("YOUTUBE_API_KEY", "")}, "Saved.")
    c1, c2 = st.columns(2)
    if has("YOUTUBE_API_KEY") and c2.button(t("Clear saved key"), key="set_yt_clear"):
        env_clear(["YOUTUBE_API_KEY"], ["set_yt"], "Key cleared.")
    if c1.button(t("Test YouTube"), key="set_yt_test", disabled=not has("YOUTUBE_API_KEY")):
        import httpx
        try:
            r = httpx.get("https://www.googleapis.com/youtube/v3/i18nRegions",
                          params={"part": "snippet", "key": os.environ["YOUTUBE_API_KEY"]},
                          timeout=15)
            if r.status_code == 200:
                st.success(t("YouTube is reachable and the key works."))
            else:
                st.error(t("YouTube answered with error {status}. Check the key.",
                           status=r.status_code))
        except httpx.HTTPError:
            st.error(t("Could not reach YouTube from this network."))

    st.divider()
    # ------------------------------------------------------------------- salt
    st.subheader(t("Author salt"))
    if has("AUTHOR_SALT"):
        st.caption(t("✅ Set. Never change it: author pseudonyms would no longer link "
                     "across runs."))
        with st.expander(t("Delete author salt")):
            st.caption(t("Only for a fresh start. Authors stored so far keep their old "
                         "pseudonyms, and new posts by the same people will not link to "
                         "them. Collection stops until a new salt is generated."))
            sure = st.checkbox(t("I understand, delete the salt"), key="set_salt_sure")
            if st.button(t("Delete author salt"), key="set_salt_del", disabled=not sure):
                env_clear(["AUTHOR_SALT"], ["set_salt_sure"], "Author salt deleted.")
    else:
        st.caption(t("Usernames are scrambled with this secret before they are stored."))
        if st.button(t("Generate author salt")):
            env_set("AUTHOR_SALT", secrets.token_hex(32))
            st.session_state["_flash"] = t("Author salt generated.")
            st.rerun()
