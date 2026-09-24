#!/bin/sh
# AivisSpeech の音声合成エンジンを、ログイン時に自動起動する LaunchAgent として登録する。
#
#   scripts/install-aivis.sh [ポート]    既定 10101。変えたら sounder に SOUNDER_AIVIS_URL を渡す
#
# 先に AivisSpeech（https://aivis-project.com/）を /Applications に入れておくこと。
# 日本語専用。止まっていても sounder は標準の声（say）で読み上げる。
set -e

PORT="${1:-10101}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
ENGINE="/Applications/AivisSpeech.app/Contents/Resources/AivisSpeech-Engine/run"
LABEL="com.local.sounder-aivis"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ ! -x "$ENGINE" ]; then
  echo "AivisSpeech が見つかりません: $ENGINE" >&2
  echo "https://github.com/Aivis-Project/AivisSpeech/releases から入れてください。" >&2
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
    <string>$ENGINE</string>
    <string>--host</string>
    <string>127.0.0.1</string>
    <string>--port</string>
    <string>$PORT</string>
  </array>
  <!-- 常駐させっぱなしにしない。sounder が必要なときに起こし、使われなくなったら止める -->
  <key>RunAtLoad</key><false/>
  <key>KeepAlive</key><false/>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$HERE/data/aivis.log</string>
  <key>StandardErrorPath</key><string>$HERE/data/aivis.log</string>
</dict>
</plist>
PLIST_END

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
# 解除した直後は登録に失敗することがあるので、少し待って何度か試す
i=0
until launchctl bootstrap "gui/$(id -u)" "$PLIST" 2>/dev/null; do
  i=$((i + 1))
  [ $i -ge 10 ] && { echo "登録に失敗しました（launchctl bootstrap）" >&2; exit 1; }
  sleep 1
done
echo "AivisSpeech のエンジンを登録しました。常駐はさせず、声が要るときに sounder が起こします。"
echo "声の一覧に出すため、いったん起こします（初回はモデルのダウンロードで 1〜3 分かかります）。"
launchctl kickstart "gui/$(id -u)/$LABEL"
echo "進み具合: tail -f $HERE/data/aivis.log"
echo "準備ができると、sounder の声の一覧に「まお・ノーマル（AivisSpeech）」などが出ます。"
echo "停止・解除は scripts/uninstall-aivis.sh"
