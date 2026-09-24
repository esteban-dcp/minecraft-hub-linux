#!/usr/bin/env bash
# Local install: symlink the launcher into ~/.local/bin and add a menu entry.
# No root required. Uninstall: scripts/install.sh --uninstall
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BIN="$HOME/.local/bin"
APPS="$HOME/.local/share/applications"
ICONS="$HOME/.local/share/icons/hicolor/256x256/apps"

if [[ "${1:-}" == "--uninstall" ]]; then
  rm -f "$BIN/minecraft-hub" "$APPS/minecraft-hub.desktop" \
        "$ICONS/minecraft-hub.png"
  echo "Uninstalled (data in ~/.local/share/minecraft-hub kept)."
  exit 0
fi

mkdir -p "$BIN" "$APPS" "$ICONS"
chmod +x "$SRC/minecraft-hub"
ln -sf "$SRC/minecraft-hub" "$BIN/minecraft-hub"

# .desktop with an absolute Exec so it works even if ~/.local/bin isn't in PATH.
# Rewrite only the program, so the Play action keeps its own argument.
sed "s|^Exec=minecraft-hub |Exec=$BIN/minecraft-hub |" \
    "$SRC/data/minecraft-hub.desktop" > "$APPS/minecraft-hub.desktop"

[[ -f "$SRC/data/icon.png" ]] && cp "$SRC/data/icon.png" \
    "$ICONS/minecraft-hub.png" || true
command -v update-desktop-database >/dev/null 2>&1 && \
    update-desktop-database "$APPS" >/dev/null 2>&1 || true

echo "Installed: $BIN/minecraft-hub"
case ":$PATH:" in
  *":$BIN:"*) ;;
  *) echo "Note: add ~/.local/bin to PATH:  echo 'export PATH=\$HOME/.local/bin:\$PATH' >> ~/.bashrc" ;;
esac
echo "Run:  minecraft-hub gui"
