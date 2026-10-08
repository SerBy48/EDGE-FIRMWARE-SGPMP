#!/usr/bin/env bash
# Instalación / actualización del edge_agent en la Raspberry (hito M2).
#
# Se corre UNA vez al provisionar el equipo (con --env). Después las versiones
# nuevas llegan solas: edge-updater.timer lo vuelve a correr con cada release
# (scripts/update.sh). También se puede correr a mano, sin --env, para
# actualizar conservando la configuración. Es idempotente.
#
# Uso:
#   sudo ./raspberry/scripts/install.sh --env /ruta/edge-agent.env [--ca /ruta/ca.pem]
#        [--no-start] [--no-auto-update] [--deploy-key /ruta/deploy_key]
#
# --deploy-key: clave de deploy de GitHub (solo lectura) para seguir
# actualizando cuando el repo sea privado; cambia EDGE_UPDATE_REPO a SSH.
#
# La contraseña MQTT y la clave de deploy solo viven en /etc/sgpmp (0600,
# root). Borrar los archivos de origen después de instalar.
set -euo pipefail

SERVICE=edge-agent
SERVICE_USER=sgpmp-edge
PREFIX=/opt/sgpmp-edge
CONF_DIR=/etc/sgpmp
ENV_FILE="$CONF_DIR/edge-agent.env"
UPDATER_ENV="$CONF_DIR/edge-updater.env"
LOCK=/run/sgpmp-edge-install.lock
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PACKAGES=(python3 python3-venv python3-dev rsync git openssh-client)

env_src=""
ca_src=""
start=1
auto_update=1
deploy_key_src=""

usage() { sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
log() { printf '[install] %s\n' "$*"; }
die() { printf '[install] ERROR: %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env) env_src="${2:?falta la ruta de --env}"; shift 2 ;;
    --ca) ca_src="${2:?falta la ruta de --ca}"; shift 2 ;;
    --no-start) start=0; shift ;;
    --no-auto-update) auto_update=0; shift ;;
    --deploy-key) deploy_key_src="${2:?falta la ruta de --deploy-key}"; shift 2 ;;
    -h|--help) usage ;;
    *) usage 1 ;;
  esac
done

[[ $EUID -eq 0 ]] || die "correr con sudo"
[[ -n "$env_src" || -f "$ENV_FILE" ]] || die "primera instalación: falta --env"

# update.sh ya tiene el lock cuando nos llama.
if [[ -z "${SGPMP_INSTALL_LOCK_HELD:-}" ]]; then
  exec 9>"$LOCK"
  flock -n 9 || die "hay otra instalación o actualización en curso"
fi

log "Paquetes del sistema"
missing=()
for pkg in "${PACKAGES[@]}"; do
  dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null | grep -q 'install ok installed' \
    || missing+=("$pkg")
