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
: "${IPTV_M3U_URLS:?set in .env}"
: "${TV_FAV:?set in .env}"
: "${M3U:?set in .env}"             # final TV playlist file name (e.g., pruned_tv.m3u)

PRUNED_M3U="${M3U_DIR}/${M3U}"
CC_MAP="${M3U_DIR}/channel_cc_map.json"
REPORT="${LOG_DIR}/prune_report.csv"

mkdir -p "$LOG_DIR" "$M3U_DIR" "$EPG_DIR"

fetch_m3u_list () {
  echo "[info] Fetching TV country M3Us into $M3U_DIR"
  for url in $IPTV_M3U_URLS; do
    out="$M3U_DIR/$(basename "$url")"
    echo "  - $url -> $out"
    curl -L --fail --retry 3 --retry-delay 2 --connect-timeout 15 -sS "$url" -o "$out" || {
      echo "    [warn] download failed: $url"
      continue
    }
    [[ -s "$out" ]] || echo "    [warn] empty file: $out"
  done
}

{
  echo "[$(date -Is)] monthly_update_m3u.sh start"
  echo "[info] .env loaded from $ENV_FILE"
  echo "[info] M3U_DIR=$M3U_DIR  EPG_DIR=$EPG_DIR  LOG_DIR=$LOG_DIR  KODI_SMB_PATH=$KODI_SMB_PATH"

  # 1) Download per-country TV M3Us (saved by basename in M3U_DIR)
  fetch_m3u_list

  # 2) Build favourites-only playlist using ONLY .env paths
  #    (prune_m3u.py will read TV_FAV/M3U_DIR/LOG_DIR/M3U from .env)
  python3 "${BIN_DIR}/prune_m3u.py" --use-env

  # 3) Sanity & deploy
  EXTINF_COUNT=$(grep -c '^#EXTINF' "${PRUNED_M3U}" || true)
  echo "[info] ${M3U} channel count: ${EXTINF_COUNT}"
  if [[ "${EXTINF_COUNT}" -eq 0 ]]; then
    echo "[error] ${PRUNED_M3U} has 0 channels. Aborting deploy."
    exit 3
  fi

  cp -f "${PRUNED_M3U}" "${KODI_SMB_PATH}/"
  if [[ -s "${CC_MAP}" ]]; then cp -f "${CC_MAP}" "${KODI_SMB_PATH}/"; fi
  if [[ -s "${REPORT}" ]]; then cp -f "${REPORT}" "${KODI_SMB_PATH}/"; fi

  echo "[ok] Deployed ${M3U} to ${KODI_SMB_PATH}"
  echo "[$(date -Is)] monthly_update_m3u.sh done"
} | tee -a "${LOG_DIR}/monthly_update_m3u.log"

