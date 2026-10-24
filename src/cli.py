"""Command line entry point. Every command acts on one country (default sg).

    python -m src.cli web                          # open the web app
    python -m src.cli countries                    # list country profiles
    python -m src.cli draft-country "Malaysia"     # the AI model drafts a new profile
    python -m src.cli --country my detect <url>    # read a pasted page, --add to save
    python -m src.cli --country my init
    python -m src.cli --country my collect --source youtube --limit 50
    python -m src.cli --country my scope           # market check of YouTube videos
    python -m src.cli --country my classify --limit 200
    python -m src.cli --country my export
    python -m src.cli --country my full
    python -m src.cli inspect <url>       # manual helper for finding CSS selectors
"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import sys
from pathlib import Path

import yaml
from rich.console import Console
from rich.table import Table

from . import classify, collectors, country, db, export, scope

console = Console()
log = logging.getLogger("waters")


def setup_logging(verbose: bool = False) -> None:
    Path("logs").mkdir(exist_ok=True)
    fmt = "%(asctime)s %(levelname)-7s %(name)s :: %(message)s"
    handlers = [
        logging.StreamHandler(sys.stdout),
        logging.handlers.RotatingFileHandler("logs/pipeline.log", maxBytes=2_000_000,
                                             backupCount=3, encoding="utf-8"),
    ]
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO,
                        format=fmt, handlers=handlers)
    logging.getLogger("httpx").setLevel(logging.WARNING)


def show_counts() -> None:
    with db.connect() as con:
        c = db.counts(con)
    t = Table(title="Database", header_style="bold")
    t.add_column("Table"); t.add_column("Rows", justify="right")
    for k, v in c.items():
        t.add_row(k, f"{v:,}")
    console.print(t)


# ------------------------------------------------------------------- collection
def _seed_products(con, products: list[dict]) -> None:
    for prod in products:
        models = prod.get("models") or [None]
        for m in models:
            db.upsert_product(con, prod["brand"], m,
                              prod.get("category", "unknown"))


SOURCE_KINDS = ("youtube", "reddit", "web", "api", "manual")


def plan_sources(profile: dict) -> list[dict]:
    """Every enabled source in the profile as (kind, name, platform, opener).

    Openers are deferred so that one source missing its key reports and is
    skipped without stopping the others.
    """
    qs = country.queries(profile)
    plan = []
    yt = profile["youtube"]
    if yt.get("enabled", True):
        plan.append({"kind": "youtube", "name": "youtube", "platform": "youtube",
                     "url": "", "open": lambda: collectors.YouTubeCollector().collect(
                         qs, max_videos=yt.get("max_videos", 8),
                         max_comments=yt.get("max_comments", 100),
                         region=yt.get("region") or profile["code"].upper())})
    rd = profile["reddit"]
    if rd.get("enabled") and rd.get("subreddits"):
        plan.append({"kind": "reddit", "name": "reddit", "platform": "reddit",
                     "url": "", "open": lambda: collectors.RedditCollector().collect(
                         rd["subreddits"], [q for q, _ in qs],
                         limit_per_query=rd.get("limit_per_query", 25),
                         comments=rd.get("comments", True))})
    for w in profile.get("websites") or []:
        if not w.get("enabled", True) or not w.get("urls") or not w.get("selectors"):
            continue
        plan.append({"kind": "web", "name": w["name"], "platform": "forum",
                     "url": w["urls"][0],
                     "open": (lambda w=w: collectors.ForumCollector().collect(
                         w["name"], w["urls"], w["selectors"],
                         max_pages=w.get("max_pages", 20),
                         page_param=w.get("page_param") or None))})
    for a in profile.get("custom_apis") or []:
        if not a.get("enabled", True) or not a.get("mapping"):
            continue
        plan.append({"kind": "api", "name": a["name"], "platform": "api",
                     "url": a["endpoint"].split("?")[0],
                     "open": (lambda a=a: collectors.CustomAPICollector().collect(
                         a["name"], a["endpoint"], a["mapping"], qs,
                         auth=a.get("auth"), key_env=a.get("key_env")))})
    csv_path = country.paths(profile)["manual_csv"]
    if csv_path.exists():
        plan.append({"kind": "manual", "name": "manual_csv", "platform": "manual",
                     "url": "", "open": lambda: collectors.ManualImportCollector()
                     .collect(str(csv_path))})
    return plan


def do_collect(profile: dict, source: str, limit: int | None, dry_run: bool) -> None:
    matcher = collectors.build_matcher(profile["products"],
                                       profile.get("category_terms"))
    with db.connect() as con:
        _seed_products(con, profile["products"])

    source = "web" if source == "forum" else source
    chosen = [s for s in plan_sources(profile) if source in (None, "all", s["kind"])]
    if not chosen:
        console.print(f"[yellow]No enabled sources match '{source}' for "
                      f"{profile['name']}[/yellow]")
        return

    for spec in chosen:
        plat = spec["platform"]
        console.print(f"[bold cyan]Collecting[/bold cyan] {spec['name']} ({plat}) "
                      f"for {profile['name']}")
        try:
            stream = spec["open"]()
        except Exception as exc:
            console.print(f"[red]{spec['name']} unavailable: {exc}[/red]")
            continue

        new = seen = 0
        try:
            for item in stream:
                seen += 1
                if dry_run:
                    if seen <= 3:
                        console.print(f"  [dim]{item['raw_text'][:120]}...[/dim]")
                    if limit and seen >= limit:
                        break
                    continue
                brand, model, cat = matcher(item["raw_text"])
                with db.connect() as con:
                    sid = db.upsert_source(con, item.get("source_name", spec["name"]), plat,
                                           spec.get("url", ""))
                    pid = db.upsert_product(con, brand, model, cat) if brand else None
                    did = db.insert_document(
                        con, source_id=sid, product_id=pid,
                        raw_text=item["raw_text"], url=item.get("url", ""),
                        author=item.get("author"), posted_at=item.get("posted_at"),
                        rating=item.get("rating"))
                if did:
                    new += 1
                if seen % 100 == 0:
                    log.info("%s: seen %s, new %s", spec["name"], seen, new)
                if limit and seen >= limit:
                    break
        except collectors.YouTubeStop as exc:
            log.error("youtube stopped: %s", exc)
            console.print(f"[yellow]Stopped {spec['name']}: {exc}[/yellow]")
        except collectors.RobotsBlocked as exc:
            console.print(f"[yellow]Halted politely: {exc}[/yellow]")
        except Exception as exc:
            log.exception("collection error on %s", spec["name"])
            console.print(f"[red]Error on {spec['name']}: {exc}[/red]")
        console.print(f"  seen {seen}, new {new}, duplicates {seen - new}")
    show_counts()


def do_scope(profile: dict, recheck: bool = False) -> None:
    """Market check before classification, so off-market videos are never paid
    for. A failure leaves the videos in the analysis and is retried next time."""
    try:
        res = scope.run(profile, recheck=recheck)
    except Exception as exc:
        log.error("market check could not run: %s", exc)
        console.print(f"[red]Market check could not run: {exc}[/red]")
        return
    with db.connect() as con:
        s = scope.summary(con)
    if res["checked"] or res["failed"]:
        console.print(f"Market check: {res['checked']} video(s) judged, {res['kept']} "
                      f"kept, {res['excluded']} excluded, {res['failed']} failed")
    console.print(f"YouTube videos in scope: {s['videos'] - s['excluded']} of "
                  f"{s['videos']}")


def do_export(profile: dict) -> None:
    path = export.run(profile=profile)
    console.print(f"[green]Workbook written to {path}[/green]")
    with db.connect() as con:
        spot = db.spot_summary(con)
    if spot["checked"]:
        console.print(f"Spot-check: {spot['correct']}/{spot['checked']} correct "
                      f"({spot['pct_correct']}%)")
    else:
        console.print("[yellow]No spot-check yet; the workbook says so.[/yellow]")


# ------------------------------------------------------------------------- main
def main() -> None:
    # .env is loaded in src/__init__.py, which runs before the submodules above
    # bind their module-level constants. Loading it here would be too late.
    ap = argparse.ArgumentParser(prog="waters", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--country", default=country.DEFAULT_CODE,
                    help="country profile code, e.g. sg, my (see `countries`)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init")

    c = sub.add_parser("collect")
    c.add_argument("--source", default="all",
                   help="all | youtube | reddit | web | api | manual")
    c.add_argument("--limit", type=int)
    c.add_argument("--dry-run", action="store_true")

    sc = sub.add_parser("scope", help="market check of YouTube videos")
    sc.add_argument("--recheck", action="store_true",
                    help="judge every video again, not only new ones")

    cl = sub.add_parser("classify")
    cl.add_argument("--limit", type=int)
    cl.add_argument("--dry-run", action="store_true")

    sub.add_parser("analyse")
    sub.add_parser("export")
    sub.add_parser("full")
    sub.add_parser("counts")

    ins = sub.add_parser("inspect")
    ins.add_argument("url")

    sub.add_parser("countries")
    dc = sub.add_parser("draft-country", help="have the AI model draft a new profile")
    dc.add_argument("name")
    dc.add_argument("--save", action="store_true", help="write it to config/countries/")
    de = sub.add_parser("detect", help="work out how to read a pasted page")
    de.add_argument("url")
    de.add_argument("--add", action="store_true", help="save it to the country profile")
    sub.add_parser("web", help="open the web app")

    a = ap.parse_args()
    setup_logging(a.verbose)

    if a.cmd == "web":
        import subprocess
        sys.exit(subprocess.call([sys.executable, "-m", "streamlit", "run", "webapp.py"]))

    if a.cmd == "countries":
        t = Table(title="Country profiles", header_style="bold")
        t.add_column("Code"); t.add_column("Country"); t.add_column("Database")
        for c in country.list_countries():
            t.add_row(c["code"], c["name"], str(country.paths(country.load(c["code"]))["db"]))
        console.print(t)
        return

    if a.cmd == "draft-country":
        from . import autodetect
        prof = autodetect.draft_country(a.name)
        console.print(yaml.safe_dump(prof, sort_keys=False, allow_unicode=True))
        if a.save:
            if not country.valid_code(prof["code"]):
                sys.exit(f"Drafted code {prof['code']!r} is not usable; save it by hand.")
            if (country.COUNTRY_DIR / f"{prof['code']}.yaml").exists():
                sys.exit(f"{prof['code']}.yaml already exists; not overwritten.")
            console.print(f"[green]Saved {country.save(prof)}[/green]")
        return

    profile = country.load(a.country)
    country.activate(profile)
    if a.cmd not in ("detect", "inspect"):
        db.init()  # idempotent; migrates databases made by older versions

    if a.cmd == "detect":
        from . import autodetect
        try:
            res = autodetect.detect_site(
                a.url, country.find_website(profile, autodetect.site_name(a.url)))
        except autodetect.DetectError as exc:
            sys.exit(str(exc))
        console.print(f"[bold]{res['name']}[/bold]  {res['reason']}")
        console.print(res["selectors"], f"page_param={res['page_param']!r}")
        for post in res["sample"][:3]:
            console.print(f"  [dim]{post['raw_text'][:160]}[/dim]")
        for prob in res["problems"]:
            console.print(f"[yellow]  problem: {prob}[/yellow]")
        if a.add:
            if res["problems"]:
                sys.exit("Not saved: fix the problems above first.")
            country.add_website(profile, a.url, res)
            console.print(f"[green]Added to {country.save(profile)}[/green]")
        return

    if a.cmd == "init":
        db.init()
        console.print("[green]Schema created[/green]")
        show_counts()

    elif a.cmd == "counts":
        show_counts()

    elif a.cmd == "collect":
        db.init()
        do_collect(profile, a.source, a.limit, a.dry_run)

    elif a.cmd == "scope":
        do_scope(profile, a.recheck)

    elif a.cmd == "classify":
        db.init()
        if not a.dry_run:
            do_scope(profile)
        try:
            res = classify.run(limit=a.limit, dry_run=a.dry_run,
                               country_name=profile["name"])
        except Exception as exc:
            sys.exit(str(exc))
        console.print(res)

    elif a.cmd == "analyse":
        from . import analyse as an
        frames = an.run_all()
        for name, df in frames.items():
            console.print(f"[bold]{name}[/bold]  ({len(df)} rows)")

    elif a.cmd == "export":
        do_export(profile)

    elif a.cmd == "full":
        db.init()
        do_collect(profile, "all", None, False)
        do_scope(profile)
        console.print(classify.run(country_name=profile["name"]))
        do_export(profile)

    elif a.cmd == "inspect":
        from selectolax.lexbor import LexborHTMLParser as HTMLParser
        col = collectors.BaseCollector()
        html = col.fetch(a.url)
        tree = HTMLParser(html)
        console.print("[bold]Candidate post containers[/bold] "
                      "(class names appearing 5+ times):")
        from collections import Counter
        classes = Counter()
        for node in tree.css("div, article, li, section"):
            cls = node.attributes.get("class")
            if cls:
                classes[cls.strip()] += 1
        for cls, n in classes.most_common(25):
            if n >= 5:
                console.print(f"  {n:>4}  .{cls.replace(' ', '.')}")


if __name__ == "__main__":
    main()
