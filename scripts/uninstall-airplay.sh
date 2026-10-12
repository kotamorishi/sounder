#!/bin/sh
# AirPlay の部品を消す。「鳴らす場所」を選んでいた予定は、この Mac で鳴る。
set -e
HERE="$(cd "$(dirname "$0")/.." && pwd)"
rm -rf "$HERE/.venv-airplay"
echo "消しました"
