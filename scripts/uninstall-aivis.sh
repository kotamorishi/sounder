#!/bin/sh
# AivisSpeech のエンジンの自動起動を解除する（AivisSpeech.app と声のモデルはそのまま残す）。
LABEL="com.local.sounder-aivis"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "AivisSpeech のエンジンの自動起動を解除しました。sounder は標準の声で代わりに読み上げます。"
