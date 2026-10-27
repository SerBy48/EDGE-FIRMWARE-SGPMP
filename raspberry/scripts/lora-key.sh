#!/usr/bin/env bash
# Entrega el secrets.ini con el que se flashea un nodo LoRa v2: su serial, su
# node_id y su clave, derivados del secreto del sitio (docs/PROTOCOLO_LORA.md
# §11). Lo instala install.sh en /opt/sgpmp-edge/bin/lora-key.
#
# Uso:
#   sudo /opt/sgpmp-edge/bin/lora-key <serial del nodo> > secrets.ini
#
# La salida contiene la clave del nodo: copiarla a esp32/secrets.ini en el PC
# que flashea y no guardarla en otro lado.
set -euo pipefail

ENV_FILE=/etc/sgpmp/edge-agent.env
PREFIX=${EDGE_UPDATE_PREFIX:-/opt/sgpmp-edge}

[[ $EUID -eq 0 ]] || { echo "lora-key: correr con sudo (lee $ENV_FILE)" >&2; exit 1; }

# systemd-run usa el mismo parser de EnvironmentFile que el servicio (no
# "source": un valor con $ o espacios se interpretaría distinto).
exec systemd-run --quiet --wait --pipe --collect \
  -p EnvironmentFile="$ENV_FILE" \
  "$PREFIX/venv/bin/python" -m edge_agent.lora.keys "$@"
