#!/usr/bin/env python3
"""upload_wechat_draft.py — Upload a Markdown weather article to a WeChat
Official Account draft box via the WeChat API.

Usage:
    python src/upload_wechat_draft.py --input wechat_article.md
    python src/upload_wechat_draft.py --input wechat_article.md --cover cover.jpg
    python src/upload_wechat_draft.py --input wechat_article.md --dry-run

Environment variables (required):
    WECHAT_APP_ID       WeChat Official Account AppID
    WECHAT_APP_SECRET   WeChat Official Account AppSecret
    >> 8111105cc9c1fa20ad8910729092de7c

Prerequisites:
    pip install requests markdown Pillow

Notes:
    - Only Verified Service/Subscription Accounts can use these APIs.
    - Your server's IP must be on the IP whitelist in the WeChat admin panel.
    - This script only creates a DRAFT; publish it manually in the admin panel.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path


# ---------------------------------------------------------------------------
# WeChat API helpers
# ---------------------------------------------------------------------------

WECHAT_API_BASE = "https://api.weixin.qq.com"


def get_access_token(app_id: str, app_secret: str) -> str:
    """Fetch a short-lived access token from WeChat (valid ~2 hours)."""
    import requests

    url = (
        f"{WECHAT_API_BASE}/cgi-bin/token"
        f"?grant_type=client_credential&appid={app_id}&secret={app_secret}"
    )
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    data = resp.json()
    if "access_token" not in data:
        raise RuntimeError(
            f"Failed to get access_token: {data.get('errmsg', data)}"
        )
    token = data["access_token"]
    print(f"  ✓ Access token obtained (expires in {data.get('expires_in', '?')}s)")
    return token


def upload_permanent_image(access_token: str, image_path: Path) -> str:
    """Upload an image to WeChat Permanent Material and return its media_id."""
    import requests

    url = (
        f"{WECHAT_API_BASE}/cgi-bin/material/add_material"
        f"?access_token={access_token}&type=image"
    )
    with open(image_path, "rb") as f:
        files = {"media": (image_path.name, f, "image/jpeg")}
        resp = requests.post(url, files=files, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "media_id" not in data:
        raise RuntimeError(
            f"Image upload failed: {data.get('errmsg', data)}"
        )
    media_id = data["media_id"]
    print(f"  ✓ Cover image uploaded → media_id: {media_id}")
    return media_id


def upload_image_for_content(access_token: str, image_path: Path) -> str:
    """Upload an image to WeChat Permanent Material and return its CDN URL.

    WeChat permanent image materials return a ``url`` field that can be
    embedded directly in article HTML content.
    """
    import requests

    # Determine MIME type from suffix
    suffix = image_path.suffix.lower()
    mime = "image/gif" if suffix == ".gif" else "image/jpeg"

    url = (
        f"{WECHAT_API_BASE}/cgi-bin/material/add_material"
        f"?access_token={access_token}&type=image"
    )
    with open(image_path, "rb") as f:
        files = {"media": (image_path.name, f, mime)}
        resp = requests.post(url, files=files, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "url" not in data:
        raise RuntimeError(
            f"Content image upload failed (no url returned): {data.get('errmsg', data)}"
        )
    cdn_url = data["url"]
    print(f"  ✓ Content image uploaded → {cdn_url}")
    return cdn_url


def upload_image_full(access_token: str, image_path: Path) -> tuple[str, str]:
    """Upload an image to WeChat Permanent Material.

    Returns:
        (media_id, url) — media_id for use as article cover (thumb_media_id);
        url for embedding directly in article HTML content.
    """
    import requests

    suffix = image_path.suffix.lower()
    if suffix == ".gif":
        mime = "image/gif"
    elif suffix == ".png":
        mime = "image/png"
    else:
        mime = "image/jpeg"

    url = (
        f"{WECHAT_API_BASE}/cgi-bin/material/add_material"
        f"?access_token={access_token}&type=image"
    )
    with open(image_path, "rb") as f:
        files = {"media": (image_path.name, f, mime)}
        resp = requests.post(url, files=files, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    if "media_id" not in data or "url" not in data:
        raise RuntimeError(
            f"Image upload failed (missing media_id or url): {data.get('errmsg', data)}"
        )
    print(f"  ✓ Image uploaded → media_id: {data['media_id']}")
    return data["media_id"], data["url"]


def replace_local_images(
    html: str,
    access_token: str,
    base_dir: Path,
) -> tuple[str, list[str]]:
    """Replace local <img src="..."> paths in HTML with WeChat CDN URLs.

    Any ``src`` that does not start with ``http`` is treated as a path
    relative to *base_dir*, uploaded to WeChat, and replaced with the
    returned CDN URL.

    Returns:
        (updated_html, cdn_urls) — updated HTML and list of all CDN URLs
        in document order (for building a gallery).
    """
    cdn_urls: list[str] = []

    def _replace(m: re.Match) -> str:
        before, src, after = m.group(1), m.group(2), m.group(3)
        if src.startswith("http"):
            cdn_urls.append(src)
            return m.group(0)  # already absolute, leave untouched
        image_path = (base_dir / src).resolve()
        if not image_path.exists():
            print(f"  ⚠ Inline image not found, skipping: {image_path}", file=__import__('sys').stderr)
            return m.group(0)
        cdn_url = upload_image_for_content(access_token, image_path)
        cdn_urls.append(cdn_url)
        return f'<img{before}src="{cdn_url}"{after}>'

    updated = re.sub(r'<img([^>]*?)src="([^"]+)"([^>]*)>', _replace, html)
    return updated, cdn_urls


def capture_screenshot(url: str, output_path: Path) -> None:
    """Use Playwright to take a full-page screenshot of *url* and save it to *output_path* (PNG)."""
    import asyncio
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        raise SystemExit(
            "Playwright is required for screenshots.\n"
            "Install it with: pip install playwright && python -m playwright install chromium"
        )

    async def _capture() -> None:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page(viewport={"width": 1280, "height": 900})
                try:
                    await page.goto(url, wait_until="networkidle", timeout=30000)
                    await page.screenshot(path=str(output_path), full_page=False)
                finally:
                    await page.close()
            finally:
                await browser.close()

    asyncio.run(_capture())
    print(f"  ✓ Screenshot saved → {output_path}")


def build_wechat_gallery(cdn_urls: list[str]) -> str:
    """Build a WeChat-native swipeable image gallery HTML block.

    WeChat's renderer recognises ``js_editor_photogallery`` and renders it
    as a full-width carousel that users can swipe left/right.  Each <img>
    inside the section becomes one slide.
    """
    if not cdn_urls:
        return ""
    imgs = "\n".join(
        f'  <img src="{url}" style="width:100%;display:block;" />'
        for url in cdn_urls
    )
    return (
        '<section class="js_editor_photogallery" '  # WeChat native gallery marker
        'style="width:100%;overflow:hidden;margin:0 0 16px;">\n'
        + imgs
        + "\n</section>"
    )


def add_draft(access_token: str, articles: list[dict]) -> str:
    """POST articles to the WeChat draft box. Returns the draft media_id."""
    import requests

    url = (
        f"{WECHAT_API_BASE}/cgi-bin/draft/add"
        f"?access_token={access_token}"
    )
    payload = {"articles": articles}
    resp = requests.post(
        url,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        timeout=30,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("errcode", 0) != 0:
        raise RuntimeError(
            f"Draft upload failed (errcode {data.get('errcode')}): "
            f"{data.get('errmsg', data)}"
        )
    media_id = data.get("media_id", "")
    print(f"  ✓ Draft created → media_id: {media_id}")
    return media_id


# ---------------------------------------------------------------------------
# Cover image helpers
# ---------------------------------------------------------------------------

MEDIA_ID_CACHE_FILE = Path(".wechat_cover_media_id")


def load_cached_media_id(image_path: Path) -> str | None:
    """Return a cached media_id if the cover image hasn't changed."""
    if not MEDIA_ID_CACHE_FILE.exists():
        return None
    try:
        cache = json.loads(MEDIA_ID_CACHE_FILE.read_text(encoding="utf-8"))
        cached_mtime = cache.get("mtime")
        cached_media_id = cache.get("media_id")
        actual_mtime = image_path.stat().st_mtime
        if cached_mtime and abs(float(cached_mtime) - actual_mtime) < 1.0:
            print(f"  ✓ Reusing cached cover media_id: {cached_media_id}")
            return cached_media_id
    except Exception:
        pass
    return None


