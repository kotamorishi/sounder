#!/bin/sh
# AirPlay（HomePod など）で鳴らすための部品（pyatv）を .venv-airplay/ に入れる。
#
#   scripts/install-airplay.sh
#
# 常駐はしない（鳴らすたびに airplay/airplay_send.py を呼ぶ）。本体の再起動も不要。
# 入れたら、予定の編集画面に「鳴らす場所」が出る。
set -e
HERE="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$HERE/.venv-airplay"
if ! command -v uv >/dev/null 2>&1; then
  echo "uv が必要です。先に入れてください:  brew install uv" >&2
  exit 1
fi
echo "Python 環境を用意しています → $VENV"
uv venv -q --allow-existing -p 3.12 "$VENV"
uv pip install -q --python "$VENV/bin/python" "pyatv==0.18.0"
echo "見つかった AirPlay の機器:"
"$VENV/bin/python" "$HERE/airplay/airplay_send.py" scan | python3 -c \
  "import json,sys;[print('  -', d['name'], '/', d['model'], '/', d['address']) for d in json.load(sys.stdin)]"
