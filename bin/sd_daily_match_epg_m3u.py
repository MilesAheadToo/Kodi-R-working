
#!/usr/bin/env python3
"""
match_epg_m3u.py (Schedules Direct edition)
- Reads epg_sd.xml.gz (SchedulesDirect, XMLTV)
- Updates/sets tvg-id in pruned_tv.m3u so each channel's tvg-id equals the XMLTV <channel id> from SD
- Writes:
    - pruned_tv_sd_matched.m3u (rewritten playlist with corrected tvg-id)
    - epg_sd_matched.xml.gz     (EPG filtered to only matched channels)
    - sd_m3u_epg_report.csv     (per-channel mapping with confidence)
    - sd_m3u_epg_unmatched.csv  (channels we couldn't match; use aliases to fix next run)
"""
import re, gzip, io, os, unicodedata, csv
from xml.etree import ElementTree as ET
from collections import defaultdict

ENV_PATH = os.environ.get("KODI_ENV_PATH", os.path.expanduser("~/Kodi/.env"))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def parse_env_file(path):
    env = {}
    if not os.path.isfile(path):
        return env
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env

def resolve_dir(env, key, default):
    cand = env.get(key) or os.environ.get(key)
    return os.path.expanduser(cand) if cand else default

env = parse_env_file(ENV_PATH)
base_dir = os.path.abspath(os.path.join(SCRIPT_DIR, os.pardir))
BIN_DIR = resolve_dir(env, "BIN_DIR", SCRIPT_DIR)
M3U_DIR = resolve_dir(env, "M3U_DIR", os.path.join(base_dir, "m3u"))
EPG_DIR = resolve_dir(env, "EPG_DIR", os.path.join(base_dir, "epg"))
LOG_DIR = resolve_dir(env, "LOG_DIR", os.path.join(base_dir, "logs"))
os.makedirs(M3U_DIR, exist_ok=True)
os.makedirs(EPG_DIR, exist_ok=True)
os.makedirs(LOG_DIR, exist_ok=True)

M3U_IN  = os.path.join(M3U_DIR, env.get("M3U", "pruned_tv.m3u"))
EPG_IN  = os.path.join(EPG_DIR, "epg_sd.xml.gz")
M3U_OUT = os.path.join(M3U_DIR, "pruned_tv_sd_matched.m3u")
EPG_OUT = os.path.join(EPG_DIR, "epg_sd_matched.xml.gz")
REPORT  = os.path.join(LOG_DIR, "sd_m3u_epg_report.csv")
UNMATCH = os.path.join(LOG_DIR, "sd_m3u_epg_unmatched.csv")
ALIASES = os.path.join(BIN_DIR, "epg_aliases.csv")
MATCH_LOG = os.path.join(LOG_DIR, "sd_m3u_epg_match_trace.log")

def strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', s or "") if not unicodedata.combining(c))

def norm_name(s: str) -> str:
    import re, unicodedata
    s = (s or "").lower().strip()
    s = strip_accents(s)
    s = s.replace("&", " and ").replace("+", " plus ")
    s = re.sub(r'\b(uhd|fhd|hd|sd|4k|hdr|hevc|h\.265|h265|1080p|720p|2160p)\b', ' ', s)
    s = re.sub(r'[\(\[][^)\]]*[\)\]]', ' ', s)
    s = re.sub(r'\b\+(\d+)\b', r' plus \1', s)
    s = re.sub(r'[^a-z0-9]+', ' ', s)
    return re.sub(r'\s+', ' ', s).strip()

def parse_m3u(path):
    out, cur = [], None
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("#EXTINF:"):
                import re
                attrs = dict(re.findall(r'(\w+?)="(.*?)"', line))
                name  = line.split(",",1)[1].strip() if "," in line else ""
                cur = {
                    "extinf": line,
                    "name": name,
                    "tvg_id": (attrs.get("tvg-id") or attrs.get("tvg_id") or attrs.get("tvgid") or "").strip(),
                    "tvg_name": attrs.get("tvg-name") or attrs.get("tvg_name") or "",
                    "group": attrs.get("group-title") or attrs.get("group_title") or ""
                }
            elif line and not line.startswith("#") and cur is not None:
                cur["url"] = line.strip()
                out.append(cur); cur = None
    return out

def load_sd_xml_channels(path):
    with gzip.open(path, 'rb') as gz:
        data = gz.read()
    root = ET.fromstring(data)
    chan_map = {}                 # id -> set(display-names)
    name_index = defaultdict(set) # normalized display-name -> {ids}
    for ch in root.findall("./channel"):
        cid = (ch.get("id") or "").strip()
        if not cid:
            continue
        dnames = [(dn.text or "").strip() for dn in ch.findall("./display-name") if (dn.text or "").strip()]
        if not dnames:
            dnames = [cid]
        chan_map[cid] = set(dnames)
        for dn in dnames:
            name_index[norm_name(dn)].add(cid)
    return root, chan_map, name_index

