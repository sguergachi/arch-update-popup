#!/bin/bash
# install.sh - Install arch-update-popup and enable the systemd timer

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "==> Checking dependencies..."
missing=()
for pkg in python-pyqt6 pacman-contrib; do
    pacman -Q "$pkg" &>/dev/null || missing+=("$pkg")
done

if (( ${#missing[@]} )); then
    echo "==> Installing missing packages: ${missing[*]}"
    sudo pacman -S --noconfirm "${missing[@]}"
fi

echo "==> Installing script..."
install -Dm755 "$SCRIPT_DIR/arch-update-popup" "$HOME/.local/bin/arch-update-popup"

echo "==> Installing systemd units..."
install -Dm644 "$SCRIPT_DIR/arch-update-popup.service" "$HOME/.config/systemd/user/arch-update-popup.service"
install -Dm644 "$SCRIPT_DIR/arch-update-popup.timer"   "$HOME/.config/systemd/user/arch-update-popup.timer"

echo "==> Enabling timer..."
systemctl --user daemon-reload
systemctl --user enable --now arch-update-popup.timer

echo ""
echo "Done! The popup will appear automatically 5 minutes after login"
echo "and once daily when updates are available."
echo ""
echo "To test immediately:  arch-update-popup"
