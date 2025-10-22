#!/usr/bin/env bash
set -euo pipefail

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

# Download all M3U sources listed in IPTV_M3U_URLS (space or newline separated)
i=0
echo "${IPTV_M3U_URLS}" | tr '\n' ' ' | tr -s ' ' | while read -r url; do
  if [ -n "${url}" ]; then
    i=$((i+1))
    out="${M3U_DIR}/source_${i}.m3u"
    echo "Downloading ${url} -> ${out}" | tee -a "${LOG_FILE}"
    curl -fsSL "${url}" -o "${out}"
  fi
done

# Build a master.m3u by concatenating sources (if MASTER_M3U provided in .env, use that path)
MASTER="${MASTER_M3U:-${M3U_DIR}/master.m3u}"
echo "#EXTM3U" > "${MASTER}"
for f in "${M3U_DIR}"/source_*.m3u; do
  [ -f "$f" ] || continue
  # skip header lines
  awk 'NR==1 && $0 ~ /^#EXTM3U/ {next} {print}' "$f" >> "${MASTER}"
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
