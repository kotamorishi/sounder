"""AirPlay（HomePod など）へ音を送る補助。.venv-airplay の Python（pyatv 入り）で動かす。

    airplay_send.py scan
        → 標準出力に JSON: [{"id", "name", "model", "address"}, ...]
    airplay_send.py play --device ID[@ADDR][=音量] [--device ...] [--volume 0〜1] -- ファイル...
        → すべての機器につないだところで標準出力に 1 行 {"connected": [id...], "failed": [id...]} を出し、
          つながった機器で一斉にファイルを順に鳴らす。鳴り終わったら終了コード 0
          終了コード 2 = どの機器にもつながらない（何も鳴っていない）。sounder はこの Mac で代わりに鳴らす
        --until 時刻（UNIX 秒）を付けると、最初のファイルをその時刻まで切れ目なく繰り返す（1 本の流れのまま）

sounder 本体は標準ライブラリだけで動かすため、pyatv はこの別プロセスに閉じ込める。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time

import pyatv
import pyatv.protocols.raop as raop
from pyatv.const import Protocol
from pyatv.protocols.raop import audio_source

_open_source = raop.open_source


class LoopSource(audio_source.FileSource):
    """ファイルの終わりで先頭に戻り、until まで音を出し続ける。つなぎ直さないので切れ目がない。"""

    until = 0.0

    async def readframes(self, nframes: int) -> bytes:
        if time.time() >= self.until or not self.samples:
            return audio_source.AudioSource.NO_FRAMES
        want = self.sample_size * self.channels * nframes
        out = bytearray()
        while len(out) < want:
            if self.pos >= len(self.samples):
                self.pos = 0
            chunk = self.samples[self.pos:self.pos + want - len(out)]
            self.pos += len(chunk)
            out += chunk
        return audio_source._to_audio_samples(bytes(out))

    @property
    def duration(self) -> int:
        return max(1, round(self.until - time.time()))


def loop_until(until: float) -> None:
    """これ以降の stream_file を、until まで繰り返す流れにする。"""
    async def open_loop(source, sample_rate, channels, sample_size):
        src = await _open_source(source, sample_rate, channels, sample_size)
        if isinstance(src, audio_source.FileSource):
            src.__class__ = LoopSource
            src.until = until
        return src
    raop.open_source = open_loop

NOT_FOUND = 2


async def scan(timeout: int) -> list[dict]:
    loop = asyncio.get_running_loop()
    found = await pyatv.scan(loop, timeout=timeout, protocol={Protocol.RAOP})
    out = []
    for conf in found:
        info = conf.device_info
        out.append({"id": conf.identifier, "name": conf.name, "address": str(conf.address),
                    "model": str(info.model_str or "")})
    return sorted(out, key=lambda d: d["name"])


async def find(ident: str, address: str | None):
    loop = asyncio.get_running_loop()
    if address:   # 前に見えたアドレスを先に見る（速い）。変わっていたら全体から探す
        found = await pyatv.scan(loop, timeout=3, hosts=[address], protocol={Protocol.RAOP})
        found = [c for c in found if c.identifier == ident or ident in c.all_identifiers]
        if found:
            return found[0]
    found = await pyatv.scan(loop, timeout=5, identifier=ident, protocol={Protocol.RAOP})
    return found[0] if found else None


async def connect(ident: str, address: str | None, volume: float | None):
    conf = await find(ident, address)
    if conf is None:
        print(f"AirPlay の機器が見つかりません: {ident}", file=sys.stderr)
        return None
    try:
        atv = await pyatv.connect(conf, asyncio.get_running_loop())
    except Exception as exc:  # noqa: BLE001
        print(f"つながりませんでした: {conf.name}: {exc!r}", file=sys.stderr)
        return None
    if volume is not None:
        try:
            await atv.audio.set_volume(max(0.0, min(100.0, volume * 100)))
        except Exception:  # noqa: BLE001  音量を変えられない機器でも鳴らす
            pass
    return atv


async def stream(atv, files: list[str]) -> bool:
    try:
        for f in files:
            await atv.stream.stream_file(f)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"送れませんでした: {exc!r}", file=sys.stderr)
        return False
    finally:
        atv.close()


async def play(devices: list[tuple[str, str | None, float | None]], volume: float | None, files: list[str]) -> int:
    atvs = await asyncio.gather(*(connect(i, a, volume if v is None else v) for i, a, v in devices))
    ok = [(ident, atv) for (ident, _a, _v), atv in zip(devices, atvs) if atv]
    print(json.dumps({"connected": [i for i, _ in ok],
                      "failed": [i for (i, _a, _v), atv in zip(devices, atvs) if not atv]}), flush=True)
    if not ok:
        return NOT_FOUND
    done = await asyncio.gather(*(stream(atv, files) for _i, atv in ok))
    return 0 if all(done) else 1


def parse_device(spec: str) -> tuple[str, str | None, float | None]:
    """ID[@アドレス][=音量] を (ID, アドレス, 音量) に。"""
    spec, _, vol = spec.partition("=")
    ident, _, address = spec.partition("@")
    return ident, address or None, float(vol) if vol else None


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("scan")
    s.add_argument("--timeout", type=int, default=4)
    p = sub.add_parser("play")
    p.add_argument("--device", action="append", required=True, help="ID か ID@アドレス（=音量 で機器ごとの音量）")
    p.add_argument("--volume", type=float)
    p.add_argument("--until", type=float, help="この時刻（UNIX 秒）まで最初のファイルを繰り返す")
    p.add_argument("files", nargs="+")
    a = ap.parse_args()
    if a.cmd == "scan":
        print(json.dumps(asyncio.run(scan(a.timeout)), ensure_ascii=False))
        return 0
    if a.until:
        loop_until(a.until)
        a.files = a.files[:1]
    devices = [parse_device(d) for d in a.device]
    return asyncio.run(play(devices, a.volume, a.files))


if __name__ == "__main__":
    sys.exit(main())
