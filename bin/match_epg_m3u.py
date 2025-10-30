#!/usr/bin/env python3
import re, gzip, io, os, unicodedata
from xml.etree import ElementTree as ET
import pandas as pd
from collections import defaultdict

M3U_IN  = "pruned_tv.m3u"
EPGS    = ["epg_GB.xml.gz","epg_US.xml.gz","epg_CA.xml.gz","epg_DE.xml.gz"]
ALIASES = "epg_aliases.csv"    # optional manual overrides (see format below)

M3U_OUT = "pruned_tv_matched.m3u"
EPG_OUT = "merged_matched_epg.xml.gz"
REPORT  = "m3u_epg_match_report.csv"
ENV_PATH = os.environ.get("KODI_ENV_PATH", os.path.expanduser("~/Kodi/.env"))
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

def parse_env_file(path):
    env = {}
    if not os.path.isfile(path):
        return env
    with io.open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env

def resolve_dir(env, key, default):
    cand = env.get(key) or os.environ.get(key)
    if cand:
        return os.path.expanduser(cand)
    return default

def build_paths():
    env = parse_env_file(ENV_PATH)
    base_dir = os.path.abspath(os.path.join(SCRIPT_DIR, os.pardir))
    bin_dir = resolve_dir(env, "BIN_DIR", SCRIPT_DIR)
    m3u_dir = resolve_dir(env, "M3U_DIR", os.path.join(base_dir, "m3u"))
    epg_dir = resolve_dir(env, "EPG_DIR", os.path.join(base_dir, "epg"))
    log_dir = resolve_dir(env, "LOG_DIR", os.path.join(base_dir, "logs"))

    os.makedirs(m3u_dir, exist_ok=True)
    os.makedirs(epg_dir, exist_ok=True)
    os.makedirs(log_dir, exist_ok=True)

    m3u_name = env.get("M3U") or M3U_IN

    paths = {
        "m3u_in": os.path.join(m3u_dir, m3u_name),
        "alias": os.path.join(bin_dir, ALIASES),
        "epgs": [os.path.join(epg_dir, name) for name in EPGS],
        "m3u_out": os.path.join(m3u_dir, M3U_OUT),
        "epg_out": os.path.join(epg_dir, EPG_OUT),
        "report": os.path.join(log_dir, REPORT),
    }
    return paths

def strip_accents(s):
    return ''.join(c for c in unicodedata.normalize('NFKD', s) if not unicodedata.combining(c))

def norm_name(s):
    s = (s or "").lower().strip()
    s = strip_accents(s)
    s = s.replace('&',' and ').replace('+',' plus ')
    s = re.sub(r'(?<!\w)\+(?=\d)', ' plus ', s)       # "+1" -> " plus 1"
    s = re.sub(r'\b(uhd|fhd|hd|sd|4k|hdr|hevc|h\.265|h265|1080p|720p|2160p)\b',' ', s)
    s = re.sub(r'[\(\[][^)\]]*[\)\]]',' ', s)         # drop (region) tags
    s = re.sub(r'\b\+(\d+)\b', r' plus \1', s)
    s = re.sub(r'[^a-z0-9]+',' ', s)
    return re.sub(r'\s+',' ', s).strip()

def tokens(s): return set(norm_name(s).split()) if s else set()

def jaccard(a, b):
    if not a or not b: return 0.0
    inter = len(a & b); union = len(a | b)
    return inter/union if union else 0.0

