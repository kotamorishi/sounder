"""効果音ラボ（https://soundeffect-lab.info/）の効果音を、この Mac に取ってくる。

効果音ラボの音は無料で使えるが再配布は禁止なので、リポジトリには入れない。
各自の Mac で次を実行して sounds/effects/<分類>/ にダウンロードする（sounds/effects は git の外）。

  python3 -m sounder.effects                       ボタン・システム音（既定）
  python3 -m sounder.effects https://soundeffect-lab.info/sound/anime/   ほかの分類も同じ形で

分類ごとに manifest.json（ファイル名・題名・説明）を置き、画面の一覧とランダム再生に使う。
相手のサーバに負担をかけないよう、1 ファイルずつ間をあけて取る。取得済みのものは取り直さない。
"""

from __future__ import annotations

import html
import json
import re
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path

DEFAULT_PAGES = ["https://soundeffect-lab.info/sound/button/"]
UA = "Mozilla/5.0 (Macintosh) sounder/1.0 (personal use)"
ITEM_RE = re.compile(r'<li><span>([^<]+)</span>([^<]*)<a href="([^"]+\.mp3)"')
TITLE_RE = re.compile(r"<h1>([^<]+)</h1>")
DELAY = 1.0


def parse_page(page_html: str) -> tuple[str, list[dict]]:
    """ページの題名と、効果音の一覧（題名・説明・mp3 の相対パス）を取り出す。"""
    m = TITLE_RE.search(page_html)
    title = re.sub(r"\[\d+\]$", "", html.unescape(m.group(1)).strip()) if m else ""
    items = []
    for name, desc, href in ITEM_RE.findall(page_html):
        items.append({"title": html.unescape(name).strip(), "desc": html.unescape(desc).strip(),
                      "href": href})
    return title, items


def category_of(url: str) -> str:
    """https://soundeffect-lab.info/sound/button/ → button"""
    parts = [p for p in urllib.parse.urlparse(url).path.split("/") if p]
    return re.sub(r"[^A-Za-z0-9_-]", "_", parts[-1].removesuffix(".html")) if parts else "misc"


def _get(url: str, referer: str | None = None) -> bytes:
    # python.org 版の Python は証明書を持っていないことがあるので、macOS の curl で取る
    argv = ["curl", "-fsSL", "--max-time", "60", "-A", UA]
    if referer:
        argv += ["-e", referer]
    r = subprocess.run(argv + [url], capture_output=True)
    if r.returncode != 0:
        raise OSError(f"{url} を取得できませんでした: {r.stderr.decode('utf-8', 'replace').strip()}")
    return r.stdout


def fetch(page_url: str, dest_root: Path, *, out=print, delay: float = DELAY) -> int:
    """1 ページ分を dest_root/<分類>/ に取る。新しく取った数を返す。"""
    title, items = parse_page(_get(page_url).decode("utf-8", "replace"))
    cat = category_of(page_url)
    dest = dest_root / cat
    dest.mkdir(parents=True, exist_ok=True)
    got = 0
    files = []
    for it in items:
        name = Path(urllib.parse.urlparse(it["href"]).path).name
        path = dest / name
        if not path.exists():
            data = _get(urllib.parse.urljoin(page_url, it["href"]), referer=page_url)
            tmp = path.with_suffix(".part")
            tmp.write_bytes(data)
            tmp.replace(path)
            got += 1
            out(f"  {it['title']}（{name}）")
            time.sleep(delay)
        files.append({"file": name, "title": it["title"], "desc": it["desc"]})
    manifest = {"title": title or cat, "source": page_url,
                "fetched": time.strftime("%Y-%m-%d"), "files": files}
    (dest / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=1))
    out(f"{title or cat}: {len(files)} 個（新しく {got} 個）→ {dest}")
    return got


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    root = Path(__file__).resolve().parent.parent / "sounds" / "effects"
    for url in args or DEFAULT_PAGES:
        fetch(url, root)
    print("効果音ラボの音は無料で使えますが、再配布（ファイルを人に渡す・公開する）は禁止です。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
