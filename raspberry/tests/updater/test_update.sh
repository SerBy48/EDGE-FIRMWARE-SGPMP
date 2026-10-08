#!/usr/bin/env bash
# Prueba de scripts/update.sh contra un repo local con tags firmados, sin
# firmar, con una clave no autorizada y fuera de main. install.sh y systemctl
# son falsos: no toca el sistema.
#
# Uso (desde raspberry/):
#   bash tests/updater/test_update.sh        # elección de release (--check)
#   sudo -E bash tests/updater/test_update.sh  # además instalación y rollback
set -euo pipefail

UPDATE="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/scripts/update.sh"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

export GIT_AUTHOR_NAME=test GIT_AUTHOR_EMAIL=test@example.com
export GIT_COMMITTER_NAME=test GIT_COMMITTER_EMAIL=test@example.com
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_NOSYSTEM=1

ssh-keygen -q -t ed25519 -N '' -C trusted -f "$TMP/trusted"
ssh-keygen -q -t ed25519 -N '' -C other -f "$TMP/other"
printf 'release namespaces="git" %s\n' "$(cat "$TMP/trusted.pub")" >"$TMP/allowed_signers"

origin="$TMP/origin"
git init -q -b main "$origin"
o() { git -C "$origin" "$@"; }
commit() { o commit -q --allow-empty -m "$1"; o rev-parse HEAD; }
tag_signed() { o -c gpg.format=ssh -c user.signingkey="$2" tag -s "$1" -m "$1"; }

# install.sh falso: registra qué se instaló y falla si el commit trae FAIL.
mkdir -p "$origin/raspberry/scripts"
cat >"$origin/raspberry/scripts/install.sh" <<'EOF'
#!/usr/bin/env bash
src=$(cd "$(dirname "$0")/../.." && pwd)
git -C "$src" -c safe.directory='*' rev-parse HEAD >"$EDGE_UPDATE_PREFIX/COMMIT"
echo "$(git -C "$src" -c safe.directory='*' describe --tags)" >>"$EDGE_UPDATE_PREFIX/instalados"
[[ ! -f "$src/raspberry/FAIL" ]]
EOF
chmod +x "$origin/raspberry/scripts/install.sh"
o add -A
c1=$(commit uno); tag_signed v0.1.0 "$TMP/trusted"
c2=$(commit dos); tag_signed v0.1.1 "$TMP/trusted"
o checkout -q -b develop
c3=$(commit develop); tag_signed v0.9.0 "$TMP/trusted"   # fuera de main
o checkout -q main
c4=$(commit cuatro); o tag -a v0.1.2 -m v0.1.2           # sin firma
c5=$(commit cinco); tag_signed v0.1.3 "$TMP/other"         # clave no autorizada
: "$c4" "$c5"

export EDGE_UPDATE_PREFIX="$TMP/prefix" EDGE_UPDATE_SRC="$TMP/src"
export EDGE_UPDATE_STATE_DIR="$TMP/state" EDGE_UPDATE_LOCK="$TMP/lock"
export EDGE_UPDATE_SIGNERS="$TMP/allowed_signers" EDGE_UPDATE_REPO="$origin"
export EDGE_UPDATE_DEPLOY_KEY="$TMP/sin-deploy-key"
mkdir -p "$TMP/prefix" "$TMP/state"

failures=0
expect() {  # expect <commit instalado> <texto esperado en la salida>
  local out
  echo "$1" >"$TMP/prefix/COMMIT"
  out=$(bash "$UPDATE" --check 2>&1) || true
  if [[ $out == *"$2"* ]]; then
    echo "ok   - $3"
  else
    echo "FAIL - $3"; echo "       esperado: $2"; echo "$out" | sed 's/^/       /'
    failures=$((failures + 1))
  fi
}

# Por defecto (EDGE_UPDATE_REQUIRE_SIGNATURE=0) la firma no se mira.
expect "$c1" "Instalaría v0.1.3" "sin firma requerida: el tag más alto en main"
expect "$c5" "Sin releases nuevos" "no reinstala lo que ya está"
expect "$c3" "no desciende de lo instalado" "instalado desde develop: no baja de versión"

printf 'v0.1.3\nv0.1.3\nv0.1.3\n' >"$TMP/state/failed"
expect "$c1" "v0.1.3 falló 3 veces" "deja de intentar un tag que falló 3 veces"
expect "$c1" "Instalaría v0.1.2" "y pasa al siguiente"
rm "$TMP/state/failed"

EDGE_UPDATE_DEPLOY_KEY="$TMP/trusted" expect "$c1" "Instalaría v0.1.3" "con clave de deploy presente"
EDGE_UPDATE_ENABLED=0 expect "$c1" "deshabilitada" "respeta EDGE_UPDATE_ENABLED=0"
expect "0000000000000000000000000000000000000000" "no está en" "commit instalado desconocido"

export EDGE_UPDATE_REQUIRE_SIGNATURE=1
expect "$c1" "Instalaría v0.1.1" "con firma requerida: el tag firmado más alto"
expect "$c1" "v0.1.3 no tiene una firma" "con firma requerida: ignora una clave no autorizada"
expect "$c1" "v0.1.2 no tiene una firma" "con firma requerida: ignora un tag sin firma"
expect "$c2" "Sin releases nuevos" "con firma requerida: no reinstala lo que ya está"

: >"$TMP/allowed_signers"
expect "$c1" "Sin releases nuevos" "con firma requerida y sin claves no instala nada"

if [[ $EUID -eq 0 ]]; then
  printf 'release namespaces="git" %s\n' "$(cat "$TMP/trusted.pub")" >"$TMP/allowed_signers"
  mkdir -p "$TMP/bin"
  printf '#!/bin/sh\ncase "$*" in *NRestarts*) echo 0 ;; *) echo active ;; esac\n' >"$TMP/bin/systemctl"
  chmod +x "$TMP/bin/systemctl"
  export PATH="$TMP/bin:$PATH" EDGE_UPDATE_HEALTH_S=0

  touch "$origin/raspberry/FAIL"; o add -A
  c6=$(commit roto); tag_signed v0.2.0 "$TMP/trusted"
  echo "$c5" >"$TMP/prefix/COMMIT"
  if bash "$UPDATE" >/dev/null 2>&1; then
    echo "FAIL - un release que falla debe terminar en error"; failures=$((failures + 1))
  elif [[ $(cat "$TMP/prefix/COMMIT") == "$c5" && $(cat "$TMP/state/failed") == v0.2.0 ]]; then
    echo "ok   - release que falla: rollback a lo anterior y queda registrado"
  else
    echo "FAIL - rollback"; cat "$TMP/prefix/instalados"; failures=$((failures + 1))
  fi

  o rm -q raspberry/FAIL
  c7=$(commit arreglado); tag_signed v0.2.1 "$TMP/trusted"
  if bash "$UPDATE" >/dev/null 2>&1 && [[ $(cat "$TMP/prefix/COMMIT") == "$c7" ]]; then
    echo "ok   - el release siguiente se instala"
  else
    echo "FAIL - instalar v0.2.1"; cat "$TMP/prefix/instalados"; failures=$((failures + 1))
  fi
  : "$c6"
else
  echo "skip - instalación y rollback (correr con sudo -E)"
fi

[[ $failures -eq 0 ]] || { echo "$failures prueba(s) fallaron"; exit 1; }
echo "todas las pruebas pasaron"
