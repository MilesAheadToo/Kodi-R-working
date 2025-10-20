#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# Build EPG from pruned.m3u (Favourite-only) using exact + fuzzy matching.
# Uses ONLY ~/Kodi/.env (M3U_DIR, M3U, EPG_DIR, EPG, LOG_DIR, FUZZY) when --use-env.

import argparse, csv, difflib, gzip, io, os, re, sys, xml.etree.ElementTree as ET
from glob import glob
from typing import Dict, Iterable, List, Optional, Set, Tuple

ENV_PATH = os.path.expanduser("~/Kodi/.env")
ATTR_RE = re.compile(r'([a-zA-Z0-9_-]+)="([^"]*)"')

def parse_env_file(path: str) -> Dict[str, str]:
    env: Dict[str, str] = {}
    with io.open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip().strip("'").strip('"')
    return env

def _read_text(path: str) -> Iterable[str]:
    with io.open(path, "r", encoding="utf-8", errors="replace") as f:
        for line in f: yield line.rstrip("\n")

def _open_xml(path: str): return gzip.open(path, "rb") if path.endswith(".gz") else open(path, "rb")
def _write_xml(path: str): return gzip.open(path, "wb") if path.endswith(".gz") else open(path, "wb")

def norm(s: str) -> str:
    if s is None: return ""
    s = re.sub(r"[^a-z0-9]+", " ", s.strip().lower())
    return re.sub(r"\s+", " ", s).strip()

def parse_attrs(extinf: str) -> Dict[str,str]:
    return {m.group(1): m.group(2) for m in ATTR_RE.finditer(extinf)}

def read_m3u(path: str) -> Iterable[Dict[str, str]]:
    it = iter(_read_text(path))
    header = next(it, None)
    if header is None or not header.startswith("#EXTM3U"):
        raise ValueError(f"{path}: not a valid M3U (missing #EXTM3U)")
    pending_ext = None
    for line in it:
        if line.startswith("#EXTINF:"): pending_ext = line; continue
        if pending_ext is not None:
            name = pending_ext.split(",", 1)[1].strip() if "," in pending_ext else ""
            attrs = parse_attrs(pending_ext)
            yield {"name": name, "tvg_id": (attrs.get("tvg-id","") or "").strip(),
                   "ext": pending_ext, "url": line.strip()}
            pending_ext = None

def load_epg_channels(epg_dir: str, progress: bool=False) -> Tuple[Dict[str,Dict], Dict[str,Dict], List[str]]:
    id2rec: Dict[str,Dict] = {}; name2rec: Dict[str,Dict] = {}; keys: List[str] = []
    xml_paths = []
    for ext in ("*.xml","*.xml.gz"):
        xml_paths += glob(os.path.join(epg_dir, ext))
    if not xml_paths:
        raise FileNotFoundError(f"No XMLTV files found in {epg_dir}")
    for p in xml_paths:
        if progress: print(f"[epg] scanning {os.path.basename(p)} ...", file=sys.stderr)
        with _open_xml(p) as fh:
            try: tree = ET.parse(fh)
            except ET.ParseError as e:
                print(f"[WARN] Skipping malformed XML: {p} ({e})", file=sys.stderr); continue
        root = tree.getroot()
        for ch in root.findall("channel"):
            cid = (ch.get("id") or "").strip()
            if not cid: continue
            disp_el = ch.find("display-name")
            disp = (disp_el.text or "").strip() if disp_el is not None else cid
            if cid not in id2rec: id2rec[cid] = {"id": cid, "name": disp}
            key = norm(disp)
            if key and key not in name2rec:
                name2rec[key] = {"id": cid, "name": disp}
                keys.append(key)
    if progress: print(f"[epg] channels indexed: {len(id2rec)}", file=sys.stderr)
    return id2rec, name2rec, keys

def best_fuzzy(norm_name: str, keys: List[str]) -> Tuple[Optional[str], float]:
    best_key, best_score = None, 0.0
    for k in keys:
        s = difflib.SequenceMatcher(None, norm_name, k).ratio()
        if s > best_score:
            best_key, best_score = k, s
    return best_key, best_score

