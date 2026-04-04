#!/bin/bash
# install.sh - Install arch-update-popup and enable the systemd timer

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

echo "==> Installing script..."
install -Dm755 "$SCRIPT_DIR/arch-update-popup" "$HOME/.local/bin/arch-update-popup"

echo "==> Installing systemd units..."
install -Dm644 "$SCRIPT_DIR/arch-update-popup.service" "$HOME/.config/systemd/user/arch-update-popup.service"
install -Dm644 "$SCRIPT_DIR/arch-update-popup.timer"   "$HOME/.config/systemd/user/arch-update-popup.timer"

echo "==> Enabling service..."
systemctl --user daemon-reload
systemctl --user enable --now arch-update-popup.timer

echo ""
echo "Done! Arch Update will start automatically 5 minutes after login"
echo "and run in the system tray, checking for updates every 30 minutes."
echo ""
echo "To start immediately:  arch-update-popup"
echo "To restart the service: systemctl --user restart arch-update-popup"
