"""Chinese (Simplified) text for the web app.

English strings are the keys: webapp.t("Save") returns ZH["Save"] when the
interface is in Chinese and the English text otherwise, so a missing entry
degrades to English instead of breaking. tests/test_core.py checks that every
string the app shows has an entry here with the same {placeholders}.
"""

# WATERS needs and sentiments, for the spot-check and the chart.
CODE_NAMES_ZH = {
    "W": "健康安全", "A": "即取即用", "T": "口感品质", "E": "空间安装",
    "R": "信赖保障", "S": "社交形象", "COST": "成本", "OTHER": "其他",
}
CODE_DEFS_ZH = {
    "W": "使用者或其家人的健康与安全，重点是保护某人（如儿童、老人、婴儿冲奶、防烫、童锁），"
         "而不是方便或口感。担心机器本身的卫生（发霉、水箱、出水口）属于 R。",
    "A": "速度与随取随用、免去等待，包括出水量和容量不足导致的等待或断水。",
    "T": "感官品质与精准度：味道、气味、温度是否准确，以及水是否适合冲咖啡、泡茶等特定用途。",
    "E": "物理空间与安装：占地、台面空间、尺寸、重量、打孔、接水管、改电路、是否便于搬动。"
         "外观只有在关乎「放不放得下」时才算这里。",
    "R": "卫生、品牌信誉与售后信心：发霉、细菌、清洗水箱、换滤芯、维修服务、保修、认证、"
         "可靠性、客服。它决定选哪个品牌，而不是要不要买这类产品。",
    "S": "待客、展示、身份与审美表达：机器在别人眼中的样子，或是否符合主人对家的品味。",
    "COST": "价格、性价比与使用成本（如滤芯费用、订阅、促销）。",
    "OTHER": "与品类相关但不属于以上任何一类（如噪音、智能 App、能耗、配送体验）。",
}
SENTIMENT_ZH = {"positive": "正面", "negative": "负面", "neutral": "中性",
                "mixed": "褒贬参半"}

