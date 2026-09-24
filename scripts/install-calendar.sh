#!/bin/sh
# カレンダー連携の補助アプリ（SounderCalendar.app）を作って、5 分ごとに予定を書き出す LaunchAgent を登録する。
#
#   scripts/install-calendar.sh
#
# 初回は Mac の画面に「“sounder カレンダー連携”がカレンダーへのアクセスを求めています」と出るので、
# 「フルアクセスを許可」を押す。許可はシステム設定 → プライバシーとセキュリティ → カレンダー で変えられる。
# Xcode か Command Line Tools（swiftc）が必要。
set -e

HERE="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$HERE/tools/calendar"
APP="$HERE/tools/calendar/build/SounderCalendar.app"
LABEL="com.local.sounder-calendar"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
OUT="$HERE/data/calendar.json"

command -v swiftc >/dev/null 2>&1 || { echo "swiftc がありません: xcode-select --install" >&2; exit 1; }

echo "補助アプリを作っています → $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"
cp "$SRC/Info.plist" "$APP/Contents/Info.plist"
swiftc -O -framework EventKit "$SRC/main.swift" -o "$APP/Contents/MacOS/SounderCalendar"
# 署名（アドホック）。許可は署名に結びつくので、作り直したら許可を求め直されることがある
codesign --force --sign - "$APP"

mkdir -p "$HOME/Library/LaunchAgents" "$HERE/data"
cat > "$PLIST" <<PLIST_END
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$APP/Contents/MacOS/SounderCalendar</string>
    <string>--out</string>
    <string>$OUT</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>300</integer>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$HERE/data/calendar.log</string>
  <key>StandardErrorPath</key><string>$HERE/data/calendar.log</string>
</dict>
</plist>
PLIST_END

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
i=0
until launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null; do
  i=$((i + 1))
  [ $i -ge 10 ] && { echo "登録に失敗しました（launchctl bootstrap）" >&2; exit 1; }
  sleep 1
done
echo "登録しました。5 分ごとに $OUT へ予定を書き出します。"
echo "Mac の画面にカレンダーへのアクセスの確認が出たら「フルアクセスを許可」を押してください。"
echo "状態: cat $OUT | head"