def save_cached_media_id(image_path: Path, media_id: str) -> None:
    cache = {"mtime": image_path.stat().st_mtime, "media_id": media_id}
    MEDIA_ID_CACHE_FILE.write_text(
        json.dumps(cache, indent=2), encoding="utf-8"
    )


def prepare_cover_image(cover_arg: Path | None, gif_path: Path | None) -> Path:
    """
    Return a path to a JPG cover image, creating one from the GIF if needed.
    WeChat thumbnail requirements: JPG, ≤ 1 MB (we down-scale to be safe).
    """
    # User supplied an explicit cover
    if cover_arg and cover_arg.exists():
        return cover_arg

    # Try to extract the first frame of the forecast GIF
    if gif_path and gif_path.exists():
        try:
            from PIL import Image

            img = Image.open(gif_path)
            img.seek(0)
            rgb = img.convert("RGB")
            # Down-scale if necessary to keep under 1 MB
            max_dim = 900
            if max(rgb.size) > max_dim:
                rgb.thumbnail((max_dim, max_dim), Image.LANCZOS)
            tmp = tempfile.NamedTemporaryFile(
                suffix=".jpg", delete=False, prefix="wechat_cover_"
            )
            rgb.save(tmp.name, "JPEG", quality=85, optimize=True)
            tmp.close()
            print(f"  ✓ Cover generated from GIF first frame → {tmp.name}")
            return Path(tmp.name)
        except Exception as exc:
            print(f"  ⚠ Could not extract GIF frame: {exc}", file=sys.stderr)

    raise SystemExit(
        "✗ No cover image available.\n"
        "  Provide one with --cover <path.jpg> or ensure "
        "wxcharts_forecast_wechat.gif exists in the project root."
    )


