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

    # --- 4. Cover image ---
    gif_default = Path("wxcharts_forecast_wechat.gif")
    cover_path = prepare_cover_image(args.cover, gif_default)

    # --- 5. Dry-run: just print the payload ---
    if args.dry_run:
        payload = {
            "articles": [
                {
                    "title": title,
                    "author": args.author,
                    "content": html_body,
                    "content_source_url": args.content_source_url,
                    "thumb_media_id": "<would-be-uploaded>",
                    "show_cover_pic": 1,
                    "need_open_comment": 0,
                }
            ]
        }
        print("\n=== DRY RUN — payload that would be sent ===")
        # Print a truncated version so the terminal isn't flooded
        preview = dict(payload)
        preview["articles"][0]["content"] = (
            html_body[:300] + "…[truncated]" if len(html_body) > 300 else html_body
        )
        print(json.dumps(preview, indent=2, ensure_ascii=False))
        print(f"\n  Cover image: {cover_path}")
        print("=== End dry run ===")
        return

    # --- 6. Get access token ---
    print("\n[1/3] Authenticating with WeChat API…")
    access_token = get_access_token(app_id, app_secret)

    # --- 7. Upload cover image (with caching) ---
    print("[2/3] Handling cover image…")
    thumb_media_id: str | None = None
    if not args.no_cache:
        thumb_media_id = load_cached_media_id(cover_path)
    if not thumb_media_id:
        thumb_media_id = upload_permanent_image(access_token, cover_path)
        save_cached_media_id(cover_path, thumb_media_id)

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