def collect_keep_ids(m3u_in: str, name2rec: Dict[str,Dict], name_keys: List[str],
                     fuzzy_thresh: float, report_csv: str=None, progress: bool=False) -> Set[str]:
    rows = []; keep_ids: Set[str] = set()
    for row in read_m3u(m3u_in):
        name, tvg = row["name"], row["tvg_id"]
        if tvg.isdigit():
            keep_ids.add(tvg); rows.append(["KEPT_ID", name, tvg, tvg, row["url"]]); continue
        nkey = norm(name); rec = name2rec.get(nkey)
        if rec:
            keep_ids.add(rec["id"]); rows.append(["KEPT_NAME", name, tvg, rec["id"], row["url"]]); continue
        best_key, score = best_fuzzy(nkey, name_keys) if nkey else (None, 0.0)
        if best_key and score >= fuzzy_thresh:
            cid = name2rec[best_key]["id"]
            keep_ids.add(cid); rows.append([f"KEPT_FUZZY_{score:.3f}", name, tvg, cid, row["url"]])
        else:
            rows.append(["UNMATCHED", name, tvg, "", row["url"]])

    if report_csv:
        with io.open(report_csv, "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["action","name","m3u_tvg_id","epg_channel_id","url"])
            w.writerows(rows)

    if progress:
        kept = sum(1 for r in rows if r[0].startswith("KEPT"))
        print(f"[match] matched={kept} total={len(rows)} fuzzy>={fuzzy_thresh}", file=sys.stderr)

    return keep_ids

def consolidate(epg_dir: str, out_xml: str, keep_ids: Set[str], progress: bool=False) -> None:
    tv = ET.Element("tv"); seen = set(); kept_programmes = 0
    xml_paths = []
    for ext in ("*.xml","*.xml.gz"):
        xml_paths += glob(os.path.join(epg_dir, ext))
    for p in xml_paths:
        if progress: print(f"[epg] consolidating from {os.path.basename(p)} ...", file=sys.stderr)
        with _open_xml(p) as fh:
            try: tree = ET.parse(fh)
            except ET.ParseError as e:
                print(f"[WARN] Skipping malformed XML: {p} ({e})", file=sys.stderr); continue
        root = tree.getroot()
        for ch in root.findall("channel"):
            cid = (ch.get("id") or "").strip()
            if cid in keep_ids and cid not in seen:
                tv.append(ch); seen.add(cid)
        for pg in root.findall("programme"):
            cid = (pg.get("channel") or "").strip()
            if cid in keep_ids:
                tv.append(pg); kept_programmes += 1
    if progress: print(f"[epg] channels kept: {len(seen)} | programmes kept: {kept_programmes}", file=sys.stderr)
    tree_out = ET.ElementTree(tv)
    with (_write_xml(out_xml)) as fh:
        tree_out.write(fh, encoding="utf-8", xml_declaration=True)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--use-env", action="store_true",
                    help="Use ONLY ~/Kodi/.env (M3U_DIR+M3U, EPG_DIR+EPG, LOG_DIR, FUZZY).")
    # legacy (ignored with --use-env)
    ap.add_argument("--m3u"); ap.add_argument("--epg-dir"); ap.add_argument("--out")
    ap.add_argument("--report"); ap.add_argument("--progress", action="store_true")
    args = ap.parse_args()

    if args.use_env:
        env = parse_env_file(ENV_PATH)
        M3U_DIR = env["M3U_DIR"]; M3U_NAME = env["M3U"]
        EPG_DIR = env["EPG_DIR"]; EPG_NAME = env["EPG"]
        LOG_DIR = env["LOG_DIR"]; FUZZY = float(env.get("FUZZY", "0.86"))
        m3u_in  = os.path.join(M3U_DIR, M3U_NAME)
        epg_out = os.path.join(EPG_DIR, EPG_NAME)
        report  = os.path.join(LOG_DIR, "epg_match_report.csv")
    else:
        M3U_DIR = os.path.dirname(args.m3u or "."); EPG_DIR = args.epg_dir
        FUZZY = 0.86
        m3u_in = args.m3u; epg_out = args.out; report = args.report

    _, name2rec, name_keys = load_epg_channels(EPG_DIR, progress=args.progress)
    keep_ids = collect_keep_ids(m3u_in, name2rec, name_keys, FUZZY, report_csv=report, progress=args.progress)
    consolidate(EPG_DIR, epg_out, keep_ids, progress=args.progress)
    if args.progress: print(f"[done] EPG written: {epg_out}", file=sys.stderr)

if __name__ == "__main__":
    main()