# ---------------------------------------------------------------------------
# Markdown → WeChat HTML conversion
# ---------------------------------------------------------------------------

WECHAT_HTML_STYLE = {
    "h1": (
        "font-size:22px;font-weight:bold;text-align:center;"
        "margin:16px 0 8px;color:#1a1a1a;"
    ),
    "h2": (
        "font-size:18px;font-weight:bold;margin:20px 0 8px;"
        "color:#2c7bb6;border-left:4px solid #2c7bb6;padding-left:8px;"
    ),
    "h3": "font-size:16px;font-weight:bold;margin:14px 0 6px;color:#333;",
    "p": "font-size:15px;line-height:1.8;margin:8px 0;color:#333;",
    "li": "font-size:15px;line-height:1.8;margin:4px 0;color:#333;",
    "ul": "padding-left:20px;margin:8px 0;",
    "ol": "padding-left:20px;margin:8px 0;",
    "hr": (
        "border:none;border-top:1px solid #e0e0e0;"
        "margin:20px 0;"
    ),
    "strong": "font-weight:bold;color:#1a1a1a;",
    "em": "font-style:italic;",
    "a": "color:#2c7bb6;text-decoration:none;",
}


def _preprocess_markdown(md_text: str) -> str:
    """
    Pre-process Markdown before conversion to fix WeChat rendering issues:

    1. Flatten nested lists: a parent list item whose text ends with '：' or ':'
       and is followed only by indented child items is rewritten as a bold
       paragraph + a flat child list.  This avoids the "empty bullet" WeChat
       renders for parent-only items in loose lists.

    2. Remove lines that are purely a list marker with no content ('* ', '- ').
    """
    lines = [l.rstrip('\r') for l in md_text.splitlines()]
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]

        # Detect a top-level list item that has NO body text after the marker
        # (only bold label ending with ： or :), and whose next non-blank lines
        # are all indented child items.
        top_match = re.match(r'^(\*|-|\d+\.)\s+(\*\*.+?[：:]?\*\*)\s*$', line)
        if top_match:
            label = top_match.group(2)  # e.g. **4月14日（周二）：**
            # Peek ahead: collect contiguous indented child lines
            j = i + 1
            children: list[str] = []
            while j < len(lines):
                child = lines[j]
                child_match = re.match(r'^(    |\t)(\*|-|\d+\.)\s+(.*)', child)
                if child_match:
                    children.append(child_match.group(3))  # strip indent
                    j += 1
                elif child.strip() == '':
                    # allow a single blank line inside the group
                    if j + 1 < len(lines) and re.match(r'^(    |\t)(\*|-)', lines[j + 1]):
                        j += 1
                        continue
                    break
                else:
                    break

            if children:
                # Emit label as a bold paragraph, then children as a flat list.
                # Two blank lines are needed so the markdown parser sees them
                # as separate block elements (paragraph + list).
                out.append('')
                out.append(f'**{label.strip("*")}**')
                out.append('')   # blank line separates <p> from <ul>
                for c in children:
                    out.append(f'* {c}')
                out.append('')
                i = j
                continue

        # Drop lines that are a bare list marker with no content
        if re.match(r'^(\*|-|\d+\.)\s*$', line):
            i += 1
            continue

        out.append(line)
        i += 1

    return '\n'.join(out)


