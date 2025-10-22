#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Favourites-only writer using paths from ~/Kodi/.env when --use-env
# Writes:  <M3U_DIR>/<M3U>, channel_cc_map.json, prune_report.csv
# Includes country in group-title so Kodi can group channels by country.

import argparse, csv, io, json, os, re
from datetime import datetime
from typing import Dict, List, Tuple, Optional

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

def _channel_key(name: str, tvg: str, url: str) -> str:
    for cand in (name, tvg, url):
        if cand:
            key = cand.strip()
            if key:
                return key
    return url.strip()

def _build_overrides(channel_meta: Dict[str, Dict[str, str]]) -> Dict[str, str]:
    overrides: Dict[str, str] = {}
    for chan, meta in channel_meta.items():
        cc = (meta.get("country") or "").strip()
        if cc:
            overrides[chan] = cc
    return dict(sorted(overrides.items(), key=lambda kv: kv[0].lower()))

def _write_channel_map(channel_meta: Dict[str, Dict[str, str]], cc_map_path: str) -> Dict[str, str]:
    overrides = _build_overrides(channel_meta)
    with io.open(cc_map_path, "w", encoding="utf-8") as f:
        json.dump(dict(sorted(overrides.items(), key=lambda kv: kv[0].lower())), f, ensure_ascii=False, indent=2)
    return overrides

def _find_cc_profile_template(env: Dict[str, str], fallback_dir: Optional[str]=None) -> Optional[str]:
    candidates = [
        env.get("CC_TO_PROFILE_TEMPLATE"),
        env.get("CC_PROFILE_TEMPLATE"),
        env.get("CHANNEL_CC_TEMPLATE"),
        os.environ.get("CC_TO_PROFILE_TEMPLATE"),
        os.environ.get("CC_PROFILE_TEMPLATE"),
        os.environ.get("CHANNEL_CC_TEMPLATE"),
    ]
    if fallback_dir:
        candidates.append(os.path.join(fallback_dir, "cc_to_profile.json"))

    candidates.extend([
        "/storage/.kodi/userdata/addon_data/service.channel_vpn_cc/cc_to_profile.json",
        os.path.expanduser("~/Kodi/cc_to_profile.json")
    ])

    seen = set()
    for cand in candidates:
        if not cand: continue
        expanded = os.path.expanduser(cand)
        if expanded in seen:
            continue
        seen.add(expanded)
        if os.path.isfile(expanded):
            return expanded
    return None

def _update_cc_profile(template_path: str, overrides: Dict[str, str]) -> None:
    if not overrides:
        return
    try:
        with io.open(template_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return
    except json.JSONDecodeError:
        return

    mappings = data.setdefault("mappings", {})
    existing = mappings.get("channel_overrides")
    if isinstance(existing, dict):
        for chan, val in existing.items():
            if chan not in overrides and isinstance(val, str) and val.strip():
                overrides[chan] = val.strip()
    mappings["channel_overrides"] = dict(sorted(overrides.items(), key=lambda kv: kv[0].lower()))
    data["generated_at"] = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

    with io.open(template_path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)

def write_pruned_m3u_from_favs(favs, hdr, out_path, cc_map_path=None) -> Tuple[int,int,int,Dict[str,Dict[str,str]],Dict[str,str]]:
    written = sk_notfav = sk_nourl = 0
    channel_meta: Dict[str, Dict[str, str]] = {}
    overrides: Dict[str, str] = {}
    with io.open(out_path, "w", encoding="utf-8") as fo:
        fo.write("#EXTM3U\n")
        for r in favs:
            if not _is_fav(r, hdr): sk_notfav += 1; continue
            url = _get(r, hdr, "Url","URL","StreamUrl")
            if not url: sk_nourl += 1; continue
            name = _get(r, hdr, "ChannelName","Name","Channel")
            tvg  = _get(r, hdr, "TvgId","tvg-id")
            cc_raw = _get(r, hdr, "Country","tvg-country")
            cc = cc_raw.upper() if cc_raw else ""
            grp  = _get(r, hdr, "GroupTitle","Group","Category")
            logo = _get(r, hdr, "Logo", "tvg-logo", "TvgLogo")
            ext = "#EXTINF:-1"
            if tvg: ext = set_attr(ext, "tvg-id", tvg)
            if cc:  ext = set_attr(ext, "tvg-country", cc)
            if logo:
                ext = set_attr(ext, "tvg-logo", logo)

            country_labels = {
                "UK": "United Kingdom",
                "GB": "United Kingdom",
                "DE": "Germany",
                "CA": "Canada",
                "US": "USA",
            }
            country_label = country_labels.get(cc) or (cc if cc else grp or "")
            if country_label:
                ext = set_attr(ext, "group-title", country_label)

            ext = f"{ext},{name or ''}"
            fo.write(ext + "\n"); fo.write(url + "\n")

            key = _channel_key(name, tvg, url)
            meta = channel_meta.setdefault(key, {
                "name": name,
                "tvg_id": tvg,
                "country": cc,
                "logo": logo
            })
            if cc:
                meta["country"] = cc
                overrides[key] = cc
            if logo:
                meta["logo"] = logo
            written += 1
    if cc_map_path:
        overrides = _write_channel_map(channel_meta, cc_map_path)
    return written, sk_notfav, sk_nourl, channel_meta, overrides

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--use-env", action="store_true",
                    help="Use ONLY paths from ~/Kodi/.env (TV_FAV, M3U_DIR, LOG_DIR, M3U).")
    # legacy args (ignored with --use-env)
    ap.add_argument("--m3u-dir"); ap.add_argument("--out-dir"); ap.add_argument("--fav")
    ap.add_argument("--country-map"); ap.add_argument("--report")
    args = ap.parse_args()

    env: Dict[str, str] = {}

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
    written, sk_notfav, sk_nourl, channel_meta, overrides = write_pruned_m3u_from_favs(
        favs, hdr, pruned_out, cc_map_path=cc_map)

    template_path = _find_cc_profile_template(env, os.path.dirname(cc_map) if cc_map else None)
    if template_path:
        _update_cc_profile(template_path, overrides.copy())

    if report:
        with io.open(report, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["artifact","path","rows_written","skipped_not_favourite","skipped_no_url"])
            w.writerow(["pruned.m3u (Favourite-only)", pruned_out, written, sk_notfav, sk_nourl])

    print({"pruned_m3u": {"path": pruned_out, "written": written,
                          "skipped_not_favourite": sk_notfav, "skipped_no_url": sk_nourl}})

if __name__ == "__main__":
    main()