done
if [[ ${#missing[@]} -gt 0 ]]; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq "${missing[@]}" >/dev/null
fi

log "Reloj: NTP habilitado (timestamp_captura lo pone la Raspberry)"
timedatectl set-ntp true || log "no se pudo habilitar NTP, revisar a mano"

if command -v raspi-config >/dev/null; then
  log "SPI habilitado (SX1276, Fase 2)"
  raspi-config nonint do_spi 0 || log "no se pudo habilitar SPI"
fi

log "Usuario de servicio $SERVICE_USER"
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  useradd --system --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin "$SERVICE_USER"
fi
for group in spi gpio; do
  getent group "$group" >/dev/null && usermod -aG "$group" "$SERVICE_USER"
done

log "Código en $PREFIX/app"
install -d -m 0755 "$PREFIX"
rsync -a --delete \
  --exclude '.venv/' --exclude '__pycache__/' --exclude '.pytest_cache/' \
  --exclude '.ruff_cache/' --exclude 'tests/' --exclude '*.env' \
  "$SRC_DIR/" "$PREFIX/app/"

log "Entorno virtual en $PREFIX/venv"
[[ -x "$PREFIX/venv/bin/python" ]] || python3 -m venv "$PREFIX/venv"
"$PREFIX/venv/bin/pip" install -q --upgrade pip
"$PREFIX/venv/bin/pip" install -q "$PREFIX/app[lora]"

# COMMIT: desde dónde sigue update.sh. VERSION: version_firmware del heartbeat.
if git -C "$SRC_DIR" -c safe.directory='*' rev-parse HEAD >"$PREFIX/COMMIT.tmp" 2>/dev/null; then
  mv "$PREFIX/COMMIT.tmp" "$PREFIX/COMMIT"
  git -C "$SRC_DIR" -c safe.directory='*' describe --tags --always --dirty >"$PREFIX/VERSION"
  log "Versión $(cat "$PREFIX/VERSION")"
else
  rm -f "$PREFIX/COMMIT.tmp" "$PREFIX/COMMIT" "$PREFIX/VERSION"
  log "$SRC_DIR no es un clon de git: sin actualización automática"
fi

log "Configuración en $CONF_DIR"
install -d -m 0755 "$CONF_DIR"
if [[ -n "$env_src" ]]; then
  # systemd lee EnvironmentFile como root: 0600 alcanza y nadie más la lee.
  install -m 0600 -o root -g root "$env_src" "$ENV_FILE"
fi
if [[ -n "$ca_src" ]]; then
  install -m 0644 -o root -g root "$ca_src" "$CONF_DIR/ca.pem"
fi

log "Validando configuración"
# systemd-run usa el mismo parser de EnvironmentFile que el servicio (no
# "source": una contraseña con $ o espacios se interpretaría distinto).
systemd-run --quiet --wait --pipe --collect \
  -p EnvironmentFile="$ENV_FILE" \
  "$PREFIX/venv/bin/python" -m edge_agent --check-config \
  || die "configuración inválida en $ENV_FILE"

log "systemd y journald"
install -m 0644 "$SRC_DIR/systemd/edge-agent.service" "/etc/systemd/system/$SERVICE.service"
install -d -m 0755 /etc/systemd/journald.conf.d
journald_conf=/etc/systemd/journald.conf.d/sgpmp-edge.conf
if ! cmp -s "$SRC_DIR/systemd/journald-sgpmp-edge.conf" "$journald_conf"; then
  install -m 0644 "$SRC_DIR/systemd/journald-sgpmp-edge.conf" "$journald_conf"
  systemctl restart systemd-journald
fi

log "Actualización automática (edge-updater.timer)"
install -d -m 0755 "$PREFIX/bin"
install -m 0755 "$SRC_DIR/scripts/update.sh" "$PREFIX/bin/update.sh"
install -m 0644 "$SRC_DIR/release/allowed_signers" "$CONF_DIR/allowed_signers"
install -m 0644 "$SRC_DIR/systemd/edge-updater.service" /etc/systemd/system/edge-updater.service
install -m 0644 "$SRC_DIR/systemd/edge-updater.timer" /etc/systemd/system/edge-updater.timer
if [[ ! -f "$UPDATER_ENV" ]]; then
  install -m 0644 "$SRC_DIR/edge-updater.env.example" "$UPDATER_ENV"
fi
if [[ $auto_update -eq 0 ]]; then
  sed -i 's/^EDGE_UPDATE_ENABLED=.*/EDGE_UPDATE_ENABLED=0/' "$UPDATER_ENV"
  log "Deshabilitada en $UPDATER_ENV (--no-auto-update)"
fi
if [[ -n "$deploy_key_src" ]]; then
  install -m 0600 -o root -g root "$deploy_key_src" "$CONF_DIR/deploy_key"
  sed -i 's#^EDGE_UPDATE_REPO=https://github.com/#EDGE_UPDATE_REPO=git@github.com:#' "$UPDATER_ENV"
  log "Clave de deploy en $CONF_DIR/deploy_key; repo por SSH en $UPDATER_ENV"
fi

systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null
systemctl enable --now edge-updater.timer >/dev/null

if [[ $start -eq 1 ]]; then
  log "Reiniciando $SERVICE"
  systemctl restart "$SERVICE"
  sleep 3
  systemctl --no-pager --lines=5 status "$SERVICE" || true
fi

log "Listo. Logs: journalctl -u $SERVICE -f"
[[ -n "$env_src" ]] && log "Recordatorio: borrar $env_src (tiene la contraseña MQTT)"
[[ -n "$deploy_key_src" ]] && log "Recordatorio: borrar $deploy_key_src (clave de deploy)"
exit 0
