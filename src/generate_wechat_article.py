#!/usr/bin/env python3
"""generate_wechat_article.py — Read daily_weather.json and use Gemini API
to produce a Chinese weather article formatted for WeChat public accounts.

Usage:
    python src/generate_wechat_article.py --input daily_weather.json --output wechat_article.md

Environment variables:
    GEMINI_API_KEY   Your Google Gemini API key (required)

Dependencies:
    pip install google-genai
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Prompt builder
# ---------------------------------------------------------------------------

SYSTEM_PROMPT = """\
你是一位专业的新西兰华人天气资讯编辑, 擅长将英文气象数据整理成适合微信公众号发布的中文天气报道。
写作要求：
1. 语言流畅自然, 适合普通读者。
2. 地名一律翻译成标准中文并在括号内保留英文原文, 例如: 奥克兰(Auckland), 惠灵顿(Wellington)。
3. 排版适合微信公众号：使用清晰的标题层级、分段, 适当使用 emoji 增加可读性, 但不要过度。
4. 不要捏造数据；只根据提供的 JSON 内容进行改写和归纳。
5. 在文章最后另起一个段落, 用引用格式固定输出以下声明( 内容不得改动): 
   本文天气数据来源于新西兰气象局 MetService (www.metservice.com)；由 Kiwi天气站 自动整理发布；关于 Metservice、NIWA 等实时天气预报可点击 👇阅读全文 或直接访问 https://simpleweather.online
"""

ARTICLE_PROMPT_TEMPLATE = """\
以下是从 MetService 官网抓取的 JSON 天气数据, fetched_at 字段表示数据获取时间：

{json_data}

