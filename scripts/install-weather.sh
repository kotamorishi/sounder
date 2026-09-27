#!/bin/sh
# 天気の補助アプリ（SounderWeather.app、WeatherKit）を作って、30 分ごとに天気を書き出す LaunchAgent を登録する。
#
#   scripts/install-weather.sh [チーム ID] [バンドル ID]
#
# 必要なもの: Xcode、Xcode にサインイン済みの Apple Developer Program（有料）のアカウント。
# チーム ID を省くと Xcode の設定から最初の有料チームを使う。バンドル ID の既定は com.<チーム ID>.sounder.weather。
# 初回は Mac の画面にキーチェーンの確認が出るので、ログインパスワードを入れて「常に許可」を押す。
# 作ったあと Apple Developer の Certificates, Identifiers & Profiles → Identifiers でこのバンドル ID を開き、
# Capabilities と App Services の両方で WeatherKit をオンにする（有効になるまで 30 分ほどかかることがある）。
set -e

HERE="$(cd "$(dirname "$0")/.." && pwd)"
PROJ="$HERE/tools/weather"
TEAM="${1:-$(defaults read com.apple.dt.Xcode IDEProvisioningTeamByIdentifier 2>/dev/null \
  | awk '/isFreeProvisioningTeam = 0/{paid=1} /teamID =/{gsub(/[ ;"]/,"",$3); if (paid) {print $3; exit}}')}"
[ -n "$TEAM" ] || { echo "チーム ID が分かりません。Xcode → 設定 → Accounts でサインインしてから、引数で渡してください。" >&2; exit 1; }
BUNDLE="${2:-com.$TEAM.sounder.weather}"
APP="$PROJ/build/Release/SounderWeather.app"
LABEL="com.local.sounder-weather"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

echo "ビルドしています（チーム $TEAM、$BUNDLE）"
# -scheme と -destination を付けると、この Mac を開発用の機器として登録したプロファイルを作ってくれる
xcodebuild -project "$PROJ/SounderWeather.xcodeproj" -scheme SounderWeather \
  -destination 'platform=macOS' -configuration Release \
  -allowProvisioningUpdates -allowProvisioningDeviceRegistration \
  DEVELOPMENT_TEAM="$TEAM" PRODUCT_BUNDLE_IDENTIFIER="$BUNDLE" SYMROOT="$PROJ/build" build -quiet

mkdir -p "$HOME/Library/LaunchAgents" "$HERE/data"
cat > "$PLIST" <<PLIST_END
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$APP/Contents/MacOS/SounderWeather</string>
    <string>--config</string>
    <string>$HERE/data/config.json</string>
    <string>--out</string>
    <string>$HERE/data/weather.json</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>1800</integer>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$HERE/data/weather.log</string>
  <key>StandardErrorPath</key><string>$HERE/data/weather.log</string>
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
echo "登録しました。30 分ごとに $HERE/data/weather.json へ天気を書き出します。"
echo "場所は sounder の 設定 → 天気 で決めてください。"
echo "WeatherKit を $BUNDLE でオンにするのを忘れずに（Capabilities と App Services）。"
