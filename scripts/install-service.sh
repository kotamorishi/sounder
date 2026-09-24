#!/bin/sh
# ログイン時に sounder を自動起動する LaunchAgent を入れる。
# 使い方: scripts/install-service.sh [ポート番号]
set -e

PORT="${1:-8777}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.local.sounder"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY="$(command -v python3)"

if [ -z "$PY" ]; then
  echo "python3 が見つかりません。Xcode Command Line Tools を入れてください:" >&2
  echo "  xcode-select --install" >&2
  exit 1
fi

mkdir -p "$HOME/Library/LaunchAgents" "$HERE/data"

cat > "$PLIST" <<PLIST_END
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$PY</string>
    <string>-m</string>
    <string>sounder</string>
    <string>--host</string>
    <string>127.0.0.1</string>
    <string>--port</string>
    <string>$PORT</string>
  </array>
  <key>WorkingDirectory</key><string>$HERE</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$HERE/data/service.log</string>
  <key>StandardErrorPath</key><string>$HERE/data/service.log</string>
  <key>EnvironmentVariables</key>
  <dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
</dict>
</plist>
PLIST_END

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
sleep 1
launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 \
  && echo "自動起動を設定しました → http://127.0.0.1:$PORT/" \
  || { echo "起動に失敗しました。$HERE/data/service.log を確認してください。" >&2; exit 1; }
echo "停止・解除は scripts/uninstall-service.sh"
