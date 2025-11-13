#!/usr/bin/env bash
set -Eeuo pipefail

# Force correct HOME and PATH even if root calls us
export HOME=/home/trevor
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin

# Load your env explicitly (no ~)
set -a
. /home/trevor/Kodi/.env
set +a

# Optional: self-logging
mkdir -p /home/trevor/Kodi/logs
exec >>/home/trevor/Kodi/logs/${0##*/}.cron.log 2>&1
echo "[$(date -Is)] START as $(whoami) HOME=$HOME"


# Load .env
ENV_FILE="${HOME}/Kodi/.env"
if [ -f "$ENV_FILE" ]; then
  # Ensure UNIX newlines (in case file was edited on Windows)
  sed -i 's/\r$//' "$ENV_FILE"

  # Load every VAR from .env (supports multi-line quoted strings)
  set -a
  # shellcheck disable=SC1090
  . "$ENV_FILE"
  set +a
else
  echo "ERROR: $ENV_FILE not found" >&2
  exit 1
fi

# Ensure dirs
mkdir -p "${BIN_DIR}" "${M3U_DIR}" "${EPG_DIR}" "${LOG_DIR}"

LOG_FILE="${LOG_DIR}/monthly_update_m3u.log"
echo "[$(date -Iseconds)] Start monthly_update_m3u" | tee -a "${LOG_FILE}"

download_sources() {
  local urls="$1" prefix="$2" label="$3"
  rm -f "${M3U_DIR}/${prefix}"_*.m3u
  local i=0
  echo "${urls}" | tr '\n' ' ' | tr -s ' ' | while read -r url; do
    if [ -n "${url}" ]; then
      i=$((i+1))
      local out="${M3U_DIR}/${prefix}_${i}.m3u"
      echo "Downloading ${label} ${url} -> ${out}" | tee -a "${LOG_FILE}"
      curl -fsSL "${url}" -o "${out}"
    fi
  done
}

build_master_from_prefix() {
  local prefix="$1" target="$2"
  echo "#EXTM3U" > "${target}"
  shopt -s nullglob
  for f in "${M3U_DIR}/${prefix}"_*.m3u; do
    awk 'NR==1 && $0 ~ /^#EXTM3U/ {next} {print}' "$f" >> "${target}"
  done
  shopt -u nullglob
  echo "Built ${target} from ${prefix}_*.m3u" | tee -a "${LOG_FILE}"
}

download_sources "${FREE_TV_M3U_URLS:-}" "free_source" "Free-TV"
download_sources "${IPTV_M3U_URLS:-}" "iptv_source" "IPTV-org"

FREE_MASTER="${FREE_TV_MASTER_M3U:-${M3U_DIR}/free_tv_master.m3u}"
IPTV_MASTER="${IPTV_MASTER_M3U:-${M3U_DIR}/iptv_master.m3u}"
build_master_from_prefix "free_source" "${FREE_MASTER}"
build_master_from_prefix "iptv_source" "${IPTV_MASTER}"

# Build a combined master (Free-TV entries first so they win when deduping later)
MASTER="${MASTER_M3U:-${M3U_DIR}/master.m3u}"
echo "#EXTM3U" > "${MASTER}"
for source in "${FREE_MASTER}" "${IPTV_MASTER}"; do
  [ -f "${source}" ] || continue
  awk 'NR==1 && $0 ~ /^#EXTM3U/ {next} {print}' "${source}" >> "${MASTER}"
done
echo "Built master playlist: ${MASTER}" | tee -a "${LOG_FILE}"

# Prune using favourites
echo "Pruning with ${BIN_DIR}/prune_m3u.py" | tee -a "${LOG_FILE}"
python3 "${BIN_DIR}/prune_m3u.py" --use-env | tee -a "${LOG_FILE}"

# Deploy to Samba-mounted path
if [ -n "${KODI_SMB_PATH:-}" ]; then
  if mountpoint -q "${KODI_SMB_PATH}"; then
    cp -f "${M3U_DIR}/${M3U}" "${KODI_SMB_PATH}/${M3U}"
    if [ -f "${M3U_DIR}/channel_cc_map.json" ]; then
      cp -f "${M3U_DIR}/channel_cc_map.json" "${KODI_SMB_PATH}/channel_cc_map.json"
    fi
    echo "Copied pruned M3U and channel map to ${KODI_SMB_PATH}" | tee -a "${LOG_FILE}"
  else
    echo "[warn] ${KODI_SMB_PATH} not mounted; skipping copy" | tee -a "${LOG_FILE}"
  fi
fi

echo "[$(date -Iseconds)] Done monthly_update_m3u" | tee -a "${LOG_FILE}"
