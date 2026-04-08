from __future__ import annotations

import argparse
import asyncio
import re
import sys
from pathlib import Path

from _common import now_iso, write_json


SECTION_NAMES = ("Short Forecast", "Extended Forecast")


async def extract_all_content(page) -> list[str]:
    """Extract all text content from the page."""
    content = await page.evaluate(
        """() => {
            const clean = (text) =>
              (text || "").replace(/\\s+/g, " ").trim();
            const out = [];
            const seen = new Set();

            function push(text) {
              const normalized = clean(text);
              if (normalized && !seen.has(normalized)) {
                seen.add(normalized);
                out.push(normalized);
              }
            }

            // Extract all text content from paragraphs, list items, headings, divs, spans
            document.querySelectorAll("h1, h2, h3, h4, h5, h6, p, li, div, span").forEach(el => {
              if (el.innerText) {
                const tag = (el.tagName || "").toUpperCase();
                if (/^H[1-6]$/.test(tag)) {
                  push(`[${tag}] ${el.innerText}`);
                } else {
                  push(el.innerText);
                }
              }
            });

            return out;
        }"""
    )
    return content


async def extract_sections(url: str, export_all: bool = False) -> dict:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise SystemExit(
            "Playwright is required. Install it with "
            "`python3 -m pip install playwright && python3 -m playwright install chromium`."
        ) from exc

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded")
                await page.wait_for_timeout(1500)

                if export_all:
                    # Export all content from the page
                    content = await extract_all_content(page)
                    return {
                        "fetched_at": now_iso(),
                        "url": url,
                        "mode": "all_content",
                        "content": content,
                    }
                else:
                    # Extract specific sections
                    sections: dict[str, dict] = {}
                    for name in SECTION_NAMES:
                        locator = page.get_by_role("heading", name=re.compile(rf"^{re.escape(name)}$", re.I))
                        if await locator.count() == 0:
                            sections[name] = {
                                "heading": name,
                                "content": [],
                                "error": f"Heading '{name}' not found",
                            }
                            continue

                        heading = locator.first
                        content = await heading.evaluate(
                            """(node) => {
                                const clean = (text) =>
                                  (text || "").replace(/\\s+/g, " ").trim();
                                const rank = Number((node.tagName || "H6").replace(/[^0-9]/g, "")) || 6;
                                const out = [];
                                const seen = new Set();

                                function push(text) {
                                  const normalized = clean(text);
                                  if (!normalized || seen.has(normalized)) return;
                                  seen.add(normalized);
                                  out.push(normalized);
                                }

                                function nextElement(current) {
                                  if (current.firstElementChild) return current.firstElementChild;
                                  while (current) {
                                    if (current.nextElementSibling) return current.nextElementSibling;
                                    current = current.parentElement;
                                  }
                                  return null;
                                }

                                let current = node;
                                let inspected = 0;
                                while ((current = nextElement(current)) && inspected < 120) {
                                  inspected += 1;
                                  const tag = (current.tagName || "").toUpperCase();
                                  if (/^H[1-6]$/.test(tag)) {
                                    const currentRank = Number(tag.slice(1)) || 6;
                                    if (currentRank <= rank) break;
                                  }

                                  if (current.querySelector && current.querySelector("h1,h2,h3,h4,h5,h6")) {
                                    continue;
                                  }

                                  if (/^H[1-6]$/.test(tag)) {
                                    push(`[${tag}] ${current.innerText}`);
                                  } else if (["P", "LI", "SPAN", "DIV"].includes(tag)) {
                                    push(current.innerText);
                                  }
                                }

                                return out.slice(0, 12);
                            }"""
                        )

                        sections[name] = {
                            "heading": name,
                            "content": content,
                        }

                    return {
                        "fetched_at": now_iso(),
                        "url": url,
                        "mode": "sections",
                        "sections": sections,
                    }
            finally:
                await page.close()
        finally:
            await browser.close()



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fetch the MetService homepage and export the Short Forecast and Extended Forecast sections."
    )
    parser.add_argument("--url", default="https://www.metservice.com/")
    parser.add_argument("--output", type=Path, required=True, help="Output JSON file path")
    parser.add_argument(
        "--export-all",
        action="store_true",
        help="Export all homepage content instead of just specific sections"
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        data = asyncio.run(extract_sections(args.url, export_all=args.export_all))
        write_json(args.output, data)
        mode = "all content" if args.export_all else "sections"
        print(f"✓ Wrote homepage {mode} to {args.output}")
    except Exception as e:
        print(f"✗ Error: {e}", file=sys.stderr)
        sys.exit(1)



if __name__ == "__main__":
    main()
