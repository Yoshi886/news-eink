#!/usr/bin/env python3
"""
每日新闻速览 - GitHub Actions 自动更新脚本
功能：搜索新闻 → LLM整理 → 生成JSON → 推送到GitHub Pages
"""

import os
import sys
import json
import time
import datetime
import base64
import re
import requests

# ============ 配置 ============
# LLM API（支持任何OpenAI兼容接口，默认用火山引擎豆包）
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_API_URL = os.environ.get("LLM_API_URL", "https://ark.cn-beijing.volces.com/api/v3/chat/completions")
LLM_MODEL = os.environ.get("LLM_MODEL", "doubao-pro-32k")

# GitHub配置
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
OWNER = os.environ.get("NEWS_OWNER", "Yoshi886")
REPO = os.environ.get("NEWS_REPO", "news-eink")
BRANCH = os.environ.get("NEWS_BRANCH", "main")
FILE_PATH = "news-data.json"

# 搜索配置
MAX_RETRIES = 3
RESULTS_PER_QUERY = 12

# 8个板块的搜索关键词
SEARCH_QUERIES = [
    ("国际政治", "今日国际政治要闻 外交 地缘政治"),
    ("国际政治", "今日联合国 大国博弈 国际关系"),
    ("军事国防", "今日军事新闻 国防 军队"),
    ("军事国防", "今日军事动态 武器装备 军演"),
    ("科技前沿", "今日科技新闻 AI 人工智能 芯片"),
    ("科技前沿", "今日互联网 半导体 航天 生物科技"),
    ("数码产品", "今日手机发布 数码新品"),
    ("数码产品", "今日电脑 智能硬件 消费电子"),
    ("经济金融", "今日经济金融 A股 股市 黄金"),
    ("经济金融", "今日油价 汇率 大宗商品 央行"),
    ("国内经济", "今日国内经济 产业政策 企业"),
    ("国内经济", "今日消费市场 房地产 新能源汽车"),
    ("社会民生", "今日社会民生 教育 就业 社保"),
    ("社会民生", "今日医疗 公共安全 民生政策"),
    ("文化", "今日文化新闻 电影 电视剧 综艺"),
    ("文化", "今日体育 艺术 出版 文化遗产"),
]


def log(msg):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def search_news():
    """使用DuckDuckGo搜索新闻"""
    try:
        from duckduckgo_search import DDGS
    except ImportError:
        log("安装duckduckgo-search...")
        os.system(f"{sys.executable} -m pip install duckduckgo-search -q")
        from duckduckgo_search import DDGS

    today = datetime.date.today()
    date_str = today.strftime("%Y年%m月%d日")
    all_results = []

    with DDGS() as ddgs:
        for category, query in SEARCH_QUERIES:
            full_query = f"{query} {date_str}"
            for attempt in range(MAX_RETRIES):
                try:
                    results = list(ddgs.news(full_query, max_results=RESULTS_PER_QUERY))
                    for r in results:
                        r["_category"] = category
                    all_results.extend(results)
                    log(f"  [{category}] '{query}' → {len(results)}条")
                    break
                except Exception as e:
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(2)
                    else:
                        log(f"  [{category}] 搜索失败: {e}")

    # 去重（按URL）
    seen = set()
    unique = []
    for r in all_results:
        url = r.get("url", "")
        if url and url not in seen:
            seen.add(url)
            unique.append(r)

    log(f"搜索完成：原始{len(all_results)}条，去重后{len(unique)}条")
    return unique


def format_search_results(results):
    """将搜索结果格式化为LLM可读文本"""
    lines = []
    for i, r in enumerate(results):
        title = r.get("title", "").strip()
        body = r.get("body", "").strip()
        source = r.get("source", "").strip()
        date = r.get("date", "").strip()
        url = r.get("url", "").strip()
        cat = r.get("_category", "")
        lines.append(f"[{i+1}][{cat}] {title}")
        lines.append(f"  来源:{source} | 时间:{date}")
        lines.append(f"  摘要:{body}")
        lines.append(f"  链接:{url}")
    return "\n".join(lines)


