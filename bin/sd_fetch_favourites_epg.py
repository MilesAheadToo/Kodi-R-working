#!/usr/bin/env python3
"""
sd_fetch_favourites_epg.py
- Reads the favourites list (Favourite=1) from tv_favourites.csv
- Maps those channels to Schedules Direct station ids via sd_epg_report.csv
- Runs tv_grab_zz_sdjson limited to that station set (when supported)
- Writes a trimmed XMLTV (optionally gzipped) containing only the favourite channels
"""
from __future__ import annotations

import argparse
import csv
import gzip
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple
import xml.etree.ElementTree as ET


def load_env(path: Path) -> Dict[str, str]:
    env: Dict[str, str] = {}
    if not path.exists():
        return env
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        env[key.strip()] = value.strip().strip("'").strip('"')
    return env


def norm_name(value: str) -> str:
    return (value or "").strip().lower()


def read_favourites(csv_path: Path) -> List[Dict[str, str]]:
    favourites: List[Dict[str, str]] = []
    if not csv_path.exists():
        raise FileNotFoundError(f"tv_favourites.csv not found: {csv_path}")
    with csv_path.open(encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            fav_flag = (row.get("Favourite") or "").strip().lower()
            if fav_flag in {"1", "true", "yes", "y"}:
                favourites.append(row)
    return favourites


def load_match_report(report_path: Path) -> List[Dict[str, str]]:
    if not report_path.exists():
        raise FileNotFoundError(f"Match report not found: {report_path}")
    with report_path.open(encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def resolve_station_ids(
    favourites: Sequence[Dict[str, str]],
    match_rows: Sequence[Dict[str, str]],
) -> Tuple[Set[str], List[Tuple[str, Optional[str]]]]:
    match_by_name: Dict[str, Dict[str, str]] = {
        norm_name(row.get("name", "")): row for row in match_rows
    }
    resolved: Set[str] = set()
    coverage: List[Tuple[str, Optional[str]]] = []
    for fav in favourites:
        name = fav.get("ChannelName", "").strip()
        key = norm_name(name)
        matched = match_by_name.get(key)
        station_id: Optional[str] = None
        if matched:
            station_id = (matched.get("matched_id") or "").strip()
        # Fall back to the TvgId if it already looks like an SD station id
        if not station_id:
            tid = (fav.get("TvgId") or "").strip()
            if tid.startswith("I") and "schedulesdirect" in tid:
                station_id = tid
        if station_id:
            resolved.add(station_id)
        coverage.append((name, station_id))
    return resolved, coverage


def write_station_list(path: Path, station_ids: Iterable[str]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for station_id in sorted(set(station_ids)):
            fh.write(f"{station_id}\n")


def prune_xmltv(
    src: Path,
    dst: Path,
    keep_ids: Set[str],
    gzip_output: bool = True,
) -> None:
    if not keep_ids:
        raise ValueError("No station ids to retain when pruning XMLTV")
    if src.suffix == ".gz" or src.name.endswith(".xml.gz"):
        with gzip.open(src, "rb") as fh:
            data = fh.read()
    else:
        data = src.read_bytes()
    root = ET.fromstring(data)
    out_root = ET.Element("tv", root.attrib)

    kept_channels = set()
    for ch in root.findall("channel"):
        cid = ch.get("id", "")
        if cid in keep_ids and cid not in kept_channels:
            out_root.append(ch)
            kept_channels.add(cid)

    for pr in root.findall("programme"):
        if pr.get("channel", "") in kept_channels:
            out_root.append(pr)

    serialized = ET.tostring(out_root, encoding="utf-8")
    if gzip_output:
        with gzip.open(dst, "wb") as fh:
            fh.write(serialized)
    else:
        dst.write_bytes(serialized)


def run_tv_grab(
    station_file: Path,
    days: int,
    output_xml: Path,
) -> Tuple[bool, subprocess.CompletedProcess]:
    cmd = [
        "tv_grab_zz_sdjson",
        "--days",
        str(days),
        "--output",
        str(output_xml),
        "--channel-file",
        str(station_file),
    ]
    try:
        completed = subprocess.run(cmd, check=False, text=True, capture_output=True)
    except FileNotFoundError as exc:
        raise FileNotFoundError(
            "tv_grab_zz_sdjson was not found in PATH; install the XMLTV SchedulesDirect grabber."
        ) from exc

    if completed.returncode == 0:
        return True, completed

    stderr = completed.stderr or ""
    if "unknown option" in stderr.lower() or "--channel-file" in stderr:
        # Retry without the channel restriction; caller will prune afterwards.
        fallback_cmd = [
            "tv_grab_zz_sdjson",
            "--days",
            str(days),
            "--output",
            str(output_xml),
        ]
        fallback = subprocess.run(fallback_cmd, check=False, text=True, capture_output=True)
        return False, fallback

    return False, completed


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Download SchedulesDirect XMLTV limited to Favourite=1 channels."
    )
    parser.add_argument("--env", default="~/Kodi/.env", help="Override path to .env file")
    parser.add_argument(
        "--days", type=int, default=int(os.environ.get("EPG_DAYS", "7")), help="Number of days to fetch"
    )
    parser.add_argument(
        "--output",
        help="Explicit output path (default: EPG_DIR/epg_sd_favourites.xml.gz)",
    )
    parser.add_argument(
        "--no-gzip",
        action="store_true",
        help="Write plain XML instead of gzipping the result",
    )
    parser.add_argument(
        "--skip-refresh",
        action="store_true",
        help="Do not run sd_daily_match_epg_m3u.py when the match report is missing",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print the resolved station ids; do not call tv_grab_zz_sdjson",
    )
    args = parser.parse_args(argv)

    env_path = Path(args.env).expanduser()
    ENV = load_env(env_path)

    script_dir = Path(__file__).resolve().parent
    bin_dir = Path(ENV.get("BIN_DIR", script_dir))
    m3u_dir = Path(ENV.get("M3U_DIR", "~/Kodi/m3u")).expanduser()
    epg_dir = Path(ENV.get("EPG_DIR", "~/Kodi/epg")).expanduser()
    log_dir = Path(ENV.get("LOG_DIR", "~/Kodi/logs")).expanduser()
    fav_candidates = [
        Path(ENV.get("TV_FAV", "~/Kodi/tv_favourites.csv")).expanduser(),
        script_dir.parent / "tv_favourites.csv",
    ]
    fav_path = next((p for p in fav_candidates if p.exists()), fav_candidates[0])

    report_candidates = [
        log_dir / "sd_m3u_epg_report.csv",
        script_dir.parent / "logs" / "sd_m3u_epg_report.csv",
    ]
    report_path = next((p for p in report_candidates if p.exists()), report_candidates[0])
    output_path = Path(args.output or (epg_dir / "epg_sd_matched.xml.gz"))

    epg_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    if not report_path.exists() and not args.skip_refresh:
        match_script = bin_dir / "sd_daily_match_epg_m3u.py"
        if match_script.exists():
            print(f"[info] Match report missing; running {match_script.name} to refresh mappings...")
            result = subprocess.run([sys.executable, str(match_script)], check=False)
            if result.returncode != 0:
                print(
                    f"[warn] Matching script exited with {result.returncode}; continuing anyway.",
                    file=sys.stderr,
                )
        else:
            print(
                "[warn] Match report missing and sd_daily_match_epg_m3u.py not found; "
                "favourite -> station mapping may be incomplete.",
                file=sys.stderr,
            )

    favourites = read_favourites(fav_path)
    if not favourites:
        print("No favourites marked with Favourite=1 were found in tv_favourites.csv.")
        return 0

    try:
        match_rows = load_match_report(report_path)
    except FileNotFoundError as exc:
        print(
            f"ERROR: {exc}. Run sd_daily_match_epg_m3u.py first or pass --skip-refresh to override.",
            file=sys.stderr,
        )
        return 1

    station_ids, coverage = resolve_station_ids(favourites, match_rows)

    coverage_csv = log_dir / "sd_favourite_station_coverage.csv"
    with coverage_csv.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(["ChannelName", "StationId"])
        writer.writerows(coverage)

    if not station_ids:
        print(
            "No favourites resolved to SchedulesDirect station ids. "
            "Check sd_favourite_station_coverage.csv for details.",
            file=sys.stderr,
        )
        return 1

    station_list_path = log_dir / "sd_favourite_station_ids.txt"
    write_station_list(station_list_path, station_ids)
    print(f"[info] Resolved {len(station_ids)} station ids -> {station_list_path}")

    if args.dry_run:
        print("[info] Dry-run complete; skipping download.")
        return 0

    output_xml = output_path.with_suffix(".xml") if output_path.suffix == ".gz" else output_path
    gzip_output = not args.no_gzip and output_path.suffix == ".gz"

    with tempfile.NamedTemporaryFile("w", delete=False, encoding="utf-8") as tmp:
        tmp_path = Path(tmp.name)
        for station_id in sorted(station_ids):
            tmp.write(f"{station_id}\n")

    try:
        filtered, grab_result = run_tv_grab(tmp_path, args.days, output_xml)
    finally:
        tmp_path.unlink(missing_ok=True)

    if grab_result.returncode != 0:
        print(grab_result.stdout, file=sys.stdout)
        print(grab_result.stderr, file=sys.stderr)
        return grab_result.returncode

    if not filtered:
        # tv_grab_zz_sdjson did not accept --channel-file; prune the download now.
        print("[info] tv_grab_zz_sdjson ran without channel filtering; pruning favourites locally.")
        prune_xmltv(output_xml, output_path, station_ids, gzip_output=gzip_output)
        if output_xml != output_path and output_xml.exists():
            output_xml.unlink()
    else:
        if gzip_output:
            prune_xmltv(output_xml, output_path, station_ids, gzip_output=True)
            if output_xml != output_path and output_xml.exists():
                output_xml.unlink()
        else:
            print(f"[info] Wrote {output_xml}")
            return 0

    print(f"[info] Favourite-only XMLTV written to {output_path}")
    print(f"[info] Coverage report: {coverage_csv}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
