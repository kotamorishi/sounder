#!/bin/sh
# 機械学習の読み上げ（Qwen3-TTS）を入れて、ログイン時に自動起動する LaunchAgent を登録する。
#
#   scripts/install-tts.sh [ポート]    既定 8778。変えたら sounder に SOUNDER_TTS_URL を渡す
#
# Apple Silicon の Mac 専用。初回はモデル（約 4GB）をダウンロードするので時間がかかる。
# 動いている間はメモリを 3〜4GB ほど使う（しばらく使われなければ sounder が止める）。止まっていても sounder は標準の声（say）で読み上げる。
set -e

PORT="${1:-8778}"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$HERE/.venv-tts"
LABEL="com.local.sounder-tts"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ "$(uname -m)" != "arm64" ]; then
  echo "Qwen3-TTS（MLX）は Apple Silicon の Mac でしか動きません。" >&2
  exit 1
fi
if ! command -v uv >/dev/null 2>&1; then
  echo "uv が必要です。先に入れてください:  brew install uv" >&2
  exit 1
fi

echo "Python 環境を用意しています → $VENV"
uv venv -q --allow-existing -p 3.12 "$VENV"
uv pip install -q --python "$VENV/bin/python" "mlx-audio==0.5.6" soundfile

mkdir -p "$HOME/Library/LaunchAgents" "$HERE/data"
cat > "$PLIST" <<PLIST_END
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$VENV/bin/python</string>
    <string>$HERE/tts/qwen_server.py</string>
    <string>--port</string>
    <string>$PORT</string>
  </array>
  <key>WorkingDirectory</key><string>$HERE</string>
  <!-- 常駐させっぱなしにしない。sounder が必要なときに起こし、使われなくなったら止める -->
  <key>RunAtLoad</key><false/>
  <key>KeepAlive</key><false/>
  <key>ProcessType</key><string>Interactive</string>
  <key>StandardOutPath</key><string>$HERE/data/tts.log</string>
  <key>StandardErrorPath</key><string>$HERE/data/tts.log</string>
  <key>EnvironmentVariables</key>
  <dict><key>PYTHONUNBUFFERED</key><string>1</string></dict>
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
echo "Qwen3-TTS を登録しました。常駐はさせず、声が要るときに sounder が起こします（数秒〜10 秒）。"
echo "声の一覧に出すため、いったん起こします（初回はモデル約 4GB のダウンロードに時間がかかります）。"
launchctl kickstart "gui/$(id -u)/$LABEL"
echo "進み具合: tail -f $HERE/data/tts.log"
echo "準備ができると、sounder の声の一覧に「Ono Anna（Qwen3-TTS）」などが出ます。"
echo "停止・解除は scripts/uninstall-tts.sh"
