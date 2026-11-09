#!/bin/bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
UV_PATH="$(command -v uv)"
LOG_DIRECTORY="$ROOT_DIR/data/logs"
LAUNCH_AGENTS="$HOME/Library/LaunchAgents"
BOT_HOST="${DSQQ_BOT_HOST:-127.0.0.1}"
BOT_PORT="${DSQQ_BOT_PORT:-3001}"
ONEBOT_TOKEN="${DSQQ_ONEBOT_ACCESS_TOKEN:-}"

mkdir -p "$LOG_DIRECTORY" "$LAUNCH_AGENTS"

export ROOT_DIR UV_PATH LOG_DIRECTORY LAUNCH_AGENTS BOT_HOST BOT_PORT ONEBOT_TOKEN
python3 - <<'PY'
import os
from pathlib import Path
from xml.sax.saxutils import escape

root = Path(os.environ['ROOT_DIR'])
values = {
    '__WORKING_DIRECTORY__': escape(str(root)),
    '__HOME__': escape(str(Path.home())),
    '__UV_PATH__': escape(os.environ['UV_PATH']),
    '__BOT_HOST__': escape(os.environ['BOT_HOST']),
    '__BOT_PORT__': escape(os.environ['BOT_PORT']),
    '__LOG_DIRECTORY__': escape(os.environ['LOG_DIRECTORY']),
}
token = os.environ['ONEBOT_TOKEN']
values['__ONEBOT_ENVIRONMENT__'] = (
    '<key>ONEBOT_ACCESS_TOKEN</key><string>' + escape(token) + '</string>'
    if token else ''
)
for name in ('com.dsqq.bot', 'com.dsqq.dashboard'):
    content = (root / 'deploy' / 'launchd' / f'{name}.plist').read_text()
    for key, value in values.items():
        content = content.replace(key, value)
    destination = Path(os.environ['LAUNCH_AGENTS']) / f'{name}.plist'
    destination.write_text(content)
PY

plutil -lint "$LAUNCH_AGENTS/com.dsqq.bot.plist" "$LAUNCH_AGENTS/com.dsqq.dashboard.plist"
for service in com.dsqq.bot com.dsqq.dashboard; do
  launchctl bootout "gui/$UID" "$LAUNCH_AGENTS/$service.plist" 2>/dev/null || true
  launchctl bootstrap "gui/$UID" "$LAUNCH_AGENTS/$service.plist"
  launchctl enable "gui/$UID/$service"
  launchctl kickstart -k "gui/$UID/$service"
done

brew services start colima >/dev/null
if docker ps -a --format '{{.Names}}' | grep -qx dsqq-napcat; then
  docker update --restart unless-stopped dsqq-napcat >/dev/null
fi

echo "Installed com.dsqq.bot and com.dsqq.dashboard"
echo "Logs: $LOG_DIRECTORY"
