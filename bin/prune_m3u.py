#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Favourites-only writer using paths from ~/Kodi/.env when --use-env
# Writes:  <M3U_DIR>/<M3U>, channel_cc_map.json, prune_report.csv
# Includes country in group-title so Kodi can group channels by country.

import argparse, csv, io, json, os, re
from datetime import datetime
from dataclasses import dataclass, field
from typing import Dict, List, Tuple, Optional, Sequence

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

@dataclass
class MasterEntry:
    name: str
    tvg_id: str
    url: str
    props: List[str]
    priority: int = 0
    source: str = ""
    attrs: Dict[str, str] = field(default_factory=dict)

def _norm_key(val: Optional[str]) -> str:
    return (val or "").strip().lower()

def _split_url_and_props(value: str) -> Tuple[str, List[str]]:
    if not value:
        return "", []
    url = ""
    props: List[str] = []
    for line in value.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#KODIPROP:"):
            props.append(stripped)
        else:
            url = stripped
    if not url:
        url = value.strip()
    return url, props

def _merge_props(*prop_groups: List[str]) -> List[str]:
    merged: List[str] = []
    seen = set()
    for group in prop_groups:
        for line in group:
            key = line.strip()
            if not key or not key.startswith("#KODIPROP:"):
                continue
            if key in seen:
                continue
            seen.add(key)
            merged.append(key)
    return merged

def _attr_lookup(attrs: Optional[Dict[str, str]], *keys: str) -> str:
    if not attrs:
        return ""
    for key in keys:
        norm = key.lower()
        if norm in attrs:
            val = attrs[norm]
            if val:
                return val
    return ""

def _source_category(raw: Optional[str]) -> str:
    if not raw:
        return "Unknown"
    if raw == "free_tv_master":
        return "Free-TV"
    if raw == "iptv_master":
        return "IPTV-org"
    return "Unknown"

def _parse_master_playlist(sources: Optional[Sequence[Tuple[str, str]]]) -> Dict[str, Dict[str, MasterEntry]]:
    lookup: Dict[str, Dict[str, MasterEntry]] = {"by_url": {}, "by_tvg": {}, "by_name": {}}
    if not sources:
        return lookup
    iterable = list(sources)
    for priority, (raw_path, label) in enumerate(iterable):
        if not raw_path:
            continue
        path = os.path.expanduser(raw_path)
        try:
            fh = io.open(path, "r", encoding="utf-8", errors="ignore")
        except FileNotFoundError:
            continue
        with fh:
            current_ext = None
            props: List[str] = []
            for raw in fh:
                line = raw.rstrip("\n")
                if line.startswith("#EXTINF:"):
                    current_ext = line
                    props = []
                    continue
                if current_ext and line.startswith("#"):
                    if line.startswith("#KODIPROP:"):
                        props.append(line.strip())
                    continue
                if current_ext and line and not line.startswith("#"):
                    url = line.strip()
                    raw_attrs = dict(re.findall(r'([\w\-]+)="(.*?)"', current_ext))
                    attrs: Dict[str, str] = {}
                    for k, v in raw_attrs.items():
                        low = k.lower()
                        attrs[low] = v
                        attrs.setdefault(low.replace("_","-"), v)
                        attrs.setdefault(low.replace("-","_"), v)
                    name = current_ext.split(",", 1)[1].strip() if "," in current_ext else ""
                    entry = MasterEntry(
                        name=name,
                        tvg_id=_attr_lookup(attrs, "tvg-id", "tvg_id", "tvgid"),
                        url=url,
                        props=props[:],
                        priority=priority,
                        source=label or os.path.basename(path) or "unknown",
                        attrs=attrs
                    )
                    url_key = entry.url.strip()
                    if url_key and url_key not in lookup["by_url"]:
                        lookup["by_url"][url_key] = entry
                    tvg_key = _norm_key(entry.tvg_id)
                    if tvg_key and tvg_key not in lookup["by_tvg"]:
                        lookup["by_tvg"][tvg_key] = entry
                    name_key = _norm_key(entry.name)
                    if name_key and name_key not in lookup["by_name"]:
                        lookup["by_name"][name_key] = entry
                    current_ext = None
                    props = []
    return lookup

