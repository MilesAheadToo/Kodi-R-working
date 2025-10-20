#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="$HOME/Kodi/.env"
# shellcheck disable=SC1090
source "$ENV_FILE"

# Required from .env
: "${BIN_DIR:?set in .env}"
: "${M3U_DIR:?set in .env}"
: "${EPG_DIR:?set in .env}"
: "${LOG_DIR:?set in .env}"
: "${KODI_SMB_PATH:?set in .env}"
: "${COUNTRY_EPG_URLS:?set in .env}"
: "${M3U:?set in .env}"           # input playlist filename
: "${EPG:?set in .env}"           # output epg filename (e.g., pruned.epg.xml.gz)

PRUNED_M3U="${M3U_DIR}/${M3U}"
PRUNED_EPG="${EPG_DIR}/${EPG}"
CC_MAP="${M3U_DIR}/channel_cc_map.json"
EPG_REPORT="${LOG_DIR}/epg_match_report.csv"

mkdir -p "$LOG_DIR" "$EPG_DIR"

fetch_epg_list () {
  echo "[info] Fetching XMLTV into $EPG_DIR"
  for url in $COUNTRY_EPG_URLS; do
    out="$EPG_DIR/$(basename "$url")"
    echo "  - $url -> $out"
    curl -L --fail --retry 3 --retry-delay 2 --connect-timeout 15 -sS "$url" -o "$out" || {
      echo "    [warn] download failed: $url"
      continue
    }
    [[ -s "$out" ]] || echo "    [warn] empty file: $out"
  done
}

{
  echo "[$(date -Is)] daily_update_epg.sh start"
  echo "[info] .env loaded from $ENV_FILE"
  echo "[info] EPG_DIR=$EPG_DIR  M3U_DIR=$M3U_DIR  KODI_SMB_PATH=$KODI_SMB_PATH"

  [[ -s "$PRUNED_M3U" ]] || { echo "[error] $PRUNED_M3U missing/empty. Run monthly_update_m3u.sh."; exit 2; }

  # 1) Pull fresh EPGs
  fetch_epg_list
  ls "$EPG_DIR"/*.xml* >/dev/null 2>&1 || { echo "[error] No XMLTV files present in $EPG_DIR"; exit 2; }

  # 2) Build matched-only EPG (prune_epg_from_country.py reads M3U_DIR/EPG_DIR/LOG_DIR/EPG/FUZZY from .env)
  python3 "${BIN_DIR}/prune_epg_from_country.py" --use-env --progress

  [[ -s "$PRUNED_EPG" ]] || { echo "[error] Failed to produce $PRUNED_EPG"; exit 3; }

  # 3) Publish
  cp -f "$PRUNED_M3U" "$KODI_SMB_PATH/"
  cp -f "$PRUNED_EPG" "$KODI_SMB_PATH/"
  if [[ -s "$CC_MAP" ]]; then cp -f "$CC_MAP" "$KODI_SMB_PATH/"; fi
  if [[ -s "$EPG_REPORT" ]]; then cp -f "$EPG_REPORT" "$KODI_SMB_PATH/"; fi

  echo "[ok] Deployed ${M3U} and ${EPG} to $KODI_SMB_PATH"
  echo "[$(date -Is)] daily_update_epg.sh done"
} | tee -a "${LOG_DIR}/daily_update_epg.log"