def md_to_wechat_html(md_text: str) -> tuple[str, str]:
    """
    Convert Markdown to WeChat-compatible HTML with inline styles.

    Returns:
        (title, html_body) — title is the text of the first H1;
        html_body is everything else as HTML.
    """
    try:
        import markdown
    except ImportError:
        raise SystemExit(
            "The 'markdown' package is required.\n"
            "Install it with:  pip install markdown"
        )

    # Split off the first H1 as the article title
    lines = md_text.strip().splitlines()
    title = ""
    body_lines = []
    found_h1 = False
    for line in lines:
        if not found_h1 and line.startswith("# "):
            title = line[2:].strip()
            found_h1 = True
            continue
        body_lines.append(line)

    body_md = "\n".join(body_lines)

    # Fix nested-list and empty-bullet issues before conversion
    body_md = _preprocess_markdown(body_md)

    # Convert to HTML with basic extensions
    raw_html = markdown.markdown(
        body_md,
        extensions=["extra", "sane_lists"],
    )

    # Strip any empty <li> elements that slipped through
    raw_html = re.sub(r'<li[^>]*>\s*</li>', '', raw_html)

    # Apply inline styles via simple tag replacement
    styled = _apply_inline_styles(raw_html)
    return title, styled


def _apply_inline_styles(html: str) -> str:
    """Inject inline styles into common HTML tags."""
    tag_style_map = {
        "h1": WECHAT_HTML_STYLE["h1"],
        "h2": WECHAT_HTML_STYLE["h2"],
        "h3": WECHAT_HTML_STYLE["h3"],
        "p": WECHAT_HTML_STYLE["p"],
        "ul": WECHAT_HTML_STYLE["ul"],
        "ol": WECHAT_HTML_STYLE["ol"],
        "li": WECHAT_HTML_STYLE["li"],
        "strong": WECHAT_HTML_STYLE["strong"],
        "em": WECHAT_HTML_STYLE["em"],
        "a": WECHAT_HTML_STYLE["a"],
    }
    for tag, style in tag_style_map.items():
        # Replace opening tags (with no existing style)
        html = re.sub(
            rf"<{tag}(\s[^>]*)?>" ,
            lambda m, t=tag, s=style: (
                f"<{t}{m.group(1) or ''} style=\"{s}\">"
            ),
            html,
        )
    # Handle <hr> as self-closing
    html = re.sub(
        r"<hr\s*/?>",
        f"<hr style=\"{WECHAT_HTML_STYLE['hr']}\" />",
        html,
    )
    return html