def parse_m3u(path):
    out = []
    cur = None
    with open(path, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.rstrip("\n")
            if line.startswith("#EXTINF:"):
                attrs = dict(re.findall(r'(\w+?)="(.*?)"', line))
                name  = line.split(",",1)[1].strip() if "," in line else ""
                cur = {
                    "extinf": line,
                    "name": name,
                    "tvg_id": attrs.get("tvg-id", attrs.get("tvg_id", attrs.get("tvgid",""))).strip(),
                    "tvg_name": attrs.get("tvg-name", attrs.get("tvg_name","")),
                    "group": attrs.get("group-title", attrs.get("group_title",""))
                }
            elif line and not line.startswith("#") and cur is not None:
                cur["url"] = line.strip()
                out.append(cur); cur = None
    return out

def load_xml_channels(paths):
    chan_map = {}                       # id -> set(display-names)
    name_index = defaultdict(set)       # norm display-name -> {ids}
    id_suffix_index = defaultdict(set)  # ".uk/.us/.ca/.de" -> ids
    for p in paths:
        with gzip.open(p,'rb') as gz: data = gz.read()
        root = ET.fromstring(data)
        for ch in root.findall("./channel"):
            cid = ch.get("id","").strip()
            if not cid: continue
            dnames = [(dn.text or "").strip() for dn in ch.findall("./display-name") if (dn.text or "").strip()]
            if not dnames: dnames = [cid]
            chan_map[cid] = set(dnames)
            m = re.search(r'\.([a-z]{2})$', cid)
            if m: id_suffix_index[m.group(1)].add(cid)
            for dn in dnames:
                name_index[norm_name(dn)].add(cid)
    return chan_map, name_index, id_suffix_index

def guess_suffix(row):
    blob = f" {(row['tvg_id'] or '')} {(row['group'] or '')} {(row['tvg_name'] or row['name'] or '')} ".lower()
    if any(k in blob for k in [" uk "," gb "," united kingdom ",".uk",".gb"," british "]): return "uk"
    if any(k in blob for k in [" us "," usa "," united states ",".us"]): return "us"
    if any(k in blob for k in [" ca "," canada ",".ca"]): return "ca"
    if any(k in blob for k in [" de "," germany "," deutschland ",".de"]): return "de"
    m = re.search(r'\.([a-z]{2})$', (row['tvg_id'] or '').lower())
    return m.group(1) if m else None

def best_match(row, chan_map, name_index, id_suffix_index):
    tid = (row["tvg_id"] or "").strip()
    nm  = (row["tvg_name"] or row["name"] or "").strip()
    nkey = norm_name(nm)
    # (0) manual alias wins
    if row.get("_alias_target"): 
        return ("alias", row["_alias_target"], 1.0)
    # (1) exact id
    if tid in chan_map: return ("id_exact", tid, 1.00)
    # (2) compact id equal
    if tid:
        compact = re.sub(r'[^a-z0-9]+','',tid.lower())
        for cid in chan_map.keys():
            if re.sub(r'[^a-z0-9]+','',cid.lower()) == compact:
                return ("id_compact", cid, 0.97)
        if tid.endswith(".gb") and tid[:-3]+".uk" in chan_map: return ("id_gb_to_uk", tid[:-3]+".uk", 0.96)
        if tid.endswith(".uk") and tid[:-3]+".gb" in chan_map: return ("id_uk_to_gb", tid[:-3]+".gb", 0.96)
    # (3) direct name unique
    if nkey in name_index and len(name_index[nkey]) == 1:
        return ("name_unique", list(name_index[nkey])[0], 0.92)
    # (4) suffix-constrained Jaccard
    suf = row.get("_suffix") or guess_suffix(row)
    cands = set(chan_map.keys()) if not suf else set(id_suffix_index.get(suf, set()))
    name_tok = tokens(nm)
    best, score = None, 0.0
    for cid in cands:
        for dn in chan_map[cid]:
            sc = jaccard(name_tok, tokens(dn))
            if sc > score:
                score, best = sc, cid
    if score >= 0.60:
        conf = 0.85 if suf and best and best.endswith(f".{suf}") else 0.80
        conf += min(0.10, (score - 0.60))  # up to +0.1 boost
        return ("name_jaccard", best, round(conf,3))
    # (5) slug+country guess
    slug = re.sub(r'[^a-z0-9]+','', nkey)
    for s in ["uk","us","ca","de"]:
        guess = slug+"."+s
        if guess in chan_map: return ("slug_guess", guess, 0.72)
    return ("unmatched","",0.0)

def read_aliases(path):
    if not os.path.exists(path): return {}
    df = pd.read_csv(path)
    # columns: m3u_name,tvg_id_current,tvg_id_target,_suffix (optional)
    out = {}
    for r in df.to_dict("records"):
        key = (str(r.get("m3u_name","")).strip().lower(),
               str(r.get("tvg_id_current","")).strip().lower())
        out[key] = {
            "target": str(r.get("tvg_id_target","")).strip(),
            "suffix": str(r.get("_suffix","")).strip().lower() or None
        }
    return out

# Load inputs
paths = build_paths()
m3u = parse_m3u(paths["m3u_in"])
chan_map, name_index, id_suffix_index = load_xml_channels(paths["epgs"])
aliases = read_aliases(paths["alias"])

rows = []
for ch in m3u:
    key = ((ch["name"] or "").lower(), (ch["tvg_id"] or "").lower())
    alias = aliases.get(key, {})
    rows.append({
        "name": ch["name"],
        "tvg_name": ch["tvg_name"] or ch["name"],
        "tvg_id": ch["tvg_id"],
        "group": ch["group"],
        "url": ch["url"],
        "_alias_target": alias.get("target"),
        "_suffix": alias.get("suffix")
    })
df = pd.DataFrame(rows)

# Match
matches = df.apply(lambda r: best_match(r, chan_map, name_index, id_suffix_index), axis=1, result_type="expand")
df[["match_method","matched_id","confidence"]] = matches

# Write report
df_out = df[["name","tvg_id","tvg_name","group","matched_id","match_method","confidence"]].copy()
df_out.to_csv(paths["report"], index=False)

# Rewrite M3U with high-confidence matches
THRESH = 0.90
lines = ["#EXTM3U"]
for ch, r in zip(m3u, df.to_dict("records")):
    ext = ch["extinf"]
    new_id = r["matched_id"] if r["confidence"] >= THRESH and r["matched_id"] else ch["tvg_id"]
    if new_id:
        if 'tvg-id="' in ext:
            ext = re.sub(r'tvg-id="(.*?)"', f'tvg-id="{new_id}"', ext)
        else:
            ext = ext.replace('",', f'" tvg-id="{new_id}",')
    lines.append(ext)
    lines.append(ch["url"])
with open(paths["m3u_out"], "w", encoding="utf-8") as f: f.write("\n".join(lines) + "\n")

# Build merged EPG containing only matched channels
keep_ids = set(df.loc[df["confidence"]>=THRESH, "matched_id"].dropna().tolist())
root_out = ET.Element("tv")
seen = set()
for p in paths["epgs"]:
    with gzip.open(p,'rb') as gz: data = gz.read()
    r = ET.fromstring(data)
    for ch in r.findall("./channel"):
        cid = ch.get("id","")
        if cid in keep_ids and cid not in seen:
            root_out.append(ch); seen.add(cid)
    for pg in r.findall("./programme"):
        if pg.get("channel","") in keep_ids:
            root_out.append(pg)
buf = io.BytesIO()
ET.ElementTree(root_out).write(buf, encoding="utf-8", xml_declaration=True)
with gzip.open(paths["epg_out"],'wb') as gz: gz.write(buf.getvalue())

print("Wrote:", M3U_OUT, EPG_OUT, REPORT)