请根据上述数据, 撰写一篇微信公众号天气报道, 要求如下：
- 文章标题( 第一行, 使用 Markdown # 标记) ：格式为：{date_label} | 根据天气数据总结的标题, 例如：新西兰南岛持续降雨, 北岛晴好
- 在第一段直接切入主题, 根据Json的天气数据, 按南岛、北岛归纳总结近期天气概况。
- 第一段结束后, 加入一个二级标题( ## 当前天气形势) 后留出一行空行（不用添加任何内容）, 我会手动添加一张天气形势图。
- 分别介绍"近期天气概况"( 对应 Short Forecast) 和"未来天气展望"( 对应 Extended Forecast) 。
- 在"未来天气展望"板块结尾处单独插入一行占位符: <!-- CITY_FORECAST_TABLE -->
- 地名标准中文翻译+英文括注( 例如：北岛( North Island) 、科罗曼德尔( Coromandel) 、吉斯本( Gisborne) 、霍克斯湾( Hawke's Bay) 、怀卡托( Waikato) 等) 。
- 排版清晰, 使用二级标题( ##) 区分各板块, 适当使用 emoji。
- 末尾保留上述固定声明。
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# MetService forecast word → emoji
_WEATHER_EMOJI: dict[str, str] = {
    "fine": "☀️",
    "mostly fine": "🌤️",
    "mainly fine": "🌤️",
    "partly cloudy": "⛅",
    "cloudy": "☁️",
    "overcast": "☁️",
    "few showers": "🌦️",
    "drizzle": "🌦️",
    "showers": "🌧️",
    "rain": "🌧️",
    "heavy rain": "⛈️",
    "thunder": "⛈️",
    "thunderstorm": "⛈️",
    "snow": "❄️",
    "hail": "🌨️",
    "fog": "🌫️",
    "windy": "💨",
    "morning frost": "🥶",
}

# City English name → Chinese display name
_CITY_ZH: dict[str, str] = {
    "Auckland": "奥克兰",
    "Wellington": "惠灵顿",
    "Christchurch": "基督城",
    "Hamilton": "汉密尔顿",
    "Tauranga": "陶朗加",
    "Dunedin": "但尼丁",
    "Queenstown": "皇后镇",
    "Napier": "内皮尔",
    "Palmerston North": "北帕默斯顿",
    "Nelson": "尼尔森",
    "Rotorua": "罗托鲁瓦",
    "New Plymouth": "新普利茅斯",
    "Invercargill": "因弗卡吉尔",
    "Whangarei": "旺阿雷",
    "Gisborne": "吉斯本",
}


def _forecast_emoji(word: str) -> str:
    """Map a MetService forecastWord to an emoji, falling back to the raw text."""
    return _WEATHER_EMOJI.get(word.lower().strip(), word)


def _city_label(city: str) -> str:
    zh = _CITY_ZH.get(city, city)
    return f"{zh}({city})"


def build_city_forecast_table(forecast_path: Path) -> str:
    """Build a Markdown table of city forecasts from localForecast.json."""
    try:
        raw = json.loads(forecast_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""

    forecasts: dict = raw.get("forecasts", {})
    if not forecasts:
        return ""

    lines: list[str] = []
    lines.append("## 🗓️ 未来48小时城市预报")
    lines.append("")
    lines.append("| 城市 | 日期 | 最低/最高 | 上午 | 下午 | 傍晚 |")
    lines.append("|------|------|-----------|---------|---------|--------|")

    for city, days in sorted(forecasts.items(), key=lambda x: x[0]):
        if not days:
            continue
        for i, day in enumerate(days):
            city_col = _city_label(city) if i == 0 else ""
            date_col = f"{day['dow'][:3]} {day['date']}"
            temp_col = f"{day['min']}°C / {day['max']}°C"
            morning = _forecast_emoji(day.get("morning", ""))
            afternoon = _forecast_emoji(day.get("afternoon", ""))
            evening = _forecast_emoji(day.get("evening", ""))
            lines.append(
                f"| {city_col} | {date_col} | {temp_col} "
                f"| {morning} | {afternoon} | {evening} |"
            )

    return "\n".join(lines)


def extract_date_label(data: dict) -> str:
    """Derive a human-readable Chinese date label from the fetched_at field."""
    fetched_at = data.get("fetched_at", "")
    if fetched_at:
        # Parse ISO datetime, e.g. "2026-04-11T17:27:42+12:00"
        from datetime import datetime

        try:
            dt = datetime.fromisoformat(fetched_at)
            # Format: 2026年4月11日
            return f"{dt.year}年{dt.month}月{dt.day}日"
        except ValueError:
            pass
    return "最新"


def build_prompt(data: dict) -> str:
    json_data = json.dumps(data, indent=2, ensure_ascii=False)
    date_label = extract_date_label(data)
    return ARTICLE_PROMPT_TEMPLATE.format(json_data=json_data, date_label=date_label)


# ---------------------------------------------------------------------------
# Gemini API call
# ---------------------------------------------------------------------------


def call_gemini(prompt: str, model: str = "gemini-3-pro-preview") -> str:
    try:
        from google import genai
        from google.genai import types
    except ImportError:
        raise SystemExit(
            "google-genai is required. Install it with:\n" "  pip install google-genai"
        )

    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        raise SystemExit(
            "GEMINI_API_KEY environment variable is not set.\n"
            "Export it before running this script:\n"
            "  export GEMINI_API_KEY=your_key_here"
        )

    client = genai.Client(api_key=api_key)

    response = client.models.generate_content(
        model=model,
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=SYSTEM_PROMPT,
            temperature=0.7,
            max_output_tokens=8192,
        ),
    )
    return response.text


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Call Gemini API to generate a WeChat-formatted Chinese weather article from daily_weather.json."
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("daily_weather.json"),
        help="Path to the weather JSON file (default: daily_weather.json)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("wechat_article.md"),
        help="Output Markdown file path (default: wechat_article.md)",
    )
    parser.add_argument(
        "--model",
        default="gemini-3-flash-preview",
        help="Gemini model name to use (default: gemini-3-flash-preview)",
    )
    parser.add_argument(
        "--local-forecast",
        type=Path,
        default=Path("structured_forecast.json"),
        help="Path to structured_forecast.json from local_forecast.py (default: structured_forecast.json)",
    )
    parser.add_argument(
        "--print",
        action="store_true",
        dest="print_output",
        help="Also print the article to stdout",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # Read JSON
    if not args.input.exists():
        print(f"✗ Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    try:
        data = json.loads(args.input.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        print(f"✗ Failed to parse JSON: {exc}", file=sys.stderr)
        sys.exit(1)

    print(f"✓ Loaded weather data from {args.input}")

    # Build prompt and call Gemini
    prompt = build_prompt(data)
    print(f"  Calling Gemini ({args.model})…")

    import time
    max_retries = 3
    retry_delay = 30
    article = None

    for attempt in range(1, max_retries + 1):
        try:
            article = call_gemini(prompt, model=args.model)
            break
        except Exception as exc:
            if attempt < max_retries:
                print(f"✗ Gemini API error (attempt {attempt}): {exc}", file=sys.stderr)
                print(f"  Retrying in {retry_delay}s…", file=sys.stderr)
                time.sleep(retry_delay)
            else:
                print(f"✗ Gemini API error (attempt {attempt}): {exc}", file=sys.stderr)
                sys.exit(1)

    if article is None:
        print("✗ Failed to generate article after all retries", file=sys.stderr)
        sys.exit(1)

    # Inject city forecast table at placeholder (or insert before trailing disclaimer)
    city_table = build_city_forecast_table(args.local_forecast)
    if city_table:
        placeholder = "<!-- CITY_FORECAST_TABLE -->"
        if placeholder in article:
            article = article.replace(placeholder, city_table)
        else:
            # Fallback: insert before the fixed disclaimer
            disclaimer_marker = "本文天气数据来源于新西兰气象局 MetService"
            idx = article.find(disclaimer_marker)
            if idx != -1:
                article = article[:idx].rstrip() + "\n\n" + city_table + "\n\n" + article[idx:]
            else:
                article = article.rstrip() + "\n\n" + city_table
    else:
        print("  ⚠ local forecast file not found or empty, skipping city table", file=sys.stderr)

    # Write output
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(article, encoding="utf-8")
    print(f"✓ WeChat article written to {args.output}")

    if args.print_output:
        print("\n" + "─" * 60)
        print(article)


if __name__ == "__main__":
    main()
