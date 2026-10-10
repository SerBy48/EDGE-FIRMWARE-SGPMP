#!/usr/bin/env bash
# Escribe la versión del edge_agent en pyproject.toml y __init__.py. Lo llama
# semantic-release (release.config.cjs) al publicar un release en main, para
# que el código diga la misma versión que el tag.
#
# Uso: raspberry/scripts/set_version.sh 0.3.0
set -euo pipefail

version=${1:-}
[[ $version =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]] || {
  echo "Uso: $0 X.Y.Z (versión final, sin -rc)" >&2
  exit 1
}

root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
sed -i -E "s/^version = \"[^\"]*\"/version = \"$version\"/" "$root/pyproject.toml"
sed -i -E "s/^__version__ = \"[^\"]*\"/__version__ = \"$version\"/" "$root/edge_agent/__init__.py"

grep -qx "version = \"$version\"" "$root/pyproject.toml"
grep -qx "__version__ = \"$version\"" "$root/edge_agent/__init__.py"
echo "edge_agent $version"
