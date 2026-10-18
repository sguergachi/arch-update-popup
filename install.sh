#!/bin/bash
# install.sh - Install Upkeep and enable the systemd timer

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Checking dependencies..."
missing=()
for pkg in python-pyqt6 pacman-contrib; do
    pacman -Q "$pkg" &>/dev/null || missing+=("$pkg")
done

# Recommend an AUR helper for AUR update support (optional)
if ! command -v paru &>/dev/null && ! command -v yay &>/dev/null; then
    echo "    Note: install paru or yay for AUR update support (optional)"
fi

if (( ${#missing[@]} )); then
    echo "==> Installing missing packages: ${missing[*]}"
    sudo pacman -S --noconfirm "${missing[@]}"
fi

# The app was called arch-update-popup before the rename. Stop its units and
# remove its files; its cache and settings stay put for the app to adopt
# (see _migrate_old_state), so the window comes back the size you left it.
echo "==> Retiring the old name (arch-update-popup)..."
systemctl --user disable --now arch-update-popup.timer &>/dev/null || true
systemctl --user stop arch-update-popup.service &>/dev/null || true
rm -f "$HOME/.config/systemd/user/arch-update-popup.timer" \
      "$HOME/.config/systemd/user/arch-update-popup.service" \
      "$HOME/.local/bin/arch-update-popup"

echo "==> Installing script..."
install -Dm755 "$SCRIPT_DIR/upkeep" "$HOME/.local/bin/upkeep"

echo "==> Installing systemd units..."
install -Dm644 "$SCRIPT_DIR/upkeep.service" "$HOME/.config/systemd/user/upkeep.service"
install -Dm644 "$SCRIPT_DIR/upkeep.timer"   "$HOME/.config/systemd/user/upkeep.timer"

echo "==> Enabling service..."
systemctl --user daemon-reload
systemctl --user enable --now upkeep.timer

echo ""
echo "Done! Upkeep will start automatically 5 minutes after login"
echo "and run in the system tray, checking for updates every 30 minutes."
echo ""
echo "To start immediately:  upkeep"
echo "To restart the service: systemctl --user restart upkeep"
