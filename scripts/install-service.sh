#!/bin/sh
# ログイン時に sounder を自動起動する LaunchAgent を入れる。
#
#   scripts/install-service.sh [ポート] [待ち受けアドレス] [合言葉]
#
#   例) scripts/install-service.sh                       この Mac の中だけ
#       scripts/install-service.sh 8777 0.0.0.0 himitsu  同じ LAN のスマホからも
set -e

PORT="${1:-8777}"
HOST="${2:-127.0.0.1}"
TOKEN="${3:-}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.local.sounder"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY="$(command -v python3)"

if [ -z "$PY" ]; then
  echo "python3 が見つかりません。Xcode Command Line Tools を入れてください:" >&2
  echo "  xcode-select --install" >&2
  exit 1
fi

if [ "$HOST" != "127.0.0.1" ] && [ -z "$TOKEN" ]; then
  echo "ローカル以外に公開するときは合言葉を指定してください:" >&2
  echo "  scripts/install-service.sh $PORT $HOST <合言葉>" >&2
  exit 1
fi
TOKEN_ARGS=""
[ -n "$TOKEN" ] && TOKEN_ARGS="
    <string>--token</string>
    <string>$TOKEN</string>"

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
    <string>$HOST</string>
    <string>--port</string>
    <string>$PORT</string>$TOKEN_ARGS
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
URL="http://127.0.0.1:$PORT/"
[ -n "$TOKEN" ] && URL="$URL?t=$TOKEN"
launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1 \
  && echo "自動起動を設定しました → $URL" \
  || { echo "起動に失敗しました。$HERE/data/service.log を確認してください。" >&2; exit 1; }
if [ "$HOST" != "127.0.0.1" ]; then
  IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)"
  [ -n "$IP" ] && echo "スマホからは → http://$IP:$PORT/?t=$TOKEN"
fi
echo "停止・解除は scripts/uninstall-service.sh"