def _find_master_entry(lookup: Optional[Dict[str, Dict[str, MasterEntry]]],
                       name: str, tvg_id: str, url: str) -> Optional[MasterEntry]:
    if not lookup:
        return None
    url_key = url.strip()
    if url_key and url_key in lookup["by_url"]:
        return lookup["by_url"][url_key]
    tvg_key = _norm_key(tvg_id)
    if tvg_key and tvg_key in lookup["by_tvg"]:
        return lookup["by_tvg"][tvg_key]
    if tvg_key and "@" in tvg_key:
        base = tvg_key.split("@", 1)[0]
        if base in lookup["by_tvg"]:
            return lookup["by_tvg"][base]
    if tvg_key and "." in tvg_key:
        lower = tvg_key.lower()
        if lower in lookup["by_tvg"]:
            return lookup["by_tvg"][lower]
    name_key = _norm_key(name)
    if name_key and name_key in lookup["by_name"]:
        return lookup["by_name"][name_key]
    return None

def write_pruned_m3u_from_favs(favs, hdr, out_path, cc_map_path=None,
                               master_lookup: Optional[Dict[str, Dict[str, MasterEntry]]] = None,
                               source_report_path: Optional[str] = None
                               ) -> Tuple[int,int,int,Dict[str,Dict[str,str]],Dict[str,str]]:
    written = sk_notfav = sk_nourl = 0
    channel_meta: Dict[str, Dict[str, str]] = {}
    overrides: Dict[str, str] = {}
    source_rows: List[Dict[str, str]] = []
    with io.open(out_path, "w", encoding="utf-8") as fo:
        fo.write("#EXTM3U\n")
        for r in favs:
            if not _is_fav(r, hdr): sk_notfav += 1; continue
            url_raw = _get(r, hdr, "Url","URL","StreamUrl")
            stream_url, inline_props = _split_url_and_props(url_raw)
            if not stream_url: sk_nourl += 1; continue
            name = _get(r, hdr, "ChannelName","Name","Channel")
            tvg_seed = _get(r, hdr, "TvgId","tvg-id")
            master_entry = _find_master_entry(master_lookup, name, tvg_seed, stream_url)
            master_attrs = master_entry.attrs if master_entry else {}

            tvg  = tvg_seed or _attr_lookup(master_attrs, "tvg-id","tvg_id","tvgid")
            cc_raw = _get(r, hdr, "Country","tvg-country") or _attr_lookup(master_attrs, "tvg-country","country")
            cc = cc_raw.upper() if cc_raw else ""
            grp  = _get(r, hdr, "GroupTitle","Group","Category") or _attr_lookup(master_attrs, "group-title","group","category")
            logo = _get(r, hdr, "Logo", "tvg-logo", "TvgLogo") or _attr_lookup(master_attrs, "tvg-logo","logo")
            if not name and master_entry and master_entry.name:
                name = master_entry.name
            ext = "#EXTINF:-1"
            if tvg: ext = set_attr(ext, "tvg-id", tvg)
            if cc:  ext = set_attr(ext, "tvg-country", cc)
            if logo:
                ext = set_attr(ext, "tvg-logo", logo)
            tvg_name_attr = _attr_lookup(master_attrs, "tvg-name","tvg_name")
            if tvg_name_attr:
                ext = set_attr(ext, "tvg-name", tvg_name_attr)

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
            fo.write(ext + "\n")

            prop_lines = _merge_props(inline_props, master_entry.props if master_entry else [])
            for line in prop_lines:
                fo.write(line + "\n")
            final_url = (master_entry.url if master_entry else stream_url).strip()
            fo.write(final_url + "\n")
            raw_source = master_entry.source if master_entry else "favourites_only"
            source_label = _source_category(raw_source)

            key = _channel_key(name, tvg, final_url)
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
            source_rows.append({
                "channel_name": name or "",
                "tvg_id": tvg or "",
                "url": final_url,
                "country": cc,
                "source": source_label,
            })
    if cc_map_path:
        overrides = _write_channel_map(channel_meta, cc_map_path)
    if source_report_path:
        with io.open(source_report_path, "w", encoding="utf-8", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["channel_name","tvg_id","url","country","source"])
            writer.writeheader()
            for row in source_rows:
                writer.writerow(row)
    return written, sk_notfav, sk_nourl, channel_meta, overrides

