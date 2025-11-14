#!/usr/bin/env python3
"""
Generate a CSV that lists each channel from the pruned playlist along with its
origin (Free-TV vs iptv-org). Paths are resolved via ~/Kodi/.env (or the file
referenced by $KODI_ENV_PATH) so the script can run directly from the bin/
directory on the mounted share.
"""
from __future__ import annotations

import csv
import io
import os
import re
from pathlib import Path
from string import Template
from urllib.parse import urlparse
from typing import Dict, List


ENV_PATH = Path(os.environ.get("KODI_ENV_PATH", os.path.expanduser("~/Kodi/.env")))
SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent

DEFAULTS = {
    "BIN_DIR": SCRIPT_DIR,
    "M3U_DIR": REPO_ROOT / "m3u",
    "LOG_DIR": REPO_ROOT / "logs",
    "M3U": "pruned_tv.m3u",
    "FREE_TV_MASTER_M3U": REPO_ROOT / "m3u" / "free_tv_master.m3u",
    "IPTV_MASTER_M3U": REPO_ROOT / "m3u" / "iptv_master.m3u",
}

ATTR_REGEX = re.compile(r'([A-Za-z0-9_-]+)="([^"]*)"')


def parse_env(path: Path) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not path.exists():
        return env
    with io.open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def expand_value(value: str, env: Dict[str, str]) -> str:
    if not value:
        return ""
    templated = Template(value).safe_substitute(env)
    return os.path.expanduser(templated)


def resolve_path(env: Dict[str, str], key: str) -> Path:
    raw = env.get(key)
    if not raw:
        default = DEFAULTS.get(key)
        if default is None:
            raise KeyError(f"Missing default for {key}")
        raw = str(default)
    return Path(expand_value(str(raw), env))


def parse_playlist(path: Path) -> List[Dict[str, str]]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    entries: List[Dict[str, str]] = []
    for idx, line in enumerate(lines):
        if not line.startswith("#EXTINF"):
            continue
        url_idx = idx + 1
        if url_idx >= len(lines):
            break
        url = lines[url_idx].strip()
        meta, channel_name = line.split(",", 1)
        attrs = dict(ATTR_REGEX.findall(meta))
        entries.append(
            {
                "channel_name": channel_name.strip(),
                "tvg_id": attrs.get("tvg-id", ""),
                "url": url,
                "country": attrs.get("tvg-country", ""),
            }
        )
    return entries


def build_source_index(entries: List[Dict[str, str]], label: str) -> Dict[str, str]:
    index: Dict[str, str] = {}
    for entry in entries:
        identifiers = [
            (entry.get("tvg_id") or "").strip(),
            (entry.get("channel_name") or "").strip().lower(),
            (entry.get("url") or "").strip(),
        ]
        for ident in identifiers:
            if not ident:
                continue
            index.setdefault(ident, label)
    return index


def determine_source(
    entry: Dict[str, str],
    free_index: Dict[str, str],
    iptv_index: Dict[str, str],
) -> str:
    identifiers = [
        (entry.get("tvg_id") or "").strip(),
        (entry.get("channel_name") or "").strip().lower(),
        (entry.get("url") or "").strip(),
    ]
    for ident in identifiers:
        if not ident:
            continue
        if ident in free_index:
            return "Free-TV"
        if ident in iptv_index:
            return "iptv-org"

    parsed = urlparse(entry.get("url", ""))
    host = parsed.netloc or parsed.path.split("/")[0]
    host = host.split("@")[-1].split(":")[0]
    return host or "Unknown"


def main() -> None:
    env = {k: str(v) for k, v in DEFAULTS.items()}
    env.update(parse_env(ENV_PATH))

    pruned_name = env.get("M3U") or DEFAULTS["M3U"]
    pruned_path = resolve_path(env, "M3U_DIR") / pruned_name
    free_tv_path = Path(expand_value(env.get("FREE_TV_MASTER_M3U") or str(DEFAULTS["FREE_TV_MASTER_M3U"]), env))
    iptv_path = Path(expand_value(env.get("IPTV_MASTER_M3U") or str(DEFAULTS["IPTV_MASTER_M3U"]), env))
    log_dir = resolve_path(env, "LOG_DIR")
    output_csv = log_dir / "pruned_sources.csv"

    pruned_entries = parse_playlist(pruned_path)
    free_index = build_source_index(parse_playlist(free_tv_path), "Free-TV")
    iptv_index = build_source_index(parse_playlist(iptv_path), "iptv-org")

    for entry in pruned_entries:
        entry["source"] = determine_source(entry, free_index, iptv_index)

    log_dir.mkdir(parents=True, exist_ok=True)
    with output_csv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["channel_name", "tvg_id", "url", "country", "source"]
        )
        writer.writeheader()
        writer.writerows(pruned_entries)


if __name__ == "__main__":
    main()
