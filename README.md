# WATERS Sentiment Engine

Collects public consumer discussion about countertop water dispensers, water purifiers
and filter jugs in any country, codes each opinion against the WATERS needs framework
with its sentiment, and produces an Excel workbook of the results.

The web interface works in English or Chinese (switch at the top of the sidebar).
中文说明见 [README_zh.md](README_zh.md)。

---

## Quick start

Needs Python 3.9 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m src.cli web                # opens http://localhost:8501
```

Keep the terminal open while using the site; `Ctrl+C` stops it.

### First-time setup (Settings tab)

| Setting | Why |
|---|---|
| **Organisation name + contact email** | Every website the tool visits sees who is collecting and how to reach them. Required. |
| **AI provider + API key** | Classifies posts and helps set up countries and websites. Choose Claude, Qwen, DeepSeek, Zhipu GLM, Doubao or any OpenAI-compatible endpoint. Use **Test connection** after saving. |
| **YouTube Data API v3 key** | The default source. Google Cloud Console → enable *YouTube Data API v3* → Credentials. Use **Test YouTube**. |
| **Author salt** | Click **Generate**. Usernames are scrambled with it before storage. Never change it afterwards. |

Everything is stored in a local `.env` file. It holds secrets: never share or commit it.

### Running a country

1. **Sidebar:** pick a country, or **Add a country** → **Draft with AI** (or **Start empty**).
2. **1 · Country setup:** check the languages, search terms and brand list, then save.
   A drafted profile is a starting point: verify the brands before collecting.
3. **2 · Sources:** YouTube is on by default. Optionally paste forum/review page
   addresses, a custom API, Reddit credentials, or upload a hand-collected CSV.
4. **3 · Run:** **Run all three** (collect → classify → export). Classify first runs
   the market check on new YouTube videos. It runs in the background; the log
   updates live.
5. **4 · Results:** charts, evidence quotes, the **quality spot-check**, and the
   workbook download.

---

## AI providers

| Preset | Endpoint (filled in automatically) |
|---|---|
| Claude (Anthropic) | Anthropic SDK |
| Qwen 通义千问 | `https://dashscope.aliyuncs.com/compatible-mode/v1` |
| DeepSeek | `https://api.deepseek.com` |
| Zhipu GLM 智谱 | `https://open.bigmodel.cn/api/paas/v4` |
| Doubao 豆包 | `https://ark.cn-beijing.volces.com/api/v3` (type your model/endpoint ID) |
| Other | any OpenAI-compatible base address |

- **Keep one model.** Results from different models are not comparable. Use the same
  provider and model across countries and over time; after any change, do a new
  spot-check.
- **Strict JSON output** is used where the provider supports it. Otherwise the tool falls
  back to plain JSON mode automatically. Every coded opinion must still quote the post
  word for word, and any quote not found in the post is discarded, whichever provider
  is used.
- **Check your eligibility.** Anthropic does not serve mainland China and restricts
  companies controlled from unsupported regions. Confirm with your legal team which
  providers your organisation may use.

## Running from mainland China

- **YouTube is not reachable from mainland China.** Run collection from a network with
  approved international access, such as an overseas office.
- **Domestic Chinese platforms** (Xiaohongshu, Douyin, Zhihu, JD, Tmall) have no open
  API and use strong anti-bot protection. The tool does not scrape them, and marketplace
  addresses are refused. Use a licensed data vendor's API through **Custom API**, or the
  CSV upload.
- **Cross-border data.** Moving collected posts across borders and sending them to an AI
  provider may need approval under China's PIPL and the source country's data law, even
  though usernames are hashed.

---

## Sources

| Source | How to add | Notes |
|---|---|---|
| YouTube | on by default | uses the country's region and each query's language |
| Websites | paste page addresses on *Sources* | the AI works out how to read each site once; you see sample posts before saving |
| Custom API | endpoint + key on *Sources* | `{query}` in the endpoint is filled with each search term; the AI maps the JSON fields; first page only |
| Reddit | client ID/secret on *Sources* | official API |
| CSV | upload on *Sources* | columns: source, product, author, date, rating, text, url |

