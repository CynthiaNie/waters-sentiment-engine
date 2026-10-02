"""Core tests. Run with: pytest -q"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault("AUTHOR_SALT", "test-salt-do-not-use-in-production")

from src import analyse, classify, collectors, db  # noqa: E402


@pytest.fixture()
def con(tmp_path):
    path = tmp_path / "t.db"
    db.init(path)
    with db.connect(path) as c:
        yield c


# ------------------------------------------------------------------ dedup
def test_content_hash_normalises_whitespace_and_case():
    assert db.content_hash("Hello  World") == db.content_hash("hello world")


def test_content_hash_ignores_url_so_crossposts_dedup():
    # Same text at two URLs must collide: crossposts are one document.
    assert db.content_hash("same review", "url1") == db.content_hash("same review", "url2")


def test_duplicate_document_is_rejected(con):
    sid = db.upsert_source(con, "s", "reddit")
    first = db.insert_document(con, source_id=sid, raw_text="The tank grows mould")
    second = db.insert_document(con, source_id=sid, raw_text="the   TANK grows mould")
    assert first is not None
    assert second is None, "normalised duplicate should be rejected"


def test_empty_document_rejected(con):
    sid = db.upsert_source(con, "s", "reddit")
    assert db.insert_document(con, source_id=sid, raw_text="   ") is None


# -------------------------------------------------------- pseudonymisation
def test_author_hash_is_stable_and_not_reversible():
    h = db.author_hash("some_user")
    assert h == db.author_hash("some_user")
    assert "some_user" not in h
    assert len(h) == 32


def test_author_hash_requires_salt(monkeypatch):
    monkeypatch.delenv("AUTHOR_SALT", raising=False)
    with pytest.raises(RuntimeError):
        db.author_hash("someone")


# ------------------------------------------------- classifier output contract
def test_evidence_span_must_appear_verbatim():
    text = "It heats fast but the tank started growing mould after two months."
    good = {"waters_code": "R", "sentiment": "negative",
            "evidence_span": "tank started growing mould"}
    invented = {"waters_code": "R", "sentiment": "negative",
                "evidence_span": "the filter was expensive"}
    assert classify._valid(good, text)
    assert not classify._valid(invented, text), "hallucinated spans must be dropped"


def test_invalid_codes_rejected():
    text = "fast heating"
    assert not classify._valid(
        {"waters_code": "Z", "sentiment": "positive", "evidence_span": "fast heating"}, text)
    assert not classify._valid(
        {"waters_code": "A", "sentiment": "delighted", "evidence_span": "fast heating"}, text)


def test_json_extraction_survives_markdown_fences():
    raw = '```json\n{"results":[{"doc_id":1,"mentions":[]}]}\n```'
    assert classify._extract_json(raw)["results"][0]["doc_id"] == 1


# --------------------------------------------------------- product matching
def test_matcher_prefers_the_most_specific_match():
    match = collectors.build_matcher([
        {"brand": "novita", "category": "countertop_dispenser",
         "models": ["W29", "HydroCube"]},
        {"brand": "Brita", "category": "jug_filter", "models": ["Marella"]},
    ])
    assert match("my novita W29 is great")[:2] == ("novita", "W29")
    assert match("bought a novita last year")[:2] == ("novita", None)
    assert match("Brita Marella jug")[2] == "jug_filter"


def test_matcher_falls_back_to_category_hint():
    match = collectors.build_matcher([{"brand": "novita", "category": "countertop_dispenser"}])
    assert match("any decent filter jug in SG?")[2] == "jug_filter"
    assert match("looking for an instant hot water dispenser")[2] == "countertop_dispenser"
    assert match("unrelated post about cats")[2] == "unknown"


# ------------------------------------------------------------------ analysis
def test_suppression_hides_thin_cells():
    import pandas as pd
    df = pd.DataFrame({"n": [3, 50], "net_sentiment": [1.0, 0.2]})
    out = analyse._suppress(df)
    assert pd.isna(out.loc[0, "net_sentiment"]), "n<10 must be suppressed"
    assert out.loc[1, "net_sentiment"] == 0.2
    assert "insufficient data" in out.loc[0, "note"]


def test_mentions_are_many_per_document(con):
    sid = db.upsert_source(con, "s", "reddit")
    did = db.insert_document(con, source_id=sid,
                             raw_text="Heats fast but the tank grows mould")
    n = db.insert_mentions(con, did, [
        {"waters_code": "A", "sentiment": "positive", "intensity": 2,
         "evidence_span": "Heats fast", "confidence": 0.9},
        {"waters_code": "R", "sentiment": "negative", "intensity": 3,
         "evidence_span": "the tank grows mould", "confidence": 0.95},
    ], "test-model")
    assert n == 2
    assert db.counts(con)["mentions"] == 2


# ------------------------------------------------------------------- robots
def test_unreachable_robots_txt_is_treated_as_allowed():
    # Policy: robots.txt that cannot be fetched -> proceed (and log). A parsed
    # disallow, by contrast, raises RobotsBlocked in fetch() and is never retried.
    col = collectors.BaseCollector(delay=0)
    col._robots["unreachable.example"] = None
    assert col.robots_ok("https://unreachable.example/x") is True


def test_parsed_disallow_blocks(monkeypatch):
    import urllib.robotparser as rp
    parser = rp.RobotFileParser()
    parser.parse(["User-agent: *", "Disallow: /"])
    col = collectors.BaseCollector(delay=0)
    col._robots["blocked.example"] = parser
    assert col.robots_ok("https://blocked.example/anything") is False


# ------------------------------------------------------- multi-country engine
from src import autodetect, country  # noqa: E402


def test_result_schema_requires_only_declared_properties():
    # A required key missing from properties (e.g. a typo such as "in tensity")
    # makes every classification call fail.
    def walk(node):
        if isinstance(node, dict):
            if "required" in node and "properties" in node:
                assert set(node["required"]) <= set(node["properties"]), node["required"]
            for v in node.values():
                walk(v)
    walk(classify.RESULT_SCHEMA)
    walk(autodetect.SELECTOR_SCHEMA)
    walk(autodetect.MAPPING_SCHEMA)
    walk(autodetect.DRAFT_SCHEMA)


def test_system_prompt_names_the_country_and_keeps_spans_verbatim():
    tax = classify.load_taxonomy()
    p = classify.system_prompt(tax, "Malaysia")
    assert "in Malaysia" in p and "Singapore" not in p
    assert "never translate it" in p


def test_matcher_finds_local_script_aliases_without_word_boundaries():
    match = collectors.build_matcher(
        [{"brand": "Xiaomi", "category": "countertop_dispenser", "models": ["S1"],
          "aliases": ["小米"]}],
        {"jug_filter": ["滤水壶", "penapis air jag"]})
    assert match("我买了小米净水器")[:2] == ("Xiaomi", None)
    assert match("my xiaomi s1 heats fast")[:2] == ("Xiaomi", "S1")
    assert match("the xiaomix blender")[0] is None, "must not match inside a Latin word"
    assert match("这个滤水壶不错")[2] == "jug_filter"
    assert match("beli penapis air jag murah")[2] == "jug_filter"


def test_marketplaces_are_refused():
    assert collectors.is_marketplace("https://shopee.com.my/product/1")
    assert collectors.is_marketplace("https://www.lazada.sg/x")
    assert not collectors.is_marketplace("https://forum.lowyat.net/topic/1")
    with pytest.raises(autodetect.DetectError):
        autodetect.detect_site("https://www.amazon.com/review/x")


def test_page_param_keeps_existing_query():
    assert collectors.with_param("https://f.x/t/1/", "page", 2) == "https://f.x/t/1/?page=2"
    assert collectors.with_param("https://f.x/t?id=3&page=1", "page", 2) == \
        "https://f.x/t?id=3&page=2"


def test_parse_posts_strips_quotes_and_follows_next_link():
    html = ('<div class="p"><span class="a">ann</span><time datetime="2026-01-02">'
            'Yesterday</time><div class="b"><blockquote>quoted words</blockquote>'
            'my own words</div></div><div class="p"><div class="b">second</div></div>'
            '<a class="nx" href="?page=2">Next</a>')
    sel = {"post": ".p", "body": ".b", "author": ".a", "date": "time",
           "strip": "blockquote"}
    posts = collectors.parse_posts(html, sel)
    assert [p["raw_text"] for p in posts] == ["my own words", "second"]
    assert posts[0]["posted_at"] == "2026-01-02" and posts[0]["author"] == "ann"
    assert collectors.next_page_url(html, "a.nx", "https://f.x/t") == "https://f.x/t?page=2"


def test_check_selectors_flags_a_too_broad_post_selector():
    html = '<div class="all"><p>one long post body</p><p>another post body</p></div>'
    _, problems = autodetect.check_selectors(html, {"post": ".all", "body": ".all"})
    assert problems


def test_skeleton_collapses_repeated_siblings():
    html = "<ul>" + "".join(f'<li class="post">post {i}</li>' for i in range(50)) + "</ul>"
    sk = autodetect.skeleton(html)
    assert sk.count('class="post"') == 4 and "more <li> siblings" in sk


def test_custom_api_mapping_reads_dot_paths():
    data = {"data": {"reviews": [
        {"body": "great taste", "user": {"name": "a"}, "stars": "4"},
        {"body": "", "user": {"name": "b"}},
        {"body": "tank mould", "user": {"name": "c"}, "stars": None}]}}
    items = collectors.CustomAPICollector.items(
        data, {"items": "data.reviews", "text": "body", "author": "user.name",
               "rating": "stars"})
    assert [i["raw_text"] for i in items] == ["great taste", "tank mould"]
    assert items[0]["rating"] == 4.0 and items[1]["rating"] is None
    urls = collectors.CustomAPICollector.build_urls(
        "https://api.x/s?q={query}", [("air panas", "ms")])
    assert urls == ["https://api.x/s?q=air+panas"]


def test_profile_round_trip(tmp_path, monkeypatch):
    monkeypatch.setattr(country, "COUNTRY_DIR", tmp_path)
    prof = {"code": "th", "name": "Thailand", "languages": ["th", "en"],
            "queries": ["เครื่องกรองน้ำ", {"text": "water dispenser", "lang": "en"}],
            "products": [{"brand": "Brita", "category": "jug_filter",
                          "models": [], "aliases": []}]}
    country.save(prof)
    back = country.load("th")
    assert back["youtube"]["region"] == "TH"
    assert country.queries(back) == [("เครื่องกรองน้ำ", "th"), ("water dispenser", "en")]
    assert country.paths(back)["db"].as_posix() == "data/countries/th/waters.db"
    with pytest.raises(ValueError):
        country.load("../etc")


def test_example_profile_loads_with_per_country_paths():
    sg = country.load("sg")
    p = country.paths(sg)
    assert p["db"].as_posix() == "data/countries/sg/waters.db"
    assert sg["websites"][0]["selectors"]["post"] == ".message-inner"
    assert ("新加坡 饮水机 推荐", "zh") in country.queries(sg)


def test_translation_is_stored_and_old_databases_migrate(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    old = sqlite3.connect(path)
    old.executescript(db.SCHEMA.replace("evidence_span_en TEXT,", ""))
    old.close()
    db.init(path)  # must add the missing column
    with db.connect(path) as c:
        sid = db.upsert_source(c, "s", "youtube")
        did = db.insert_document(c, source_id=sid, raw_text="air RO tak sedap")
        db.insert_mentions(c, did, [{"waters_code": "T", "sentiment": "negative",
                                     "evidence_span": "air RO tak sedap",
                                     "evidence_span_en": "RO water tastes bad",
                                     "confidence": 0.9}], "m")
        db.set_language(c, did, "MS")
        row = c.execute("SELECT m.evidence_span_en, d.language FROM mentions m "
                        "JOIN documents d ON d.id = m.document_id").fetchone()
    assert tuple(row) == ("RO water tastes bad", "ms")


# --------------------------------------- providers, translation, spot-check
import ast  # noqa: E402
import re  # noqa: E402

from src import i18n, llm  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def _ui_strings() -> list[str]:
    """Every string the web app passes through t(), plus engine error templates."""
    out = []
    tree = ast.parse((ROOT / "webapp.py").read_text(encoding="utf-8"))
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and n.args and isinstance(n.args[0], ast.Constant) \
                and isinstance(n.args[0].value, str):
            name = getattr(n.func, "id", getattr(n.func, "attr", ""))
            if name == "t":
                out.append(n.args[0].value)
            if name == "save" and len(n.args) > 1 and isinstance(n.args[1], ast.Constant):
                out.append(n.args[1].value)
    for f in ("src/llm.py", "src/autodetect.py"):
        for n in ast.walk(ast.parse((ROOT / f).read_text(encoding="utf-8"))):
            if isinstance(n, ast.Call) and getattr(n.func, "id", "") in (
                    "LLMError", "DetectError", "problem") and n.args \
                    and isinstance(n.args[0], ast.Constant):
                out.append(n.args[0].value)
    return out


def test_every_interface_string_has_a_chinese_translation():
    missing = [s for s in _ui_strings() if s not in i18n.ZH]
    assert not missing, missing


def test_translations_keep_the_same_placeholders():
    ph = re.compile(r"\{(\w*)\}")
    bad = [k for k, v in i18n.ZH.items() if sorted(ph.findall(k)) != sorted(ph.findall(v))]
    assert not bad, bad


def test_user_agent_names_the_organisation(monkeypatch):
    monkeypatch.setenv("ORG_NAME", "Acme Research")
    monkeypatch.setenv("RESEARCH_CONTACT", "research@example.com")
    ua = collectors.user_agent()
    assert ua.startswith("Acme-Research-ConsumerResearch/1.0")
    assert "research@example.com" in ua


def test_openai_compatible_provider_falls_back_to_json_object(monkeypatch):
    import httpx
    monkeypatch.setenv("LLM_PRESET", "qwen")
    monkeypatch.setenv("LLM_API_KEY", "k")
    monkeypatch.delenv("LLM_BASE_URL", raising=False)
    llm._NO_SCHEMA.clear()
    calls = []

    def fake_post(url, json=None, headers=None, timeout=None):
        calls.append(json["response_format"]["type"])
        if json["response_format"]["type"] == "json_schema":
            return httpx.Response(400, text="response_format not supported")
        assert "JSON Schema" in json["messages"][0]["content"]
        assert headers["Authorization"] == "Bearer k"
        body = {"choices": [{"finish_reason": "stop",
                             "message": {"content": '```json\n{"ok": true}\n```'}}]}
        return httpx.Response(200, json=body)

    monkeypatch.setattr(httpx, "post", fake_post)
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
    assert llm.complete_json("s", "u", schema, model="qwen-plus") == {"ok": True}
    assert llm.complete_json("s", "u", schema, model="qwen-plus") == {"ok": True}
    assert calls == ["json_schema", "json_object", "json_object"], "fallback remembered"
    assert llm.base_url() == "https://dashscope.aliyuncs.com/compatible-mode/v1"


def test_provider_errors_are_translatable(monkeypatch):
    monkeypatch.setenv("LLM_PRESET", "deepseek")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    with pytest.raises(llm.LLMError) as exc:
        llm.complete_json("s", "u", {}, model="deepseek-chat")
    e = exc.value
    assert e.template in i18n.ZH and "DeepSeek" in str(e)


def test_spot_check_records_and_summarises(con):
    sid = db.upsert_source(con, "s", "youtube")
    did = db.insert_document(con, source_id=sid, raw_text="heats fast but tank grows mould")
    db.insert_mentions(con, did, [
        {"waters_code": "A", "sentiment": "positive", "evidence_span": "heats fast"},
        {"waters_code": "R", "sentiment": "negative", "evidence_span": "tank grows mould"},
    ], "m")
    sample = db.spot_sample(con, 20)
    assert len(sample) == 2 and sample[0]["raw_text"]
    db.record_spot(con, sample[0]["mention_id"], "correct")
    db.record_spot(con, sample[1]["mention_id"], "wrong")
    db.record_spot(con, sample[1]["mention_id"], "correct")  # changed mind: one verdict
    assert db.spot_summary(con) == {"checked": 2, "correct": 2, "pct_correct": 100.0,
                                    "last_checked": db.spot_summary(con)["last_checked"]}
    assert db.spot_sample(con, 20) == [], "checked quotes are not offered again"