def insert_image_under_current_weather_section(html: str, img_tag: str) -> tuple[str, bool]:
    """Insert image under the '当前天气形势' section.

    This is robust to placeholder text changes by targeting the section heading
    first, then removing common placeholder paragraph variants.
    """
    # Remove placeholder paragraph variants if present.
    html = re.sub(
        r'<p[^>]*>\s*[\[\(（]?\s*在此插入天气形势图\s*[\]\)）]?\s*</p>',
        '',
        html,
    )

    # Insert right after heading containing "当前天气形势".
    m = re.search(r'(<h[2-3][^>]*>[^<]*当前天气形势[^<]*</h[2-3]>)', html)
    if m:
        updated = html[:m.end()] + img_tag + html[m.end():]
        return updated, True

    # Fallback: append at top if heading not found.
    return img_tag + html, False


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Upload a Markdown weather article to a WeChat Official "
            "Account draft box."
        )
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=Path("wechat_article.md"),
        help="Path to the Markdown article (default: wechat_article.md)",
    )
    parser.add_argument(
        "--cover",
        type=Path,
        default=None,
        help=(
            "Path to a JPG/PNG cover image. "
            "If omitted, the first frame of wxcharts_forecast_wechat.gif is used."
        ),
    )
    parser.add_argument(
        "--author",
        default="Simpleweather",
        help="Article author name (default: Simpleweather)",
    )
    parser.add_argument(
        "--content-source-url",
        default="https://simpleweather.online",
        dest="content_source_url",
        help="'Read More' link (default: https://simpleweather.online)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the JSON payload without uploading anything to WeChat.",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignore cached cover media_id and re-upload the image.",
    )
    parser.add_argument(
        "--screenshot-url",
        default="https://simpleweather.online",
        dest="screenshot_url",
        help="URL to screenshot, used as article cover and inserted under '当前天气形势' (default: https://simpleweather.online).",
    )
    parser.add_argument(
        "--no-screenshot",
        action="store_true",
        dest="no_screenshot",
        help="Skip the website screenshot; fall back to GIF first-frame as cover.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    # --- 1. Read Markdown ---
    if not args.input.exists():
        print(f"✗ Input file not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    md_text = args.input.read_text(encoding="utf-8")
    print(f"✓ Loaded article from {args.input}")

    # --- 2. Convert to HTML ---
    print("  Converting Markdown → WeChat HTML…")
    title, html_body = md_to_wechat_html(md_text)
    if not title:
        title = "新西兰天气预报"
    print(f"  Title: {title}")

    # --- 3. Credentials ---
    if not args.dry_run:
        app_id = os.environ.get("WECHAT_APP_ID", "")
        app_secret = os.environ.get("WECHAT_APP_SECRET", "")
        if not app_id or not app_secret:
            print(
                "✗ WECHAT_APP_ID and WECHAT_APP_SECRET must be set.\n"
                "  Export them before running:\n"
                "    export WECHAT_APP_ID=wx...\n"
                "    export WECHAT_APP_SECRET=...",
                file=sys.stderr,
            )
            sys.exit(1)

    # --- 4. Cover image fallback (only used when --no-screenshot) ---
    gif_default = Path("wxcharts_forecast_wechat.gif")
    cover_path = prepare_cover_image(args.cover, gif_default) if args.no_screenshot else None

    # --- 5. Dry-run: just print the payload ---
    if args.dry_run:
        dry_html = re.sub(
            r'<img([^>]*?)src="(?!http)([^"]+)"([^>]*)>',
            lambda m: f'<img{m.group(1)}src="<would-upload:{m.group(2)}>"{m.group(3)}>',
            html_body,
        )
        if not args.no_screenshot:
            dry_tag = f'<img src="&lt;would-screenshot:{args.screenshot_url}&gt;" style="width:100%;display:block;margin:12px 0;" />'
            dry_html, _ = insert_image_under_current_weather_section(dry_html, dry_tag)
        payload = {
            "articles": [
                {
                    "title": title,
                    "author": args.author,
                    "content": dry_html,
                    "content_source_url": args.content_source_url,
                    "thumb_media_id": "<screenshot-media_id>" if not args.no_screenshot else "<cover-media_id>",
                    "show_cover_pic": 1,
                    "need_open_comment": 0,
                }
            ]
        }
        print("\n=== DRY RUN — payload that would be sent ===")
        preview = dict(payload)
        preview["articles"][0]["content"] = (
            dry_html[:300] + "…[truncated]" if len(dry_html) > 300 else dry_html
        )
        print(json.dumps(preview, indent=2, ensure_ascii=False))
        print(f"\n  Cover: {'screenshot of ' + args.screenshot_url if not args.no_screenshot else str(cover_path)}")
        print("=== End dry run ===")
        return

    # --- 6. Get access token ---
    print("\n[1/3] Authenticating with WeChat API…")
    access_token = get_access_token(app_id, app_secret)

    # --- 7. Screenshot → cover + inline image under 当前天气形势 ---
    screenshot_tmp: Path | None = None
    if not args.no_screenshot:
        print(f"[2/3] Taking screenshot of {args.screenshot_url}…")
        try:
            screenshot_tmp = Path(tempfile.mktemp(suffix=".png", prefix="wechat_screenshot_"))
            capture_screenshot(args.screenshot_url, screenshot_tmp)
            thumb_media_id, screenshot_cdn_url = upload_image_full(access_token, screenshot_tmp)
            # Insert screenshot into the 当前天气形势 section
            img_tag = f'<img src="{screenshot_cdn_url}" style="width:100%;display:block;margin:12px 0;" />'
            html_body, inserted = insert_image_under_current_weather_section(html_body, img_tag)
            if inserted:
                print("  ✓ Screenshot inserted under '当前天气形势' and set as cover")
            else:
                print("  ⚠ '当前天气形势' heading not found; screenshot inserted at top and set as cover")
        except Exception as exc:
            print(f"  ⚠ Screenshot failed: {exc}", file=sys.stderr)
            thumb_media_id = None
            screenshot_cdn_url = None
        finally:
            if screenshot_tmp and screenshot_tmp.exists():
                screenshot_tmp.unlink(missing_ok=True)
    else:
        # Fall back to GIF first-frame as cover
        print("[2/3] Handling cover image (GIF fallback)…")
        thumb_media_id = None
        if not args.no_cache:
            thumb_media_id = load_cached_media_id(cover_path)
        if not thumb_media_id:
            thumb_media_id = upload_permanent_image(access_token, cover_path)
            save_cached_media_id(cover_path, thumb_media_id)

    if not thumb_media_id:
        print("  ⚠ No cover image available; draft may be rejected by WeChat.", file=sys.stderr)

    # --- 7b. Upload local images inline (e.g. GIF under 欧洲中心 section) ---
    print("  Uploading inline images in article body…")
    base_dir = args.input.parent.resolve()
    html_body, _ = replace_local_images(html_body, access_token, base_dir)

    # --- 8. Upload draft ---
    print("[3/3] Uploading draft to WeChat…")
    article = {
        "title": title,
        "author": args.author,
        "content": html_body,
        "content_source_url": args.content_source_url,
        "thumb_media_id": thumb_media_id,
        "show_cover_pic": 1,
        "need_open_comment": 0,
    }
    draft_media_id = add_draft(access_token, [article])

    print(f"\n✅ Done! Draft media_id: {draft_media_id}")
    print(
        "   → Log in to the WeChat Official Account admin panel to preview "
        "and publish."
    )


if __name__ == "__main__":
    main()
