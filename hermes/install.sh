#!/bin/sh
# Install the visor plugin for Hermes Agent.
# Copies hermes/plugin -> ~/.hermes/plugins/visor and enables it.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="${HOME}/.hermes/plugins/visor"

echo "installing visor plugin -> $DEST"
mkdir -p "$DEST"
cp "$HERE/plugin/__init__.py" "$DEST/__init__.py"
cp "$HERE/plugin/plugin.yaml" "$DEST/plugin.yaml"

if command -v hermes >/dev/null 2>&1; then
  hermes plugins enable visor || true
  echo "enabled (run 'hermes plugins list' to verify)"
else
  echo "hermes CLI not found — plugin files copied; enable with 'hermes plugins enable visor'"
fi
echo "done. VISOR_HOME=${VISOR_HOME:-$HOME/code/visor} (set if your visor lives elsewhere)."
