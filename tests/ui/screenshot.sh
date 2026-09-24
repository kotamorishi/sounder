#!/bin/sh
# 画面の見た目を確かめるための撮影スクリプト（macOS の Chrome を使う）。
#
#   tests/ui/screenshot.sh 出力先ディレクトリ [ポート]
#
# ヘッドレス Chrome は macOS だとウィンドウ幅が 500px 未満にならないため、
# iframe に実寸の幅を与えて中身を撮る。撮影用の _frame.html は終了時に消す。
set -e
OUT="${1:-/tmp/sounder-shots}"
PORT="${2:-8799}"
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
CHROME="${CHROME:-/Applications/Google Chrome.app/Contents/MacOS/Google Chrome}"

[ -x "$CHROME" ] || { echo "Google Chrome が見つかりません" >&2; exit 1; }
curl -sf -m 3 "http://127.0.0.1:$PORT/api/now" >/dev/null 2>&1 || {
  echo "ポート $PORT でサーバが動いていません（python3 -m sounder --port $PORT）" >&2; exit 1; }

mkdir -p "$OUT"
cp "$HERE/_frame.html" "$ROOT/web/_frame.html"
trap 'rm -f "$ROOT/web/_frame.html"' EXIT

shot() { # 名前 幅 高さ ページ [クリックするセレクタ]
  name="$1"; w="$2"; h="$3"; page="$4"; click="$5"
  win_w=$((w > 500 ? w : 500))
  url="http://127.0.0.1:$PORT/_frame.html?w=$w&h=$h&u=$(printf %s "$page" | sed 's|/|%2F|g;s|#|%23|g')"
  [ -n "$click" ] && url="$url&click=$(printf %s "$click" | sed 's|#|%23|g;s| |%20|g')"
  rm -f "$OUT/$name.png"
  "$CHROME" --headless=new --disable-gpu --no-first-run --no-default-browser-check \
    --user-data-dir="$OUT/.chrome" --hide-scrollbars \
    --window-size="$win_w,$((h + 20))" --screenshot="$OUT/$name.png" \
    --virtual-time-budget=5000 "$url" >/dev/null 2>&1 &
  pid=$!
  i=0
  while [ $i -lt 100 ]; do
    [ -s "$OUT/$name.png" ] && sleep 0.4 && break
    sleep 0.25; i=$((i + 1))
  done
  kill $pid 2>/dev/null || true
  wait $pid 2>/dev/null || true          # 終了メッセージを出さずに片付ける
  pkill -f "Google Chrome.*headless" 2>/dev/null || true
  [ -s "$OUT/$name.png" ] && echo "  $name.png" || echo "  $name.png 失敗"
}

echo "撮影先: $OUT"
shot phone-schedules 390 900  "/"
shot phone-timeline  390 900  "/#timeline"
shot phone-sounds    390 900  "/#sounds"
shot phone-settings  390 900  "/#settings"
shot phone-editor    390 900  "/" ".alarm-main"
shot phone-repeat    390 900  "/" ".alarm-main|[data-push=pg-repeat]"
shot phone-new       390 900  "/" "#new-btn"
shot desk-schedules  1100 900 "/"
shot desk-timeline   1100 900 "/#timeline"
