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
你是一位专业的新西兰华人天气资讯编辑，擅长将英文气象数据整理成适合微信公众号发布的中文天气报道。
写作要求：
1. 语言流畅自然，适合普通读者。
2. 地名一律翻译成标准中文并在括号内保留英文原文，例如：奥克兰（Auckland）、惠灵顿（Wellington）。
3. 排版适合微信公众号：使用清晰的标题层级、分段，适当使用 emoji 增加可读性，但不要过度。
4. 不要捏造数据；只根据提供的 JSON 内容进行改写和归纳。
5. 在文章最后另起一行，固定输出以下声明（内容不得改动）：
   本文天气数据来源于新西兰气象局 MetService 官方网站；由 Kiwi天气站 自动整理发布；关于 Metservice、NIWA 等详细天气预报可参考 simpleweather.online
"""

ARTICLE_PROMPT_TEMPLATE = """\
以下是从 MetService 官网抓取的 JSON 天气数据，fetched_at 字段表示数据获取时间：

{json_data}

请根据上述数据，撰写一篇微信公众号天气报道，要求如下：
- 文章标题（第一行，使用 Markdown # 标记）：新西兰天气预报 | {date_label}
- 分别介绍"近期天气概况"（对应 Short Forecast）和"未来天气展望"（对应 Extended Forecast）。
- 地名标准中文翻译+英文括注（例如：北岛（North Island）、科罗曼德尔（Coromandel）、吉斯本（Gisborne）、霍克斯湾（Hawke's Bay）、怀卡托（Waikato）等）。
- 排版清晰，使用二级标题（##）区分各板块，适当使用 emoji。
- 末尾保留上述固定声明。
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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
            "google-genai is required. Install it with:\n"
            "  pip install google-genai"
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

    try:
        article = call_gemini(prompt, model=args.model)
    except Exception as exc:
        print(f"✗ Gemini API error: {exc}", file=sys.stderr)
        sys.exit(1)

    # Write output
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(article, encoding="utf-8")
    print(f"✓ WeChat article written to {args.output}")

    if args.print_output:
        print("\n" + "─" * 60)
        print(article)


if __name__ == "__main__":
    main()
