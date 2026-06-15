#!/usr/bin/env bash
# Simple setup script to create runtime directories and set safe permissions
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$ROOT_DIR/config" "$ROOT_DIR/cookies" "$ROOT_DIR/downloads" "$ROOT_DIR/logs" "$ROOT_DIR/db"

echo "Created runtime directories under $ROOT_DIR"

# If example config exists and real config missing, copy it
if [ -f "$ROOT_DIR/savextube.example.toml" ] && [ ! -f "$ROOT_DIR/config/savextube.toml" ]; then
  if cp "$ROOT_DIR/savextube.example.toml" "$ROOT_DIR/config/savextube.toml" 2>/dev/null; then
    echo "Copied example config to config/savextube.toml"
  else
    echo "Warning: failed to copy example config to config/savextube.toml (permission denied?). Please copy it manually."
  fi
fi

# Ensure config dir is not world readable
chmod 700 "$ROOT_DIR/config" || true

echo "Setup complete. Review config/savextube.toml and add cookies/ and session files as needed."

exit 0
