#!/usr/bin/env python3

from __future__ import annotations

import json
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_CITIES = [
    {"name": "Auckland", "region": "auckland", "location": "auckland"},
    {"name": "Wellington", "region": "wellington", "location": "wellington"},
    {"name": "Christchurch", "region": "canterbury", "location": "christchurch"},
    {"name": "Hamilton", "region": "waikato", "location": "hamilton"},
    {"name": "Tauranga", "region": "bay-of-plenty", "location": "tauranga"},
    {"name": "Dunedin", "region": "otago", "location": "dunedin"},
]


def now_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value)
    ascii_only = normalized.encode("ascii", "ignore").decode("ascii")
    return ascii_only.lower().replace("&", "and").replace(" ", "-")


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, data: dict[str, Any]) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text())
