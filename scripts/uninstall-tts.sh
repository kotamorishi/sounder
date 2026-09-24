#!/bin/sh
# Qwen3-TTS の自動起動を解除する。sounder は標準の声（say）で読み上げるようになる。
LABEL="com.local.sounder-tts"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
HERE="$(cd "$(dirname "$0")/.." && pwd)"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$PLIST"
echo "Qwen3-TTS の自動起動を解除しました。"
echo "ディスクも空けるなら、次を削除してください:"
echo "  $HERE/.venv-tts                                   （Python 環境）"
echo "  ~/.cache/huggingface/hub/models--mlx-community--Qwen3-TTS-*  （モデル 約 4GB）"
