#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import argparse, sys, gzip, csv, re
from pathlib import Path
import xml.etree.ElementTree as ET

def load_env(path):
    env = {}
    p = Path(path).expanduser()
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line=line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k,v = line.split("=",1)
            env[k.strip()] = v.strip().strip('"').strip("'")
    return env

def parse_m3u_channels(m3u_path):
    chans = []
    tvgid_re = re.compile(r'tvg-id="(.*?)"')
    name_re = re.compile(r'#EXTINF:-1[^,]*,(.*)$')
    with open(m3u_path, "r", encoding="utf-8", errors="ignore") as f:
        tid, name = "", ""
        for line in f:
            if line.startswith("#EXTINF:"):
                m1 = tvgid_re.search(line)
                tid = m1.group(1).strip() if m1 else ""
                m2 = name_re.search(line)
                name = m2.group(1).strip() if m2 else ""
            elif line.startswith("http"):
                chans.append({"tvg-id": tid, "name": name})
                tid, name = "", ""
    return chans

def read_xmltv(path):
    if str(path).endswith(".gz"):
        with gzip.open(path, "rb") as f:
            data = f.read()
    else:
        data = Path(path).read_bytes()
    return ET.fromstring(data)

def write_xmltv_gz(path, root):
    data = ET.tostring(root, encoding="utf-8")
    with gzip.open(path, "wb") as f:
        f.write(data)

def main():
    ap = argparse.ArgumentParser(description="Prune XMLTV to channels present in pruned M3U")
    ap.add_argument("--use-env", action="store_true")
    ap.add_argument("--env", default="~/Kodi/.env")
    ap.add_argument("--progress", action="store_true")
    args = ap.parse_args()

    ENV = load_env(args.env) if args.use_env else {}
    M3U_DIR = Path(ENV.get("M3U_DIR","~/Kodi/m3u")).expanduser()
    EPG_DIR = Path(ENV.get("EPG_DIR","~/Kodi/epg")).expanduser()
    LOG_DIR = Path(ENV.get("LOG_DIR","~/Kodi/logs")).expanduser()
    OUT_NAME = ENV.get("EPG","pruned_epg.xml.gz")
    PRUNED_M3U = Path(M3U_DIR / ENV.get("M3U","pruned_tv.m3u")).expanduser()
    OUT_EPG = EPG_DIR / OUT_NAME

    LOG_DIR.mkdir(parents=True, exist_ok=True)
    report_csv = LOG_DIR / "epg_match_report.csv"

    # Input list strictly from COUNTRY_EPG_URLS basenames; exclude OUT_EPG
    input_files = []
    input_names = [Path(u).name for u in ENV.get("COUNTRY_EPG_URLS","").split() if u.strip()]
    for nm in input_names:
        p = EPG_DIR / nm
        if p.exists() and p.resolve() != OUT_EPG.resolve():
            input_files.append(p)

    # Fallback: epg_*.xml or .xml.gz, excluding OUT_EPG
    if not input_files:
        for p in EPG_DIR.iterdir():
            if p.resolve() == OUT_EPG.resolve():
                continue
            if p.name.startswith("epg_") and (p.suffix in (".xml",".gz") or p.name.endswith(".xml.gz")):
                input_files.append(p)

    if not input_files:
        print(f"No XMLTV country files found in {EPG_DIR}", file=sys.stderr)
        return 1

    if args.progress:
        print(f"Found {len(input_files)} XMLTV files")

    # Collect channels to keep
    m3u_channels = parse_m3u_channels(PRUNED_M3U)
    keep_ids = {c["tvg-id"] for c in m3u_channels if c["tvg-id"]}
    keep_names = {c["name"].lower() for c in m3u_channels}

    out_root = ET.Element("tv")
    kept_channels = 0
    kept_programmes = 0
    total_programmes = 0
    added_channels = set()

    # Copy channel defs
    for fp in input_files:
        root = read_xmltv(fp)
        for ch in root.findall("channel"):
            cid = ch.get("id","")
            names = [d.text.strip().lower() for d in ch.findall("display-name") if d.text]
            match = (cid in keep_ids) or any(n in keep_names for n in names)
            if match and cid not in added_channels:
                out_root.append(ch)
                added_channels.add(cid)
                kept_channels += 1

    # Copy programmes
    keep_ids_final = added_channels or keep_ids
    for fp in input_files:
        root = read_xmltv(fp)
        for pr in root.findall("programme"):
            total_programmes += 1
            if pr.get("channel","") in keep_ids_final:
                out_root.append(pr)
                kept_programmes += 1
        if args.progress:
            print(f"Processed {fp.name}: programmes so far {kept_programmes}/{total_programmes}")

    # Write output
    EPG_DIR.mkdir(parents=True, exist_ok=True)
    write_xmltv_gz(OUT_EPG, out_root)

    with report_csv.open("w", encoding="utf-8", newline="") as f:
        wr = csv.writer(f)
        wr.writerow(["inputs","kept_channels","kept_programmes","total_programmes","output_epg"])
        wr.writerow([len(input_files), kept_channels, kept_programmes, total_programmes, str(OUT_EPG)])

    print(f"Wrote {OUT_EPG} (channels: {kept_channels}, programmes: {kept_programmes}/{total_programmes})")
    return 0

if __name__ == "__main__":
    sys.exit(main())