def call_llm(news_text):
    """调用LLM API整理新闻"""
    today = datetime.date.today()
    weekday_map = {0: "周一", 1: "周二", 2: "周三", 3: "周四", 4: "周五", 5: "周六", 6: "周日"}
    weekday = weekday_map[today.weekday()]
    now_str = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M")
    date_str = today.strftime("%Y-%m-%d")

    prompt = f"""你是资深新闻编辑。根据以下搜索到的{date_str}（{weekday}）新闻素材，整理出80条新闻+12篇深度解读。

【硬性要求】
1. 8个板块各10条，顺序固定：国际政治、军事国防、科技前沿、数码产品、经济金融、国内经济、社会民生、文化
2. 12篇深度解读必须另选题，标题不可与80条中任何一条重复
3. 每条普通新闻字段：title(精炼标题)、desc(15-35字摘要)、meta(来源媒体)、time(必须MM-DD HH:MM格式，从素材时间转换)、background(1-2句具体背景，有事实有数据)、impact(1-2句具体影响，写明方向和程度)、outlook(1句具体预判，写明时间节点或关注事项)、link(原文URL)
4. 每篇深度解读字段：category(分类)、title、source、link、time、background(2-3句更详细)、impact(2-3句更深入)、outlook(1-2句更具体)
5. 分析严禁套话：禁止"可能产生影响建议关注""后续取决于相关方"等废话，每条必须针对该新闻具体内容
6. 时间字段：素材中的时间如"2小时前""今天08:30"等转为MM-DD HH:MM；无法确定的按该新闻时段合理标注
7. 涉及中国港澳台按一个中国原则表述
8. link字段用素材中的真实URL，没有的用"https://www.baidu.com/s?wd="+标题搜索

【输出格式】只输出JSON，不要输出其他文字：
{{"date":"{date_str}","updatedAt":"{now_str}","weekday":"{weekday}","title":"今日新闻速览","subtitle":"共80条+12篇深度解读（不重复）· 点击标题跳转原文","summary":"50-80字一句话总结","stats":{{"国际":10,"军事":10,"科技":10,"数码":10,"经济":10,"国内":10,"社会":10,"文化":10,"深度":12}},"deepAnalysis":[...],"sections":[{{"name":"国际政治","count":10,"items":[...]}},{{"name":"军事国防","count":10,"items":[...]}},{{"name":"科技前沿","count":10,"items":[...]}},{{"name":"数码产品","count":10,"items":[...]}},{{"name":"经济金融","count":10,"items":[...]}},{{"name":"国内经济","count":10,"items":[...]}},{{"name":"社会民生","count":10,"items":[...]}},{{"name":"文化","count":10,"items":[...]}}]}}

【新闻素材】
{news_text}"""

    headers = {
        "Authorization": f"Bearer {LLM_API_KEY}",
        "Content-Type": "application/json",
    }
    data = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 16000,
    }

    for attempt in range(MAX_RETRIES):
        try:
            log(f"调用LLM（第{attempt+1}次）...")
            resp = requests.post(LLM_API_URL, headers=headers, json=data, timeout=180)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]

            # 提取JSON
            content = content.strip()
            if "```json" in content:
                content = content.split("```json")[1].split("```")[0]
            elif "```" in content:
                content = content.split("```")[1].split("```")[0]

            news_data = json.loads(content.strip())
            return news_data

        except Exception as e:
            log(f"LLM调用失败: {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(5)
            else:
                raise


def validate_news(news_data):
    """校验新闻数据"""
    errors = []

    # 检查sections
    expected_sections = ["国际政治", "军事国防", "科技前沿", "数码产品", "经济金融", "国内经济", "社会民生", "文化"]
    sections = news_data.get("sections", [])

    if len(sections) != 8:
        errors.append(f"sections数量{len(sections)}≠8")

    section_names = [s.get("name") for s in sections]
    if section_names != expected_sections:
        errors.append(f"板块顺序不对: {section_names}")

    total_items = 0
    all_titles = set()
    for s in sections:
        items = s.get("items", [])
        total_items += len(items)
        for item in items:
            title = item.get("title", "")
            all_titles.add(title)
            # 检查必填字段
            for field in ["time", "background", "impact", "outlook", "link", "desc", "meta"]:
                if not item.get(field):
                    errors.append(f"缺少字段{field}: {title}")
            # 检查时间格式
            t = item.get("time", "")
            if not re.match(r"\d{2}-\d{2}\s+\d{2}:\d{2}", t):
                errors.append(f"时间格式错误: {title} → {t}")

    # 检查深度解读
    deep = news_data.get("deepAnalysis", [])
    if len(deep) != 12:
        errors.append(f"深度解读数量{len(deep)}≠12")

    for d in deep:
        title = d.get("title", "")
        if title in all_titles:
            errors.append(f"深度解读与普通新闻重复: {title}")
        for field in ["time", "background", "impact", "outlook", "link", "source", "category"]:
            if not d.get(field):
                errors.append(f"深度解读缺少字段{field}: {title}")

    if errors:
        log(f"⚠️ 校验发现{len(errors)}个问题:")
        for e in errors[:10]:
            log(f"  - {e}")
    else:
        log("✅ 数据校验通过")

    return len(errors) == 0


def push_to_github(news_data):
    """推送JSON到GitHub仓库"""
    content = json.dumps(news_data, ensure_ascii=False, indent=2)
    content_b64 = base64.b64encode(content.encode("utf-8")).decode("utf-8")

    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }

    # 获取当前SHA
    r = requests.get(
        f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{FILE_PATH}?ref={BRANCH}",
        headers=headers,
    )
    sha = ""
    if r.status_code == 200:
        sha = r.json().get("sha", "")

    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    data = {
        "message": f"Update news-data.json {now}",
        "content": content_b64,
        "branch": BRANCH,
    }
    if sha:
        data["sha"] = sha

    r = requests.put(
        f"https://api.github.com/repos/{OWNER}/{REPO}/contents/{FILE_PATH}",
        headers=headers,
        json=data,
    )

    if r.status_code in (200, 201):
        log(f"✅ 部署成功！GitHub Pages将在1-2分钟内更新")
        return True
    else:
        log(f"❌ 部署失败: {r.status_code} {r.text[:500]}")
        return False


