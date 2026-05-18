from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

from _common import now_iso, write_json

SECTION_NAMES = ("Short Forecast", "Extended Forecast")
REAL_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)


async def _fetch_html(url: str) -> bytes:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise SystemExit(
            "Playwright is required. Install it with the project .venv active, for example: "
            "./.venv/bin/python -m pip install playwright && ./.venv/bin/python -m playwright install chromium"
        ) from exc

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                user_agent=REAL_USER_AGENT,
                locale="en-US",
                viewport={"width": 1440, "height": 1200},
                ignore_https_errors=True,
            )
            try:
                page = await context.new_page()
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(3000)
                    return (await page.content()).encode("utf-8")
                finally:
                    await page.close()
            finally:
                await context.close()
        finally:
            await browser.close()


def _load_markitdown_text(url: str) -> str:
    try:
        from markitdown import MarkItDown
    except ImportError as exc:
        raise SystemExit(
            "markitdown is required. Install it with the project .venv active, for example: "
            "./.venv/bin/python -m pip install markitdown"
        ) from exc

    converter = MarkItDown()
    html_bytes = asyncio.run(_fetch_html(url))
    from io import BytesIO

    result = converter.convert_stream(
        BytesIO(html_bytes), file_extension=".html", url=url
    )
    return result.text_content or ""


def _clean_line(text: str) -> str:
    return (text or "").replace("\r", "").strip()


def _dedupe_lines(lines: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for line in lines:
        normalized = re.sub(r"\s+", " ", _clean_line(line))
        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
    return out


def _extract_markdown_sections(
    markdown_text: str,
) -> dict[str, dict[str, list[str] | str]]:
    lines = markdown_text.splitlines()
    sections: dict[str, dict[str, list[str] | str]] = {}
    current_heading: str | None = None
    current_level: int | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_heading, current_level, current_lines
        if current_heading is not None:
            sections[current_heading] = {
                "heading": current_heading,
                "content": _dedupe_lines(current_lines),
            }
        current_heading = None
        current_level = None
        current_lines = []

    for raw_line in lines:
        line = _clean_line(raw_line)
        heading_match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading_match:
            level = len(heading_match.group(1))
            heading = heading_match.group(2).strip()
            if heading in SECTION_NAMES:
                flush()
                current_heading = heading
                current_level = level
                current_lines = []
                continue
            if (
                current_heading is not None
                and current_level is not None
                and level <= current_level
            ):
                flush()

            if current_heading is not None:
                current_lines.append(line)
            continue

        if current_heading is not None and line:
            current_lines.append(line)

    flush()
    return sections


def extract_sections(url: str, export_all: bool = False) -> dict:
    markdown_text = _load_markitdown_text(url)

    if export_all:
        return {
            "fetched_at": now_iso(),
            "url": url,
            "mode": "all_content",
            "content": _dedupe_lines(markdown_text.splitlines()),
        }

    sections = _extract_markdown_sections(markdown_text)
    for name in SECTION_NAMES:
        sections.setdefault(
            name,
            {
                "heading": name,
                "content": [],
                "error": f"Heading '{name}' not found",
            },
        )

    return {
        "fetched_at": now_iso(),
        "url": url,
        "mode": "sections",
        "sections": sections,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch the MetService homepage with markitdown and export the Short Forecast and Extended Forecast sections."
    )
    parser.add_argument("--url", default="https://www.metservice.com/")
    parser.add_argument(
        "--output", type=Path, required=True, help="Output JSON file path"
    )
    parser.add_argument(
        "--export-all",
        action="store_true",
        help="Export all homepage content instead of just specific sections",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        data = extract_sections(args.url, export_all=args.export_all)
        write_json(args.output, data)
        mode = "all content" if args.export_all else "sections"
        print(f"✓ Wrote homepage {mode} to {args.output}")
    except Exception as e:
        print(f"✗ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