ZH: dict[str, str] = {
    # ---------------------------------------------------------------- general
    "Automatic": "自动",
    "No country profiles found in config/countries/.": "在 config/countries/ 中找不到任何国家配置。",
    "💧 WATERS engine": "💧 WATERS 舆情引擎",
    "Country": "国家",
    "**Setup** (in Settings)": "**配置状态**（在「设置」中修改）",
    "1 · Country setup": "1 · 国家设置",
    "2 · Sources": "2 · 数据来源",
    "3 · Run": "3 · 运行",
    "4 · Results": "4 · 结果",
    "⚙ Settings": "⚙ 设置",
    "Saved.": "已保存。",
    "Save": "保存",
    "Remove": "移除",
    "Pause": "暂停",
    "Resume": "恢复",
    "Stop": "停止",
    "Done": "完成",
    "Skip": "跳过",
    "none": "无",
    "Documents": "文档数",
    "Mentions": "提及数",
    "Classified": "已分类",
    "Choose or type to search…": "选择或输入搜索…",
    "set (hidden)": "已设置（隐藏）",
    "Testing…": "测试中…",

    # ---------------------------------------------------------------- sidebar
    "➕ Add a country": "➕ 添加国家",
    "The AI drafts the languages, local search terms and a brand list. You review and edit it before anything is collected.":
        "AI 会起草该国的语言、本地搜索词和品牌清单。采集前请先检查并修改。",
    "Draft with AI": "用 AI 起草",
    "Start empty": "从空白开始",
    "No AI call: you fill in brands and search terms.": "不调用 AI：品牌和搜索词由您自己填写。",
    "Drafting a profile for {name}…": "正在为{name}起草配置…",
    "Created {name}. Review the brands and search terms below before collecting.":
        "已创建{name}。采集前请检查下方的品牌和搜索词。",
    "{n} documents waiting to be classified": "{n} 篇文档等待分类",
    "No data collected for {name} yet.": "{name}尚未采集任何数据。",
    "AI provider: {p}": "AI 服务商：{p}",
    "YouTube key": "YouTube 密钥",
    "Organisation name and contact": "机构名称和联系邮箱",
    "Author salt": "作者加密盐",

    # ---------------------------------------------------------- country setup
    "Market": "市场",
    "Languages (main one first)": "语言（主要语言放第一位）",
    "Used to search YouTube in the right language. Posts in any language are coded either way.":
        "用于以正确的语言搜索 YouTube。无论帖子是什么语言，都会被分类。",
    "YouTube region": "YouTube 地区",
    "Currency": "货币",
    "Search terms": "搜索词",
    "Used for YouTube, Reddit and any API with {query} in it. Write them the way local consumers would type them.":
        "用于 YouTube、Reddit 以及地址中含 {query} 的 API。请按当地消费者的习惯写法填写。",
    "Query": "搜索词",
    "Language": "语言",
    "Brand watchlist": "品牌监测清单",
    "Brand names in Latin script so countries compare; local-script names and other spellings go in Aliases. Separate lists with commas.":
        "品牌名用拉丁字母书写，便于跨国比较；当地文字名称和其他拼写填在「别名」中。多个值用英文逗号分隔。",
    "Brand": "品牌",
    "Category": "品类",
    "Models": "型号",
    "Aliases": "别名",
    "Categories: countertop_dispenser = countertop dispenser, purifier = purifier, jug_filter = filter jug.":
        "品类：countertop_dispenser = 台式饮水机，purifier = 净水器，jug_filter = 滤水壶。",
    "Countertop dispenser": "台式饮水机",
    "Purifier": "净水器",
    "Jug filter": "滤水壶",
    "Local words for each category (tags posts that name no brand)": "各品类的本地用词（用于标记未提及品牌的帖子）",
    "Click × to remove a word; type a new one and press Enter to add it.": "点击 × 删除；输入新词后按回车添加。",
    "Save country setup": "保存国家设置",
    "Country setup saved.": "国家设置已保存。",

    # ---------------------------------------------------------------- sources
    "YouTube (default)": "YouTube（默认来源）",
    "No YouTube API key yet. Add it in Settings.": "尚未设置 YouTube API 密钥，请在「设置」中添加。",
    "Collect from YouTube": "从 YouTube 采集",
    "Videos per query": "每个搜索词的视频数",
    "Comments per video": "每个视频的评论数",
    "{q} queries × {v} videos. Each search uses 100 of the key's 10,000 free daily quota units.":
        "{q} 个搜索词 × {v} 个视频。每次搜索消耗该密钥每日 10,000 免费配额中的 100。",
    "Save YouTube settings": "保存 YouTube 设置",
    "YouTube settings saved.": "YouTube 设置已保存。",
    "Other websites": "其他网站",
    "Paste forum threads or review pages, one per line. The AI works out how to read each site once; robots.txt and rate limits are always respected, and marketplaces (Shopee, Lazada, Amazon, Taobao, JD…) are refused. Pages built by JavaScript can't be read: use the CSV upload below.":
        "粘贴论坛帖子或评测页面地址，每行一个。AI 会为每个网站自动识别读取方式（只需一次）；始终遵守 robots.txt 和访问频率限制，"
        "电商平台（Shopee、Lazada、亚马逊、淘宝、京东等）一律拒绝。由 JavaScript 生成的页面无法读取，请改用下方的 CSV 上传。",
    "Page addresses": "页面地址",
    "Read these pages": "读取这些页面",
    "Reading {url}…": "正在读取 {url}…",
    "Could not read this page reliably: ": "无法可靠地读取此页面：",
    "Found {n}+ posts on {site}. Check the sample below looks like real posts.":
        "在 {site} 上找到 {n}+ 条帖子。请确认下方示例确实是用户帖子。",
    "How it reads this site": "读取方式说明",
    "Add to sources": "添加到数据来源",
    "Website added.": "网站已添加。",
    "{n} page(s)": "{n} 个页面",
    "Collect from this site": "从此网站采集",
    "Max pages per thread": "每个帖子最多翻页数",
    "Pages": "页面",
    "Selectors (advanced)": "选择器（高级）",
    "Selectors are not valid JSON: {error}": "选择器不是有效的 JSON：{error}",
    "Website saved.": "网站已保存。",
    "Remove site": "移除网站",
    "Website removed. Documents already collected are kept.": "网站已移除。已采集的文档会保留。",
    "APIs": "API 接口",
    "Custom API (any JSON API you have a key for)": "自定义 API（任何您拥有密钥的 JSON API）",
    "Paste the endpoint. Put {query} where the search term goes and it is called once per search term. The key is stored only in the local .env file, never in the country profile.":
        "粘贴接口地址。在放搜索词的位置写 {query}，系统会对每个搜索词各调用一次。密钥只保存在本机的 .env 文件中，不会写入国家配置。",
    "Endpoint": "接口地址",
    "Key goes in": "密钥位置",
    "a request header": "请求头（Header）",
    "a URL parameter": "URL 参数",
    "no key needed": "不需要密钥",
    "Header / parameter name": "请求头 / 参数名称",
    "Pick one, or type the name the API's docs give.": "从列表中选择，或输入该 API 文档中的名称。",
    "Value prefix": "值前缀",
    "API key": "API 密钥",
    "Test and map fields": "测试并识别字段",
    "Calling the API and mapping its fields…": "正在调用 API 并识别字段…",
    "Field mapping": "字段映射",
    "Save API": "保存 API",
    "API added.": "API 已添加。",
    "API removed.": "API 已移除。",
    "key missing": "缺少密钥",
    "Reddit (official API)": "Reddit（官方 API）",
    "Collect from Reddit": "从 Reddit 采集",
    "Subreddits (type a name and press Enter to add)": "子版块（输入名称后按回车添加）",
    "Client ID": "Client ID",
    "Client secret": "Client secret",
    "Register a 'script' app at reddit.com/prefs/apps to get these.": "在 reddit.com/prefs/apps 注册一个「script」应用即可获得。",
    "Save Reddit settings": "保存 Reddit 设置",
    "Reddit settings saved.": "Reddit 设置已保存。",
    "Hand-collected reviews (CSV)": "人工收集的评价（CSV）",
    "For marketplaces and anything that can't be collected automatically. Columns: source, product, author, date, rating, text, url. Currently {n} row(s). Uploads are added to it.":
        "用于电商平台及其他无法自动采集的内容。列名：source, product, author, date, rating, text, url。当前共 {n} 行，上传的内容会追加进去。",
    "Upload CSV": "上传 CSV",
    "Add these rows": "添加这些行",
    "The CSV needs a 'text' column.": "CSV 必须包含「text」列。",
    "Added {n} rows. Run Collect to load them.": "已添加 {n} 行。运行「采集」即可导入。",

    # -------------------------------------------------------------------- run
    "Sources for {name}: {list}": "{name}的数据来源：{list}",
    "none enabled": "未启用任何来源",
    "AI provider key": "AI 服务商密钥",
    "Organisation name": "机构名称",
    "Contact email": "联系邮箱",
    "Set these in Settings before running: {list}": "运行前请先在「设置」中填写：{list}",
    "All enabled sources": "所有已启用的来源",
    "YouTube only": "仅 YouTube",
    "Pasted websites only": "仅粘贴的网站",
    "Custom APIs only": "仅自定义 API",
    "Reddit only": "仅 Reddit",
    "Hand-collected CSV only": "仅人工 CSV",
    "Collect from": "采集范围",
    "Max items per source": "每个来源最多条数",
    "No limit": "不限",
    "Use a small number for a test run.": "试运行时请选较小的数字。",
    "Max documents to classify": "最多分类文档数",
    "All waiting": "全部待分类",
    "{n} documents are waiting for classification (about 20 per AI call).": "{n} 篇文档等待分类（每次 AI 调用约处理 20 篇）。",
    "1 · Collect": "1 · 采集",
    "2 · Classify": "2 · 分类",
    "3 · Export workbook": "3 · 导出报表",
    "Run all three": "一键全部运行",
    "Log of the last run": "上次运行的日志",
    "⏳ {job} running for {s} s…": "⏳ {job} 已运行 {s} 秒…",
    "✅ {job} finished.": "✅ {job} 已完成。",
    "{job} stopped (exit code {rc}). See the log below.": "{job} 已停止（退出码 {rc}）。请查看下方日志。",
    "The log is in English.": "日志为英文。",

    # ---------------------------------------------------------------- results
    "No classified mentions yet. Collect and classify on the Run tab.": "还没有已分类的提及。请在「运行」页采集并分类。",
    "Spot-check": "抽查",
    "not done": "未进行",
    "Share of checked quotes a person judged correct.": "人工抽查中判定为正确的比例。",
    "Mentions per WATERS need": "各 WATERS 需求的提及数",
    "Need": "需求",
    "Share %": "占比 %",
    "Net sentiment by brand and need": "按品牌和需求的净情感",
    "(cells under n={n} suppressed)": "（样本少于 {n} 的格子不显示）",
    "Evidence (verbatim quote, with English translation)": "证据（原文引用及英文翻译）",
    "Quality spot-check": "质量抽查",
    "Read a few random quotes and say whether the AI got the need and the sentiment right. The share it got right is printed in the workbook. Aim for at least 20 quotes; below about 80% correct, treat the figures with caution.":
        "随机阅读几条引用，判断 AI 给出的需求和情感是否正确。正确率会写入报表。建议至少检查 20 条；正确率低于约 80% 时，请谨慎使用相关数据。",
    "So far: **{ok} of {n} correct ({pct}%)**.": "目前：**{n} 条中 {ok} 条正确（{pct}%）**。",
    "Check 20 random quotes": "随机抽查 20 条",
    "Every quote has already been checked.": "所有引用都已抽查过。",
    "Round finished: {ok} of {n} correct.": "本轮完成：{n} 条中 {ok} 条正确。",
    "Quote {i} of {n}": "第 {i} / {n} 条",
    "The AI says this quote is about **{need}** and is **{sentiment}**:": "AI 认为这条引用属于 **{need}**，情感为 **{sentiment}**：",
    "English: {text}": "英文：{text}",
    "What this need means": "这个需求是什么意思",
    "Full post": "完整帖子",
    "Is the need **and** the sentiment right?": "需求**和**情感都正确吗？",
    "✓ Correct": "✓ 正确",
    "✗ Wrong": "✗ 错误",
    "Workbooks": "报表",
    "None yet. Use “Export workbook” on the Run tab.": "暂无。请在「运行」页点击「导出报表」。",

    # --------------------------------------------------------------- settings
    "Stored in the local .env file on this computer only. Leave a key field blank to keep the current value.":
        "仅保存在本机的 .env 文件中。密钥栏留空表示保持当前值。",
    "Organisation": "机构",
    "Websites see who is collecting. Use your real organisation and a monitored email address.":
        "网站会看到是谁在采集。请填写真实的机构名称和有人查看的邮箱。",
    "Websites will see: {ua}": "网站将看到：{ua}",
    "Save organisation": "保存机构信息",
    "AI provider": "AI 服务商",
    "Used to classify posts and to set up countries, websites and APIs. Claude is the default; Chinese providers can be used instead.":
        "用于给帖子分类，以及设置国家、网站和 API。默认使用 Claude，也可改用国内服务商。",
    "Provider": "服务商",
    "Endpoint address": "接口地址",
    "Filled in for known providers. For 'Other', paste the base address ending before /chat/completions.":
        "已知服务商会自动填写。选择「其他」时，请粘贴 /chat/completions 之前的基础地址。",
    "Classification model": "分类模型",
    "Type the model name from the provider's docs": "输入服务商文档中的模型名称",
    "Keep the same model across countries and over time, or results stop being comparable.":
        "不同国家、不同时间请使用同一模型，否则结果不可比较。",
    "Setup helper model": "设置辅助模型",
    "Reads websites, maps APIs and drafts countries. Automatic: claude-opus-5-5 on Claude, otherwise the classification model.":
        "用于读取网站、识别 API 字段和起草国家配置。「自动」：使用 Claude 时为 claude-opus-5-5，否则与分类模型相同。",
    "Changing provider or model: results are no longer comparable with earlier runs. Do a new spot-check after classifying.":
        "更换服务商或模型后，结果将无法与之前的运行比较。分类完成后请重新抽查。",
    "Save AI provider": "保存 AI 服务商",
    "AI provider saved.": "AI 服务商已保存。",
    "Test connection": "测试连接",
    "Connected: {p} / {m}": "连接成功：{p} / {m}",
    "Save first, then test.": "请先保存，再测试。",
    "YouTube is blocked in mainland China. Run collection from a network with approved international access (e.g. an overseas office).":
        "YouTube 在中国大陆无法访问。请在具备合规国际网络的环境（如海外办公室）中运行采集。",
    "YouTube Data API v3 key": "YouTube Data API v3 密钥",
    "Google Cloud Console → enable YouTube Data API v3 → Credentials": "Google Cloud 控制台 → 启用 YouTube Data API v3 → 凭据",
    "Save YouTube key": "保存 YouTube 密钥",
    "Test YouTube": "测试 YouTube",
    "YouTube is reachable and the key works.": "YouTube 可以访问，密钥有效。",
    "YouTube answered with error {status}. Check the key.": "YouTube 返回错误 {status}，请检查密钥。",
    "Could not reach YouTube from this network.": "当前网络无法访问 YouTube。",
    "✅ Set. Never change it: author pseudonyms would no longer link across runs.":
        "✅ 已设置。请勿更改，否则不同批次之间的作者匿名编号将无法对应。",
    "Usernames are scrambled with this secret before they are stored.": "用户名在保存前会用这个密钥加密处理。",
    "Generate author salt": "生成作者加密盐",
    "Author salt generated.": "作者加密盐已生成。",

    # ------------------------------------------------- messages from the engine
    "Provider error {status}: {detail}": "服务商错误 {status}：{detail}",
    "No API key for {provider}. Add it in Settings.": "{provider} 没有 API 密钥，请在「设置」中添加。",
    "No model chosen. Pick one in Settings.": "尚未选择模型，请在「设置」中选择。",
    "The model declined this request.": "模型拒绝了这个请求。",
    "The model's answer was cut off.": "模型的回答被截断了。",
    "No endpoint address set for this provider. Add it in Settings.": "该服务商没有设置接口地址，请在「设置」中添加。",
    "The Anthropic API key was rejected. Check it in Settings.": "Anthropic API 密钥无效，请在「设置」中检查。",
    "Model {model} is not available to this key. Pick another in Settings.": "此密钥无法使用模型 {model}，请在「设置」中换一个。",
    "Rate limit reached. Wait a minute and retry.": "已达到调用频率上限，请稍等一分钟再试。",
    "Could not reach the Anthropic API. Check the network.": "无法连接 Anthropic API，请检查网络。",
    "The API key was rejected. Check it in Settings.": "API 密钥无效，请在「设置」中检查。",
    "Model {model} or the endpoint address was not found.": "找不到模型 {model} 或接口地址。",
    "Rate limit or balance reached. Wait, or check the account.": "已达到调用上限或余额不足，请稍后再试或检查账户。",
    "The model did not return JSON.": "模型没有返回 JSON。",
    "Could not reach {url}: {error}": "无法连接 {url}：{error}",
    "Unexpected response from the provider: {detail}": "服务商返回了无法识别的结果：{detail}",
    "Paste a full address starting with http:// or https://.": "请粘贴以 http:// 或 https:// 开头的完整地址。",
    "Marketplace review pages (Shopee, Lazada, Amazon and similar) are excluded: collecting them means defeating bot detection, which breaches their terms. Record those reviews by hand and upload them as a CSV instead.":
        "电商平台评价页（Shopee、Lazada、亚马逊等）不在采集范围内：采集它们需要绕过反爬机制，违反平台条款。请人工记录这些评价并以 CSV 上传。",
    "The page came back almost empty. It is probably built by JavaScript in the browser, which this collector does not run. Use the manual CSV upload for this site.":
        "页面几乎是空的，可能是由浏览器中的 JavaScript 生成的，本采集器无法运行 JavaScript。请对该网站使用 CSV 人工上传。",
    "No user posts found on this page. {reason} Paste the address of a single thread or review page.":
        "此页面上没有找到用户帖子。{reason} 请粘贴单个帖子或评测页面的地址。",
    "only {n} post(s) extracted; expected several": "只提取到 {n} 条帖子，预期应有多条",
    "most extracted posts are identical: post selector is too broad": "提取到的帖子大多相同：帖子选择器范围过大",
    "extracted bodies are very short: body selector may be wrong": "提取到的正文非常短：正文选择器可能有误",
    "This site does not permit automated collection ({error}). It cannot be used.": "该网站不允许自动采集（{error}），无法使用。",
    "Could not load the page: {error}": "无法加载页面：{error}",
    "The API refused the request ({error}). Check the key and where it goes (header or query parameter).":
        "API 拒绝了请求（{error}）。请检查密钥及其位置（请求头或 URL 参数）。",
    "Could not call the API or it did not return JSON: {error}": "无法调用 API，或其未返回 JSON：{error}",
    "no items with text were found using this mapping": "按此映射没有找到含文本的条目",
    "selector error: {error}": "选择器错误：{error}",
}
