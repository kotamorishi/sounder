#!/bin/sh
# 自動起動を解除する（設定やサウンドは消さない）。
set -e
LABEL="com.local.sounder"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "自動起動を解除しました。"
