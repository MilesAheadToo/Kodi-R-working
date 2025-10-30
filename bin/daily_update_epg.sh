#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${HOME}/Kodi/.env"
if [ -f "$ENV_FILE" ]; then
  sed -i 's/\r$//' "$ENV_FILE"
  set -a
  . "$ENV_FILE"
  set +a
else
  echo "ERROR: $ENV_FILE not found" >&2
  exit 1
fi

mkdir -p "${BIN_DIR}" "${M3U_DIR}" "${EPG_DIR}" "${LOG_DIR}"

LOG_FILE="${LOG_DIR}/daily_update_epg.log"
echo "[$(date -Iseconds)] Start daily_update_epg" | tee -a "${LOG_FILE}"

for url in ${COUNTRY_EPG_URLS}; do
  [ -n "$url" ] || continue
  fname="$(basename "$url")"
  out="${EPG_DIR}/${fname}"
  echo "Downloading ${url} -> ${out}" | tee -a "${LOG_FILE}"
  curl -fsSL "${url}" -o "${out}"
done

# Never process previous pruned output as input
if [ -n "${EPG:-}" ] && [ -f "${EPG_DIR}/${EPG}" ]; then
  rm -f "${EPG_DIR}/${EPG}"
fi

echo "Pruning EPG with ${BIN_DIR}/match_epg_m3u.py" | tee -a "${LOG_FILE}"
python3 "${BIN_DIR}/match_epg_m3u.py" --use-env --progress | tee -a "${LOG_FILE}"

if [ -n "${KODI_SMB_PATH:-}" ]; then
  mkdir -p "${KODI_SMB_PATH}"
  cp -f "${M3U_DIR}/${M3U_OUT}" "${KODI_SMB_PATH}/${M3U}"
  cp -f "${EPG_DIR}/${EPG_OUT}" "${KODI_SMB_PATH}/${EPG}"
  echo "Copied M3U+EPG to ${KODI_SMB_PATH}" | tee -a "${LOG_FILE}"
fi

echo "[$(date -Iseconds)] Done daily_update_epg" | tee -a "${LOG_FILE}"
