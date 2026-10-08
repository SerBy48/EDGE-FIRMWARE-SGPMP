#!/usr/bin/env bash
# Actualización automática del edge_agent desde los releases del repo.
#
# La corre edge-updater.timer cada 3 horas (como root). Instala el tag vX.Y.Z
# más alto que esté en origin/main y sea posterior a lo instalado (con
# EDGE_UPDATE_REQUIRE_SIGNATURE=1, además firmado por una clave de
# /etc/sgpmp/allowed_signers). Si después de instalar el servicio no queda
# sano, vuelve a la versión anterior.
#
# Uso:
#   sudo /opt/sgpmp-edge/bin/update.sh [--check]
#
#   --check  solo dice qué release instalaría; no instala nada.
#
# Configuración: /etc/sgpmp/edge-updater.env (ver edge-updater.env.example).
# Logs: journalctl -u edge-updater
set -euo pipefail

SERVICE=edge-agent
PREFIX=${EDGE_UPDATE_PREFIX:-/opt/sgpmp-edge}
SRC=${EDGE_UPDATE_SRC:-$PREFIX/src}
STATE_DIR=${EDGE_UPDATE_STATE_DIR:-/var/lib/sgpmp-edge-updater}
SIGNERS=${EDGE_UPDATE_SIGNERS:-/etc/sgpmp/allowed_signers}
LOCK=${EDGE_UPDATE_LOCK:-/run/sgpmp-edge-install.lock}
REPO=${EDGE_UPDATE_REPO:-https://github.com/SerBy48/EDGE-FIRMWARE-SGPMP.git}
BRANCH=${EDGE_UPDATE_BRANCH:-main}
HEALTH_S=${EDGE_UPDATE_HEALTH_S:-180}
REQUIRE_SIGNATURE=${EDGE_UPDATE_REQUIRE_SIGNATURE:-0}
# Repo privado: clave de deploy de GitHub (solo lectura) y REPO por SSH.
DEPLOY_KEY=${EDGE_UPDATE_DEPLOY_KEY:-/etc/sgpmp/deploy_key}
# Intentos fallidos de un mismo tag antes de dejar de probarlo. No es 1 porque
# un corte de red en pleno pip install también cuenta como fallo.
MAX_FAILS=3

# A stderr: pick_release devuelve el tag por stdout.
log() { printf '[update] %s\n' "$*" >&2; }
die() { printf '[update] ERROR: %s\n' "$*" >&2; exit 1; }
usage() { sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

# El clon es de root; safe.directory evita el rechazo de git en pruebas.
g() { git -C "$SRC" -c safe.directory='*' "$@"; }

describe() { g describe --tags --always "$1" 2>/dev/null || echo "$1"; }

fails() {
  local n
  n=$(grep -cxF "$1" "$STATE_DIR/failed" 2>/dev/null) || true
  echo "${n:-0}"
}

signed() {
  [[ -s "$SIGNERS" ]] || return 1
  g -c gpg.ssh.allowedSignersFile="$SIGNERS" verify-tag "$1" >/dev/null 2>&1
}

# Primer tag (de mayor a menor versión) en origin/$BRANCH que venga después
# del commit instalado, esté firmado y no haya fallado MAX_FAILS veces.
pick_release() {
  local installed=$1 tag commit
  while read -r tag; do
    commit=$(g rev-parse "$tag^{commit}")
    # Igual o anterior a lo instalado: nunca se baja de versión.
    g merge-base --is-ancestor "$commit" "$installed" && continue
    if ! g merge-base --is-ancestor "$installed" "$commit"; then
      log "$tag no desciende de lo instalado ($(describe "$installed")), se omite"
      continue
    fi
    if (($(fails "$tag") >= MAX_FAILS)); then
      log "$tag falló $MAX_FAILS veces, se omite"
      continue
    fi
    if [[ $REQUIRE_SIGNATURE == 1 ]] && ! signed "$tag"; then
      log "$tag no tiene una firma de $SIGNERS, se omite"
      continue
    fi
    echo "$tag"
    return
  done < <(g tag -l 'v[0-9]*' --sort=-v:refname --merged "origin/$BRANCH")
}

# Sano = activo durante HEALTH_S sin que systemd lo reinicie. No se exige
# conexión MQTT: una caída de red no debe provocar un rollback.
healthy() {
  local restarts state t
  restarts=$(systemctl show -p NRestarts --value "$SERVICE")
  for ((t = 0; t < HEALTH_S; t += 10)); do
    sleep 10
    state=$(systemctl show -p ActiveState --value "$SERVICE")
    [[ $state == active ]] || { log "$SERVICE quedó en estado $state"; return 1; }
    if [[ $(systemctl show -p NRestarts --value "$SERVICE") != "$restarts" ]]; then
      log "$SERVICE se reinició durante la verificación"
      return 1
    fi
  done
}

install_commit() {
  g checkout --quiet --force --detach "$1" \
    && SGPMP_INSTALL_LOCK_HELD=1 "$SRC/raspberry/scripts/install.sh" \
    && healthy
}

main() {
  local check=0
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --check) check=1; shift ;;
      -h|--help) usage ;;
      *) usage 1 ;;
    esac
  done

  if [[ ${EDGE_UPDATE_ENABLED:-1} != 1 ]]; then
    log "Actualización automática deshabilitada (EDGE_UPDATE_ENABLED=${EDGE_UPDATE_ENABLED})"
    ((check)) || exit 0
  fi
  ((check)) || [[ $EUID -eq 0 ]] || die "correr con sudo"

  # Mismo lock que install.sh: no pisar una instalación manual en curso.
  exec 9>"$LOCK"
  flock -n 9 || die "hay otra instalación o actualización en curso"

  if [[ -f "$DEPLOY_KEY" ]]; then
    # accept-new: la huella de github.com se fija en la primera conexión.
    export GIT_SSH_COMMAND="ssh -i $DEPLOY_KEY -o IdentitiesOnly=yes \
-o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$STATE_DIR/known_hosts"
    mkdir -p "$STATE_DIR"
  fi
  if [[ ! -d "$SRC/.git" ]]; then
    log "Clonando $REPO en $SRC"
    git clone --quiet --no-checkout "$REPO" "$SRC"
  fi
  g remote set-url origin "$REPO"
  # --force: un tag movido en el remoto se refresca aquí; la firma decide.
  g fetch --quiet --prune --prune-tags --force --tags origin

  local installed release
  installed=$(cat "$PREFIX/COMMIT" 2>/dev/null || true)
  [[ -n "$installed" ]] || die "no hay $PREFIX/COMMIT: instalar una vez a mano con install.sh"
  g cat-file -e "$installed^{commit}" 2>/dev/null \
    || die "el commit instalado $installed no está en $REPO (¿instalado con cambios locales?)"

  release=$(pick_release "$installed")
  if [[ -z "$release" ]]; then
    log "Sin releases nuevos (instalado: $(describe "$installed"))"
    exit 0
  fi
  if ((check)); then
    log "Instalaría $release (instalado: $(describe "$installed"))"
    exit 0
  fi

  mkdir -p "$STATE_DIR"
  log "Instalando $release (antes: $(describe "$installed"))"
  if install_commit "$release"; then
    log "Actualizado a $release"
    exit 0
  fi

  echo "$release" >>"$STATE_DIR/failed"
  log "$release no quedó sano (intento $(fails "$release") de $MAX_FAILS); volviendo a $(describe "$installed")"
  install_commit "$installed" || die "el rollback tampoco quedó sano: revisar en sitio"
  die "rollback a $(describe "$installed") completo"
}

# Todo dentro de main: bash lee el archivo entero antes de ejecutar, porque
# install.sh reemplaza este mismo script durante la actualización.
main "$@"
