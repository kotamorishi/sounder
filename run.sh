#!/bin/sh
# sounder を前面で起動する。停止は Ctrl-C。
# 例) ./run.sh                 → http://127.0.0.1:8777/
#     ./run.sh --port 9000
cd "$(dirname "$0")" || exit 1
exec python3 -m sounder "$@"
