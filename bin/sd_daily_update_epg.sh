
#!/usr/bin/env bash
# daily_update_epg.sh — SchedulesDirect flow
# Refresh SchedulesDirect XMLTV and align M3U tvg-id to SD channel ids.

set -euo pipefail
# Force a sane, fully-generated locale for Perl/Python tools
export LANG=en_GB.UTF-8
export LC_ALL=en_GB.UTF-8
export LANGUAGE=en_GB:en
# If you want “C” semantics in scripts (safe everywhere), use:
# export LANG=C.UTF-8; export LC_ALL=C.UTF-8; export LANGUAGE=C


ENV_FILE="${KODI_ENV_PATH:-$HOME/Kodi/.env}"
[ -f "$ENV_FILE" ] && source "$ENV_FILE"

M3U_DIR="${M3U_DIR:-$HOME/Kodi/m3u}"
EPG_DIR="${EPG_DIR:-$HOME/Kodi/epg}"
LOG_DIR="${LOG_DIR:-$HOME/Kodi/logs}"
BIN_DIR="${BIN_DIR:-$(dirname "$0")}"
M3U="${M3U:-pruned_tv.m3u}"
EPG="${EPG:-pruned.epg.xml.gz}"
EPG_DAYS="${EPG_DAYS:-7}"

mkdir -p "$M3U_DIR" "$EPG_DIR" "$LOG_DIR"

echo "[SD] Generating XMLTV for $EPG_DAYS days -> $EPG_DIR/epg_sd.xml"
tv_grab_zz_sdjson --days "$EPG_DAYS" --output "$EPG_DIR/epg_sd.xml"
gzip -f "$EPG_DIR/epg_sd.xml"

echo "[SD] Matching M3U -> SD channel ids"
python3 "$BIN_DIR/sd_daily_match_epg_m3u.py"

# Optional: point your active files to the matched versions
# ln -sf "$M3U_DIR/pruned_tv_sd_matched.m3u" "$M3U_DIR/$M3U"
# ln -sf "$EPG_DIR/epg_sd_matched.xml.gz"   "$EPG_DIR/epg_sd.xml.gz"

echo "[SD] Done. Files:"
echo " - $M3U_DIR/pruned_tv_sd_matched.m3u"
echo " - $EPG_DIR/epg_sd_matched.xml.gz"
echo " - $LOG_DIR/sd_m3u_epg_report.csv"
echo " - $LOG_DIR/sd_m3u_epg_unmatched.csv"

if [ -n "${KODI_SMB_PATH:-}" ]; then
  mkdir -p "${KODI_SMB_PATH}"
  cp -f "${M3U_DIR}/pruned_tv_sd_matched.m3u" "${KODI_SMB_PATH}/${M3U}"
  cp -f "${EPG_DIR}/${EPG_OUT}" "${KODI_SMB_PATH}/${EPG}"
  echo "Copied M3U+EPG to ${KODI_SMB_PATH}" | tee -a "${LOG_FILE}"
fi

echo "[$(date -Iseconds)] Done daily_update_epg" | tee -a "${LOG_FILE}"
