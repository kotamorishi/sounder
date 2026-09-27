#!/bin/sh
# 天気の LaunchAgent を解除する（補助アプリと data/weather.json は残す）。
LABEL="com.local.sounder-weather"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "天気の自動取得を解除しました。"