def read_aliases(path):
    import csv
    aliases = {}
    if not os.path.exists(path):
        return aliases
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            key = ( (r.get("m3u_name") or "").strip().lower(),
                    (r.get("tvg_id_current") or "").strip().lower() )
            aliases[key] = {
                "target": (r.get("tvg_id_target") or "").strip()
            }
    return aliases

def best_match(row, chan_map, name_index):
    import re
    tid = (row["tvg_id"] or "").strip()
    nm  = (row["tvg_name"] or row["name"] or "").strip()
    nkey = norm_name(nm)

    if row.get("_alias_target"):
        return ("alias", row["_alias_target"], 1.0)
    if tid in chan_map:
        return ("id_exact", tid, 1.0)
    if tid:
        compact = re.sub(r'[^a-z0-9]+', '', tid.lower())
        for cid in chan_map.keys():
            if re.sub(r'[^a-z0-9]+','',cid.lower()) == compact:
                return ("id_compact", cid, 0.97)
    if nkey in name_index and len(name_index[nkey]) == 1:
        cid = next(iter(name_index[nkey]))
        return ("name_unique", cid, 0.92)
    # simple token overlap
    name_tok = set(nkey.split()) if nkey else set()
    best, best_score = None, 0.0
    for cid, names in chan_map.items():
        for dn in names:
            toks = set(norm_name(dn).split())
            if not toks or not name_tok: 
                continue
            score = len(toks & name_tok) / len(toks | name_tok)
            if score > best_score:
                best_score, best = score, cid
    if best and best_score >= 0.6:
        return ("name_jaccard", best, round(0.8 + min(0.1, best_score-0.6), 3))
    return ("unmatched", "", 0.0)

# run
if not os.path.exists(M3U_IN):
    raise SystemExit(f"M3U not found: {M3U_IN}")
if not os.path.exists(EPG_IN):
    raise SystemExit(f"EPG not found: {EPG_IN}")

m3u = parse_m3u(M3U_IN)
root, chan_map, name_index = load_sd_xml_channels(EPG_IN)
aliases = read_aliases(ALIASES)

rows = []
for ch in m3u:
    key = ((ch["name"] or "").lower(), (ch["tvg_id"] or "").lower())
    alias = aliases.get(key, {})
    row = {
        "name": ch["name"],
        "tvg_name": ch["tvg_name"] or ch["name"],
        "tvg_id": ch["tvg_id"],
        "group": ch["group"],
        "url": ch["url"],
        "_alias_target": alias.get("target")
    }
    method, cid, conf = best_match(row, chan_map, name_index)
    row.update({"match_method": method, "matched_id": cid, "confidence": conf})
    rows.append(row)

import pandas as pd
df = pd.DataFrame(rows)
df.to_csv(os.path.join(LOG_DIR, "sd_m3u_epg_report.csv"), index=False)
df[df["match_method"]=="unmatched"][["name","tvg_id","tvg_name","group"]].to_csv(os.path.join(LOG_DIR, "sd_m3u_epg_unmatched.csv"), index=False)

# rewrite M3U
THRESH = float(os.environ.get("SD_MATCH_THRESHOLD", "0.6"))
lines = ["#EXTM3U"]
matched_ids = set()
match_log_lines = []

for ch, r in zip(m3u, rows):
    new_id = r["matched_id"] if (r["confidence"] >= THRESH and r["matched_id"]) else ch["tvg_id"]
    ext = ch["extinf"]
    old_id = ch["tvg_id"] or ""
    matched_id = r["matched_id"] or ""
    line = (
        f"[MATCH] {ch['name']}: old_tvg_id='{old_id}' matched_tvg_id='{matched_id}' "
        f"method={r['match_method']} confidence={r['confidence']:.3f} applied_tvg_id='{new_id or ''}'"
    )
    print(line)
    match_log_lines.append(line)
    if new_id:
        if 'tvg-id="' in ext:
            ext = re.sub(r'tvg-id="(.*?)"', f'tvg-id="{new_id}"', ext)
        else:
            ext = ext.replace('",', f'" tvg-id="{new_id}",')
        matched_ids.add(new_id)
    lines.append(ext)
    lines.append(ch["url"])

with open(os.path.join(M3U_DIR, "pruned_tv_sd_matched.m3u"), "w", encoding="utf-8") as fh:
    fh.write("\n".join(lines) + "\n")

if match_log_lines:
    with open(MATCH_LOG, "w", encoding="utf-8") as fh:
        fh.write("\n".join(match_log_lines) + "\n")

# filter EPG to only matched channels
keep = matched_ids if matched_ids else set([r["matched_id"] for r in rows if r["matched_id"]])
new_root = ET.Element("tv")
for ch in root.findall("./channel"):
    if ch.get("id","") in keep:
        new_root.append(ch)
for pg in root.findall("./programme"):
    if pg.get("channel","") in keep:
        new_root.append(pg)

buf = io.BytesIO()
ET.ElementTree(new_root).write(buf, encoding="utf-8", xml_declaration=True)
with gzip.open(os.path.join(EPG_DIR, "epg_sd_matched.xml.gz"), "wb") as gz:
    gz.write(buf.getvalue())

print("OK")
