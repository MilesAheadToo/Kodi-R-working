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

def _read_csv(path: str) -> Tuple[List[Dict[str, str]], Dict[str, str], List[str]]:
    rows: List[Dict[str, str]] = []
    with io.open(path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        for r in reader:
            rows.append({k: (r.get(k) or "").strip() for k in headers})
    header_map = {h.strip().lower(): h for h in (reader.fieldnames or [])}
    return rows, header_map, headers

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
    origin_path: str = ""

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

def _classify_source(raw: Optional[str], origin_path: Optional[str]) -> str:
    tokens = []
    if raw:
        tokens.append(raw.lower())
    if origin_path:
        tokens.append(os.path.basename(origin_path).lower())
    for token in tokens:
        if "free_tv_master" in token or token == "free-tv":
            return "free_tv"
        if "iptv_master" in token or "iptv-org" in token:
            return "iptv_org"
    return "unknown"

def _source_category(raw: Optional[str], origin_path: Optional[str] = None) -> str:
    kind = _classify_source(raw, origin_path)
    if kind == "free_tv":
        return "Free-TV"
    if kind == "iptv_org":
        return "IPTV-org"
    return "Unknown"

def _m3u_source_label(raw: Optional[str], origin_path: Optional[str] = None) -> str:
    kind = _classify_source(raw, origin_path)
    if kind == "free_tv":
        return "Free-TV"
    if kind == "iptv_org":
        return "iptv-org"
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
                        attrs=attrs,
                        origin_path=path
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

def _resolve_first_existing_path(candidates: List[str]) -> Tuple[str, bool]:
    """Return (path, is_existing) choosing the first existing candidate or first non-empty."""
    expanded: List[str] = []
    for cand in candidates:
        if not cand:
            continue
        path = os.path.expanduser(cand)
        if path in expanded:
            continue
        expanded.append(path)
        if os.path.isfile(path):
            return path, True
    if expanded:
        return expanded[0], os.path.isfile(expanded[0])
    raise FileNotFoundError("No candidate paths provided for tv_favourites.csv")

def _normalize_mirror_list(raw: Optional[str]) -> List[str]:
    if not raw:
        return []
    parts = []
    for line in raw.splitlines():
        for segment in line.split(","):
            seg = segment.strip()
            if seg:
                parts.append(seg)
    return parts

def _collect_master_entries(lookup: Optional[Dict[str, Dict[str, MasterEntry]]]) -> List[MasterEntry]:
    entries: List[MasterEntry] = []
    if not lookup:
        return entries
    seen: set[str] = set()
    for bucket in lookup.values():
        for entry in bucket.values():
            key = _channel_key(entry.name, entry.tvg_id, entry.url).strip().lower()
            if not key:
                continue
            if key in seen:
                continue
            seen.add(key)
            entries.append(entry)
    return entries

def _write_favourites(tv_fav_path: str, favs: List[Dict[str, str]], headers: List[str],
                      mirror_paths: Optional[List[str]] = None) -> None:
    with io.open(tv_fav_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=headers)
        writer.writeheader()
        writer.writerows(favs)
    for mirror in mirror_paths or []:
        try:
            if mirror == tv_fav_path:
                continue
            os.makedirs(os.path.dirname(mirror), exist_ok=True)
            with io.open(mirror, "w", encoding="utf-8", newline="") as mf:
                writer = csv.DictWriter(mf, fieldnames=headers)
                writer.writeheader()
                writer.writerows(favs)
        except OSError as exc:
            print(f"[warn] Failed to mirror tv_favourites to {mirror}: {exc}")

def _ensure_header(hdr: Dict[str, str], headers: List[str], column: str) -> str:
    key = column.strip()
    low = key.lower()
    if low not in hdr:
        hdr[low] = key
        headers.append(key)
    return hdr[low]

def _set_field(row: Dict[str, str], hdr: Dict[str, str], headers: List[str], column: str, value: str) -> None:
    actual = _ensure_header(hdr, headers, column)
    row[actual] = value

def _apply_source_label(row: Dict[str, str], hdr: Dict[str, str], headers: List[str], label: str) -> None:
    if not label:
        return
    _set_field(row, hdr, headers, "m3u_source", label)
    _set_field(row, hdr, headers, "Source", label)

def _preferred_source_kinds(row: Dict[str, str], hdr: Dict[str, str]) -> List[str]:
    raw = (_get(row, hdr, "Source", "m3u_source") or "").strip().lower()
    if not raw:
        return []
    mapping = {
        "free-tv": "free_tv",
        "free_tv": "free_tv",
        "freetv": "free_tv",
        "iptv-org": "iptv_org",
        "iptv_org": "iptv_org",
        "iptvorg": "iptv_org",
    }
    kind = mapping.get(raw)
    return [kind] if kind else []

def _reset_new_flags(tv_fav_path: str,
                     favs: List[Dict[str, str]],
                     hdr: Dict[str, str],
                     headers: List[str],
                     mirror_paths: Optional[List[str]] = None) -> bool:
    col = _ensure_header(hdr, headers, "New")
    m3u_col = _ensure_header(hdr, headers, "m3u_source")
    source_col = _ensure_header(hdr, headers, "Source")
    changed = False
    for row in favs:
        val = (row.get(col) or "").strip()
        if val != "0":
            row[col] = "0"
            changed = True
        m3u_val = (row.get(m3u_col) or "").strip()
        source_val = (row.get(source_col) or "").strip()
        if source_val and not m3u_val:
            row[m3u_col] = source_val
            changed = True
        elif m3u_val and not source_val:
            row[source_col] = m3u_val
            changed = True
        elif source_val and m3u_val and source_val != m3u_val:
            row[m3u_col] = source_val
            changed = True
    if changed:
        _write_favourites(tv_fav_path, favs, headers, mirror_paths)
    return changed

def _sync_favourites_with_master(tv_fav_path: str,
                                 favs: List[Dict[str, str]],
                                 hdr: Dict[str, str],
                                 headers: List[str],
                                 master_entries: List[MasterEntry],
                                 mirror_paths: Optional[List[str]] = None,
                                 master_lookup: Optional[Dict[str, Dict[str, MasterEntry]]] = None) -> int:
    if not master_entries:
        return 0
    existing_keys = set()
    updated_existing = 0
    backfilled_countries = 0
    for row in favs:
        name = _get(row, hdr, "ChannelName", "Name", "Channel")
        tvg = _get(row, hdr, "TvgId", "tvg-id")
        url = _get(row, hdr, "Url", "URL", "StreamUrl")
        key = _channel_key(name, tvg, url).strip().lower()
        if key:
            existing_keys.add(key)
        group_name = _get(row, hdr, "GroupTitle", "Group", "Category")
        country = _get(row, hdr, "Country", "tvg-country")
        preferred_kinds = _preferred_source_kinds(row, hdr)
        source_hint = (_get(row, hdr, "m3u_source", "Source") or "").lower()
        country_source_hint = (_get(row, hdr, "CountrySource") or "").lower()
        is_row_free = "free-tv" in source_hint or "free-tv" in country_source_hint or ("free_tv" in preferred_kinds)
        master_entry = None
        master_kind = ""
        if master_lookup and (not country or not group_name or not is_row_free):
            master_entry = _find_master_entry(master_lookup, name, tvg, url, preferred_kinds=preferred_kinds)
            if master_entry:
                master_kind = _classify_source(master_entry.source, master_entry.origin_path)
        if not group_name and master_entry:
            group_name = (master_entry.attrs.get("group-title")
                          or master_entry.attrs.get("group")
                          or master_entry.attrs.get("category")
                          or "")
            if group_name:
                _set_field(row, hdr, headers, "GroupTitle", group_name)
                updated_existing += 1
        should_fill_country = (not country) and group_name and (is_row_free or master_kind == "free_tv")
        if should_fill_country:
            base_source = (_source_category(master_entry.source, master_entry.origin_path)
                           if master_entry else "Free-TV")
            if base_source:
                label = base_source if base_source.lower().startswith("free-tv") else "Free-TV"
            else:
                label = "Free-TV"
            if "group" not in label.lower():
                label = f"{label} (group)"
            _set_field(row, hdr, headers, "Country", group_name)
            _set_field(row, hdr, headers, "CountrySource", label)
            country = group_name
            updated_existing += 1
            backfilled_countries += 1

    today = datetime.utcnow().strftime("%Y%m%d")
    changed = 0
    mirrors = []
    for mirror in mirror_paths or []:
        expanded = os.path.expanduser(mirror)
        if expanded and expanded not in mirrors and expanded != tv_fav_path:
            mirrors.append(expanded)
    allowed_kinds = {"free_tv", "iptv_org"}
    for entry in master_entries:
        kind = _classify_source(entry.source, entry.origin_path)
        if kind not in allowed_kinds:
            continue
        key = _channel_key(entry.name, entry.tvg_id, entry.url).strip().lower()
        if not key or key in existing_keys:
            continue
        combined_url = "\n".join(entry.props + [entry.url]) if entry.props else entry.url
        group_name = entry.attrs.get("group-title") or entry.attrs.get("group") or entry.attrs.get("category") or ""
        row = {col: "" for col in headers}
        _set_field(row, hdr, headers, "ChannelName", entry.name or entry.attrs.get("tvg-name", ""))
        _set_field(row, hdr, headers, "TvgId", entry.tvg_id or entry.attrs.get("tvg-id", ""))
        _set_field(row, hdr, headers, "Url", combined_url)
        _set_field(row, hdr, headers, "GroupTitle", group_name)
        _set_field(row, hdr, headers, "Favourite", "0")
        _set_field(row, hdr, headers, "New", "1")
        _set_field(row, hdr, headers, "AddedOn", today)
        country = entry.attrs.get("tvg-country") or entry.attrs.get("country") or ""
        country_source_label = _source_category(entry.source, entry.origin_path)
        if not country and kind == "free_tv" and group_name:
            # Free-TV playlists group channels by country name, so fall back to that label.
            country = group_name
            country_source_label = f"{country_source_label} (group)"
        _set_field(row, hdr, headers, "Country", country)
        _set_field(row, hdr, headers, "CountrySource", country_source_label)
        _apply_source_label(row, hdr, headers, _m3u_source_label(entry.source, entry.origin_path))
        favs.append(row)
        existing_keys.add(key)
        changed += 1

    needs_write = changed or updated_existing
    if needs_write:
        _write_favourites(tv_fav_path, favs, headers, mirrors)
    if backfilled_countries:
        print(f"[info] Filled country via group-title for {backfilled_countries} Free-TV entries in {tv_fav_path}")
    return changed

def _find_master_entry(lookup: Optional[Dict[str, Dict[str, MasterEntry]]],
                       name: str, tvg_id: str, url: str,
                       preferred_kinds: Optional[Sequence[str]] = None) -> Optional[MasterEntry]:
    if not lookup:
        return None
    def _accept(entry: Optional[MasterEntry]) -> Optional[MasterEntry]:
        if not entry:
            return None
        if not preferred_kinds:
            return entry
        kind = _classify_source(entry.source, entry.origin_path)
        return entry if kind in preferred_kinds else None
    url_key = url.strip()
    if url_key and url_key in lookup["by_url"]:
        cand = _accept(lookup["by_url"][url_key])
        if cand:
            return cand
    tvg_key = _norm_key(tvg_id)
    if tvg_key and tvg_key in lookup["by_tvg"]:
        cand = _accept(lookup["by_tvg"][tvg_key])
        if cand:
            return cand
    if tvg_key and "@" in tvg_key:
        base = tvg_key.split("@", 1)[0]
        if base in lookup["by_tvg"]:
            cand = _accept(lookup["by_tvg"][base])
            if cand:
                return cand
    if tvg_key and "." in tvg_key:
        lower = tvg_key.lower()
        if lower in lookup["by_tvg"]:
            cand = _accept(lookup["by_tvg"][lower])
            if cand:
                return cand
    name_key = _norm_key(name)
    if name_key and name_key in lookup["by_name"]:
        cand = _accept(lookup["by_name"][name_key])
        if cand:
            return cand
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
            preferred_kinds = _preferred_source_kinds(r, hdr)
            master_entry = _find_master_entry(master_lookup, name, tvg_seed, stream_url,
                                              preferred_kinds=preferred_kinds)
            master_attrs = master_entry.attrs if master_entry else {}

            tvg  = tvg_seed or _attr_lookup(master_attrs, "tvg-id","tvg_id","tvgid")
            cc_raw = _get(r, hdr, "Country","tvg-country") or _attr_lookup(master_attrs, "tvg-country","country")
            cc = cc_raw.strip()
            if cc and len(cc) <= 3:
                cc = cc.upper()
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
            origin_path = master_entry.origin_path if master_entry else None
            if master_entry:
                source_label = _source_category(raw_source, origin_path)
            else:
                row_source = (_get(r, hdr, "Source", "m3u_source") or "Unknown").strip()
                source_label = row_source or "Unknown"

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
        default_share = f"/run/user/{os.getuid()}/gvfs/smb-share:server=rpiserver.local,share=kodi/tv_favourites.csv"
        tv_fav_candidates = [
            os.environ.get("KODI_TV_FAV_PATH"),
            default_share,
            env.get("TV_FAV"),
            os.environ.get("TV_FAV"),
            os.path.join(os.path.expanduser("~/Kodi"), "tv_favourites.csv"),
        ]
        TV_FAV, fav_exists = _resolve_first_existing_path(tv_fav_candidates)
        if not fav_exists:
            print(f"[info] tv_favourites.csv missing, will create at {TV_FAV}")
        mirror_paths = _normalize_mirror_list(env.get("TV_FAV_MIRRORS", ""))
        if not mirror_paths and os.path.exists(default_share):
            mirror_paths = [default_share]
        M3U_DIR = env["M3U_DIR"]; LOG_DIR = env["LOG_DIR"]; M3U_NAME = env["M3U"]
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
        default_share = f"/run/user/{os.getuid()}/gvfs/smb-share:server=rpiserver.local,share=kodi/tv_favourites.csv"
        tv_fav_candidates = [
            os.environ.get("KODI_TV_FAV_PATH"),
            default_share,
            args.fav,
            os.environ.get("TV_FAV"),
            os.path.join(os.path.expanduser("~/Kodi"), "tv_favourites.csv"),
        ]
        TV_FAV, fav_exists = _resolve_first_existing_path(tv_fav_candidates)
        if not fav_exists:
            print(f"[info] tv_favourites.csv missing, will create at {TV_FAV}")
        mirror_paths = _normalize_mirror_list(os.environ.get("TV_FAV_MIRRORS", ""))
        if not mirror_paths and os.path.exists(default_share):
            mirror_paths = [default_share]
        M3U_DIR = args.m3u_dir
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

    favs, hdr, fav_headers = _read_csv(TV_FAV)
    _reset_new_flags(TV_FAV, favs, hdr, fav_headers, mirror_paths=mirror_paths)
    master_lookup = _parse_master_playlist(master_paths)
    master_entries = _collect_master_entries(master_lookup)
    added = _sync_favourites_with_master(TV_FAV, favs, hdr, fav_headers, master_entries,
                                         mirror_paths=mirror_paths, master_lookup=master_lookup)
    if added:
        print(f"[info] Added {added} new channels to tv_favourites.csv at {TV_FAV}")
    else:
        print(f"[info] No new channels detected for {TV_FAV}")
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