Pages built by JavaScript in the browser cannot be read; use the CSV upload for those.

## Market check (YouTube)

YouTube search ignores market boundaries: a search run for Singapore also returns
American pitcher reviews and Indian RO explainers. Before classifying, the AI judges
each new video from its title, channel, description and a sample of its comments:

| Verdict | Analysed? |
|---|---|
| target market: about this country, or its commenters are from there | yes |
| other market: made for another country (the country is recorded) | no |
| global, no country signal: e.g. a general science explainer | no, unless **Also keep videos with no sign of any one country** is on (Sources tab) |
| wrong category: air purifiers, aquariums, industrial filters… | no |
| off-topic | no |

- Runs automatically, with no manual step. Each video is judged once; new videos are
  judged on the next Classify. `python -m src.cli --country sg scope --recheck`
  judges every video again.
- Nothing is deleted. Excluded comments stay in the database and are listed in
  `Raw_Documents.csv`. The workbook's **Market_Check** sheet lists every video with
  its verdict and reason, and the README sheet counts the exclusions.
- Classification skips excluded videos, so they cost nothing to classify. Comments
  classified before their video was excluded are kept but no longer analysed.
- Uses the setup helper model (Settings). On Claude that is Opus; one call per 10
  videos.
- Only YouTube is checked: websites, APIs, Reddit and CSVs are sources a person
  chose.

## Quality spot-check

On **Results**, click **Check 20 random quotes**. For each quote the tool shows the need
and sentiment the AI assigned, and you click ✓ correct or ✗ wrong. The share judged
correct appears on the Results tab and in the workbook's README sheet. Aim for at least
20 quotes per country. Below about 80% correct, treat the figures with caution, and
consider a different model or clearer brand and search settings.

The reviewer sees the AI's answer, so this measures whether the coding holds up on
review. It is not an independent second coding.

---

## Method

- **The unit is the mention, not the post.** One post can raise several needs, each
  with its own sentiment ("heats fast but the tank grows mould").
- **Every mention needs a verbatim quote** from the post. Mentions whose quote does not
  appear in the post are discarded; this is the guard against invented codes.
- **Languages.** Posts are coded in their original language. The quote stays verbatim;
  an English translation sits beside it for readers.
- **Thin cells are suppressed.** Any brand × need cell with fewer than `MIN_CELL`
  (default 10) mentions shows "insufficient data" rather than an unstable percentage.
- **Brand attribution is fuzzy.** Posts that name no brand land under "Unattributed".

## Ethics and data

- robots.txt is checked and respected on every site, including its Crawl-delay.
  Collection stops on any 403, 429 or disallow.
- At least 2 seconds between requests to the same site, with backoff on errors.
- Anti-bot measures are never circumvented: no CAPTCHA solving, no logins.
- Marketplace pages (Shopee, Lazada, Amazon, Taobao, Tmall, JD…) are refused.
- Usernames are salted-hashed at ingestion and never stored in raw form.
- API keys live only in the local `.env`, never in country profiles.
- Reading a pasted site sends an outline of that one public page (including visible
  display names) to the AI provider, once per site.

---

## Command line

Every web action is also a command (`--country` defaults to `sg`):

```bash
python -m src.cli countries
python -m src.cli draft-country "Thailand" --save
python -m src.cli --country th detect <page-url> --add
python -m src.cli --country th collect --source youtube --limit 50
python -m src.cli --country th scope         # market check (classify runs it too)
python -m src.cli --country th classify
python -m src.cli --country th export
python -m src.cli --country th full          # all three (--limit N, --classify-limit N)
```

## Layout

```
webapp.py   the web app (English / 中文)
config/     waters.yaml (taxonomy, shared), countries/<code>.yaml (one per country)
src/        llm.py (AI providers), i18n.py (Chinese text), collectors.py,
            classify.py, scope.py (market check), analyse.py, export.py, db.py, country.py,
            autodetect.py (AI-assisted setup), jobs.py, cli.py
data/       countries/<code>/waters.db, raw/ (page cache)      created on first run
outputs/    <code>/ workbooks and CSVs                          created on first run
tests/      pytest suite: python -m pytest -q
```