def main():
    log("=" * 50)
    log("📰 每日新闻速览 - 自动更新开始")
    log(f"   日期: {datetime.date.today()}")
    log("=" * 50)

    # 检查环境变量
    if not LLM_API_KEY:
        log("❌ 缺少LLM_API_KEY环境变量")
        sys.exit(1)
    if not GITHUB_TOKEN:
        log("❌ 缺少GITHUB_TOKEN环境变量")
        sys.exit(1)

    # 1. 搜索新闻
    log("🔍 第一步：搜索新闻...")
    results = search_news()
    if not results:
        log("❌ 未搜索到任何新闻")
        sys.exit(1)

    # 2. 格式化并调用LLM
    log("🧠 第二步：LLM整理新闻...")
    news_text = format_search_results(results)
    log(f"   素材长度: {len(news_text)}字符")

    news_data = call_llm(news_text)

    # 3. 校验
    total_items = sum(len(s.get("items", [])) for s in news_data.get("sections", []))
    total_deep = len(news_data.get("deepAnalysis", []))
    log(f"📊 生成结果: {total_items}条新闻 + {total_deep}篇深度解读")

    validate_news(news_data)

    # 4. 保存本地备份
    with open(FILE_PATH, "w", encoding="utf-8") as f:
        json.dump(news_data, f, ensure_ascii=False, indent=2)
    log(f"💾 已保存本地: {FILE_PATH}")

    # 5. 推送到GitHub
    log("🚀 第三步：推送到GitHub...")
    success = push_to_github(news_data)

    if success:
        log("🎉 全部完成！")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
