#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Favourites-only writer using paths from ~/Kodi/.env when --use-env
# Writes:  <M3U_DIR>/<M3U>, channel_cc_map.json, prune_report.csv
# Includes country in group-title so Kodi can group channels by country.

import argparse, csv, io, json, os, re
from typing import Dict, List, Tuple

ENV_PATH = os.path.expanduser("~/Kodi/.env")

def parse_env_file(path: str) -> Dict[str, str]:
    env: Dict[str, str] = {}
    with io.open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip("'").strip('"')
    return env

def _read_csv(path: str) -> Tuple[List[Dict[str, str]], Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    with io.open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        for r in reader:
            rows.append({k: (r.get(k) or "").strip() for k in headers})
    header_map = {h.strip().lower(): h for h in (reader.fieldnames or [])}
    return rows, header_map

def set_attr(extinf: str, key: str, val: str) -> str:
    if not val: return extinf
    pat = re.compile(rf'({re.escape(key)}=")[^"]*(")')
    if pat.search(extinf): return pat.sub(rf'\1{val}\2', extinf)
    pos = extinf.find(",")
    if pos == -1: return f'{extinf} {key}="{val}"'
    return f'{extinf[:pos]} {key}="{val}"{extinf[pos:]}'

def _get(r: Dict[str, str], hdr: Dict[str, str], *cands: str) -> str:
    for c in cands:
        k = hdr.get(c.strip().lower())
        if not k: continue
        v = r.get(k, "") or ""
        v = str(v).strip()
        if v: return v
    return ""

def _is_fav(r: Dict[str, str], hdr: Dict[str, str]) -> bool:
    val = (_get(r, hdr, "Favourite", "Favorite", "Include") or "").lower()
    return val in {"1","true","yes","y"}

def write_pruned_m3u_from_favs(favs, hdr, out_path, cc_map_path=None) -> Tuple[int,int,int]:
    written = sk_notfav = sk_nourl = 0
    ch2cc: Dict[str, str] = {}
    with io.open(out_path, "w", encoding="utf-8") as fo:
        fo.write("#EXTM3U\n")
        for r in favs:
            if not _is_fav(r, hdr): sk_notfav += 1; continue
            url = _get(r, hdr, "Url","URL","StreamUrl")
            if not url: sk_nourl += 1; continue
            name = _get(r, hdr, "ChannelName","Name","Channel")
            tvg  = _get(r, hdr, "TvgId","tvg-id")
            cc   = _get(r, hdr, "Country","tvg-country")
            grp  = _get(r, hdr, "GroupTitle","Group","Category")
            ext = "#EXTINF:-1"
            if tvg: ext = set_attr(ext, "tvg-id", tvg)
            if cc:  ext = set_attr(ext, "tvg-country", cc)
            group_pieces = [val for val in (cc, grp) if val]
            if group_pieces:
                ext = set_attr(ext, "group-title", " - ".join(group_pieces))
            ext = f"{ext},{name or ''}"
            fo.write(ext + "\n"); fo.write(url + "\n")
            ch2cc[name or url] = cc or ""
            written += 1
    if cc_map_path:
        with io.open(cc_map_path, "w", encoding="utf-8") as f:
            json.dump(ch2cc, f, ensure_ascii=False, indent=2, sort_keys=True)
    return written, sk_notfav, sk_nourl

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--use-env", action="store_true",
                    help="Use ONLY paths from ~/Kodi/.env (TV_FAV, M3U_DIR, LOG_DIR, M3U).")
    # legacy args (ignored with --use-env)
    ap.add_argument("--m3u-dir"); ap.add_argument("--out-dir"); ap.add_argument("--fav")
    ap.add_argument("--country-map"); ap.add_argument("--report")
    args = ap.parse_args()

    if args.use_env:
        env = parse_env_file(ENV_PATH)
        TV_FAV = env["TV_FAV"]; M3U_DIR = env["M3U_DIR"]; LOG_DIR = env["LOG_DIR"]; M3U_NAME = env["M3U"]
        pruned_out = os.path.join(M3U_DIR, M3U_NAME)
        cc_map     = os.path.join(M3U_DIR, "channel_cc_map.json")
        report     = os.path.join(LOG_DIR, "prune_report.csv")
    else:
        # (compat mode)
        TV_FAV = args.fav; M3U_DIR = args.m3u_dir
        pruned_out = os.path.join(args.out_dir, "pruned.m3u")
        cc_map = args.country_map; report = args.report
        LOG_DIR = report and os.path.dirname(report) or "."

    os.makedirs(M3U_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)

    favs, hdr = _read_csv(TV_FAV)
    written, sk_notfav, sk_nourl = write_pruned_m3u_from_favs(favs, hdr, pruned_out, cc_map_path=cc_map)

    if report:
        with io.open(report, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["artifact","path","rows_written","skipped_not_favourite","skipped_no_url"])
            w.writerow(["pruned.m3u (Favourite-only)", pruned_out, written, sk_notfav, sk_nourl])

    print({"pruned_m3u": {"path": pruned_out, "written": written,
                          "skipped_not_favourite": sk_notfav, "skipped_no_url": sk_nourl}})

if __name__ == "__main__":
    main()
