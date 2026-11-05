#!/bin/sh
# Install the visor plugin for Hermes Agent.
# Copies hermes/plugin/* -> ~/.hermes/plugins/visor (v1.8: the plugin ships
# its own server copy, so this is the complete payload) and enables it.
set -e
HERE="$(cd "$(dirname "$0")" && pwd)"
DEST="${HOME}/.hermes/plugins/visor"

echo "installing visor plugin -> $DEST"
# mirror (not overlay): stale files from older installs get removed
rm -rf "$DEST"
mkdir -p "$DEST"
cp -r "$HERE/plugin/." "$DEST/"

if command -v hermes >/dev/null 2>&1; then
  hermes plugins enable visor || true
  echo "enabled (run 'hermes plugins list' to verify)"
else
  echo "hermes CLI not found — plugin files copied; enable with 'hermes plugins enable visor'"
fi
echo "done. Without VISOR_HOME the plugin runs its bundled server (state in"
echo "~/.local/share/visor). To use this checkout instead:"
echo "  export VISOR_HOME=$HERE/.."
