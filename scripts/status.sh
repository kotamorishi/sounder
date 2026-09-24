#!/bin/sh
# 動いているかどうかと、直近のログを見る。
LABEL="com.local.sounder"
PORT="${1:-8777}"
if launchctl print "gui/$(id -u)/$LABEL" >/dev/null 2>&1; then
  echo "LaunchAgent: 登録済み"
else
  echo "LaunchAgent: 未登録（scripts/install-service.sh で登録）"
fi
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
TOKEN=""
if [ -f "$PLIST" ]; then
  # plist から --token の次の値を取り出す
  TOKEN="$(awk '/<string>--token<\/string>/{getline; gsub(/.*<string>|<\/string>.*/, ""); print; exit}' "$PLIST")"
  PORT="$(awk '/<string>--port<\/string>/{getline; gsub(/.*<string>|<\/string>.*/, ""); print; exit}' "$PLIST")"
fi
Q=""
[ -n "$TOKEN" ] && Q="?t=$TOKEN"
if curl -sf -m 3 "http://127.0.0.1:$PORT/api/now$Q" >/dev/null 2>&1; then
  echo "サーバ: 応答あり  http://127.0.0.1:$PORT/$Q"
  IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null)"
  [ -n "$IP" ] && [ -n "$TOKEN" ] && echo "スマホから: http://$IP:$PORT/$Q"
else
  echo "サーバ: 応答なし（ポート $PORT）"
fi
LOG="$(cd "$(dirname "$0")/.." && pwd)/data/events.log"
[ -f "$LOG" ] && { echo "--- 直近のログ ---"; tail -10 "$LOG"; }
