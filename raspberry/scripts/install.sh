#!/usr/bin/env bash
# Instalación / actualización del edge_agent en la Raspberry (hito M2).
#
# Se corre UNA vez al provisionar el equipo (con --env) y de nuevo solo para
# actualizar el código (sin --env: conserva la configuración existente).
# Es idempotente: correrlo dos veces deja el mismo resultado.
#
# Uso:
#   sudo ./raspberry/scripts/install.sh --env /ruta/edge-agent.env [--ca /ruta/ca.pem] [--no-start]
#
# La contraseña MQTT solo vive en /etc/sgpmp/edge-agent.env (0600, root).
# Borrar el archivo de origen después de instalar.
set -euo pipefail

SERVICE=edge-agent
SERVICE_USER=sgpmp-edge
PREFIX=/opt/sgpmp-edge
CONF_DIR=/etc/sgpmp
ENV_FILE="$CONF_DIR/edge-agent.env"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

env_src=""
ca_src=""
start=1

usage() { sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
log() { printf '[install] %s\n' "$*"; }
die() { printf '[install] ERROR: %s\n' "$*" >&2; exit 1; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env) env_src="${2:?falta la ruta de --env}"; shift 2 ;;
    --ca) ca_src="${2:?falta la ruta de --ca}"; shift 2 ;;
    --no-start) start=0; shift ;;
    -h|--help) usage ;;
    *) usage 1 ;;
  esac
done

[[ $EUID -eq 0 ]] || die "correr con sudo"
[[ -n "$env_src" || -f "$ENV_FILE" ]] || die "primera instalación: falta --env"

log "Paquetes del sistema"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-dev rsync >/dev/null

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
"$PREFIX/venv/bin/pip" install -q "$PREFIX/app"

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
install -m 0644 "$SRC_DIR/systemd/journald-sgpmp-edge.conf" /etc/systemd/journald.conf.d/sgpmp-edge.conf
systemctl restart systemd-journald
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null

if [[ $start -eq 1 ]]; then
  log "Reiniciando $SERVICE"
  systemctl restart "$SERVICE"
  sleep 3
  systemctl --no-pager --lines=5 status "$SERVICE" || true
fi

log "Listo. Logs: journalctl -u $SERVICE -f"
[[ -n "$env_src" ]] && log "Recordatorio: borrar $env_src (tiene la contraseña MQTT)"
exit 0
