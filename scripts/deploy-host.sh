#!/usr/bin/env bash
# Install or update the agent-bus daemon for the system unit. Run as root from the
# clone at the version to deploy:
#
#   sudo UV="$(command -v uv)" scripts/deploy-host.sh
#
# A copied install, not an editable one: the service runs as a dynamic uid that
# cannot read into anyone's home, so the package, its Python, and uv's cache all
# live under /opt/agent-bus, and the unit file is copied under /etc/systemd/system.
# Re-run on each release, then the unit restarts.
set -euo pipefail

PREFIX=/opt/agent-bus
SRC=$(cd "$(dirname "$0")/.." && pwd)

[ "$(id -u)" -eq 0 ] || { echo "run as root: sudo UV=\"\$(command -v uv)\" $0" >&2; exit 1; }
UV=${UV:-$(command -v uv || true)}
[ -n "$UV" ] || { echo "uv not found under sudo; pass it: sudo UV=\"\$(command -v uv)\" $0" >&2; exit 1; }

mkdir -p "$PREFIX"
UV_TOOL_DIR="$PREFIX/tools" UV_TOOL_BIN_DIR="$PREFIX/bin" UV_PYTHON_INSTALL_DIR="$PREFIX/python" UV_CACHE_DIR="$PREFIX/cache" \
    "$UV" tool install --force --reinstall "$SRC"
# The dynamic uid needs to read all of it; nothing here is secret.
chmod -R a+rX "$PREFIX"
"$PREFIX/bin/agent-bus-daemon" --help >/dev/null

# The unit is a root-owned copy, never a link into the checkout: a linked unit
# would let any agent that can edit the clone change what root runs.
install -o root -g root -m 0644 "$SRC/systemd/agent-bus.service" /etc/systemd/system/agent-bus.service
systemctl daemon-reload

if systemctl is-active --quiet agent-bus; then
    systemctl restart agent-bus
    echo "restarted agent-bus"
fi
echo "deployed $(git -C "$SRC" describe --always --tags 2>/dev/null || echo "$SRC") to $PREFIX"
