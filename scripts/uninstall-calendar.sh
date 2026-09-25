#!/bin/sh
# カレンダー連携の LaunchAgent を解除する（補助アプリと data/calendar.json は残す）。
LABEL="com.local.sounder-calendar"
launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"
echo "カレンダー連携の自動実行を解除しました。"
echo "カレンダーへのアクセス許可は システム設定 → プライバシーとセキュリティ → カレンダー から外せます。"