def _build_master_sources(*entries: Tuple[Optional[str], str]) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    seen = set()
    for path, label in entries:
        if not path:
            continue
        expanded = os.path.expanduser(path)
        if expanded in seen:
            continue
        seen.add(expanded)
        out.append((expanded, label))
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--use-env", action="store_true",
                    help="Use ONLY paths from ~/Kodi/.env (TV_FAV, M3U_DIR, LOG_DIR, M3U).")
    # legacy args (ignored with --use-env)
    ap.add_argument("--m3u-dir"); ap.add_argument("--out-dir"); ap.add_argument("--fav")
    ap.add_argument("--country-map"); ap.add_argument("--report")
    ap.add_argument("--master-m3u", action="append",
                    help="Optional master playlist(s) to copy #KODIPROP lines from (highest priority first).")
    ap.add_argument("--source-report",
                    help="Optional CSV path to record each pruned channel's source playlist.")
    args = ap.parse_args()

    env: Dict[str, str] = {}

    if args.use_env:
        env = parse_env_file(ENV_PATH)
        TV_FAV = env["TV_FAV"]; M3U_DIR = env["M3U_DIR"]; LOG_DIR = env["LOG_DIR"]; M3U_NAME = env["M3U"]
        pruned_out = os.path.join(M3U_DIR, M3U_NAME)
        cc_map     = os.path.join(M3U_DIR, "channel_cc_map.json")
        report     = os.path.join(LOG_DIR, "prune_report.csv")
        custom_sources: List[Tuple[str, str]] = []
        if args.master_m3u:
            for idx, path in enumerate(args.master_m3u, 1):
                custom_sources.append((path, f"custom_{idx}"))
        default_sources: List[Tuple[Optional[str], str]] = [
            (env.get("FREE_TV_MASTER_M3U") or os.path.join(M3U_DIR, "free_tv_master.m3u"), "free_tv_master"),
            (env.get("IPTV_MASTER_M3U") or os.path.join(M3U_DIR, "iptv_master.m3u"), "iptv_master"),
            (env.get("MASTER_M3U") or os.path.join(M3U_DIR, "master.m3u"), "combined_master"),
        ]
        master_paths = _build_master_sources(*(custom_sources + default_sources))
        source_report_path = args.source_report or os.path.join(LOG_DIR, "pruned_source_report.csv")
    else:
        # (compat mode)
        TV_FAV = args.fav; M3U_DIR = args.m3u_dir
        pruned_out = os.path.join(args.out_dir, "pruned.m3u")
        cc_map = args.country_map; report = args.report
        LOG_DIR = report and os.path.dirname(report) or "."
        custom_sources = []
        if args.master_m3u:
            for idx, path in enumerate(args.master_m3u, 1):
                custom_sources.append((path, f"custom_{idx}"))
        default_sources: List[Tuple[Optional[str], str]] = []
        if M3U_DIR:
            default_sources.extend([
                (os.path.join(M3U_DIR, "free_tv_master.m3u"), "free_tv_master"),
                (os.path.join(M3U_DIR, "iptv_master.m3u"), "iptv_master"),
                (os.path.join(M3U_DIR, "master.m3u"), "combined_master"),
            ])
        master_paths = _build_master_sources(*(custom_sources + default_sources))
        source_report_path = args.source_report

    os.makedirs(M3U_DIR, exist_ok=True)
    os.makedirs(LOG_DIR, exist_ok=True)

    favs, hdr = _read_csv(TV_FAV)
    master_lookup = _parse_master_playlist(master_paths)
    written, sk_notfav, sk_nourl, channel_meta, overrides = write_pruned_m3u_from_favs(
        favs, hdr, pruned_out, cc_map_path=cc_map, master_lookup=master_lookup,
        source_report_path=source_report_path)

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
