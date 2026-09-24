"""効果音ラボ（https://soundeffect-lab.info/）の効果音を、この Mac に取ってくる。

効果音ラボの音は無料で使えるが再配布は禁止なので、リポジトリには入れない。
各自の Mac で次を実行して sounds/effects/<分類>/ にダウンロードする（sounds/effects は git の外）。

  python3 -m sounder.effects                       全分類（ボタン・環境音・動物・生活・声素材など）
  python3 -m sounder.effects https://soundeffect-lab.info/sound/anime/   分類を選んで

分類のトップページから、同じ分類の続きのページ（battle2.html など）もたどって 1 つの分類にまとめる。

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

SITE = "https://soundeffect-lab.info"
DEFAULT_PAGES = [SITE + p for p in (
    "/sound/button/", "/sound/environment/", "/sound/animal/", "/sound/anime/", "/sound/battle/",
    "/sound/machine/", "/sound/various/", "/sound/voice/game.html",
)]
UA = "Mozilla/5.0 (Macintosh) sounder/1.0 (personal use)"
# 声素材のページは題名の前に小さなアイコンが入る
ITEM_RE = re.compile(r'<li>(?:<img[^>]*>)?<span>([^<]+)</span>([^<]*)<a href="([^"]+\.mp3)"')
COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
TITLE_RE = re.compile(r"<h1>([^<]+)</h1>")
DELAY = 1.0


def parse_page(page_html: str) -> tuple[str, list[dict]]:
    """ページの題名と、効果音の一覧（題名・説明・mp3 の相対パス）を取り出す。"""
    m = TITLE_RE.search(page_html)
    title = html.unescape(m.group(1)).strip() if m else ""
    title = re.sub(r"\[\d+\]$", "", title.split(" - ")[0]).strip()  # 「声素材 - ゲームの戦闘」→「声素材」
    items = []
    for name, desc, href in ITEM_RE.findall(page_html):
        items.append({"title": html.unescape(name).strip(), "desc": html.unescape(desc).strip(),
                      "href": href})
    return title, items


def category_of(url: str) -> str:
    """https://soundeffect-lab.info/sound/button/ → button、/sound/voice/game.html → voice"""
    parts = [p for p in urllib.parse.urlparse(url).path.split("/") if p]
    if len(parts) >= 2 and parts[0] == "sound":
        return re.sub(r"[^A-Za-z0-9_-]", "_", parts[1])
    return re.sub(r"[^A-Za-z0-9_-]", "_", parts[-1].removesuffix(".html")) if parts else "misc"


def sibling_pages(page_url: str, page_html: str) -> list[str]:
    """同じ分類の続きのページ（/sound/battle/battle2.html など）。コメントアウトされたリンクは除く。"""
    d = urllib.parse.urlparse(page_url).path.rsplit("/", 1)[0] + "/"
    found = re.findall(r'href="(%s[^"#?/]+\.html)"' % re.escape(d), COMMENT_RE.sub("", page_html))
    return [urllib.parse.urljoin(page_url, u) for u in dict.fromkeys(found)]


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
    """1 分類（続きのページも含めて）を dest_root/<分類>/ に取る。新しく取った数を返す。"""
    first = _get(page_url).decode("utf-8", "replace")
    title, _ = parse_page(first)
    pages = [(page_url, first)]
    for url in sibling_pages(page_url, first):
        if url != page_url:
            time.sleep(delay)
            try:
                pages.append((url, _get(url).decode("utf-8", "replace")))
            except OSError as exc:
                out(f"  {url} を読めませんでした（{exc}）")
    cat = category_of(page_url)
    dest = dest_root / cat
    dest.mkdir(parents=True, exist_ok=True)
    got = 0
    files = []
    seen = set()
    items = [(url, it) for url, body in pages for it in parse_page(body)[1]]
    for url, it in items:
        name = Path(urllib.parse.urlparse(it["href"]).path).name
        if name in seen:
            continue
        seen.add(name)
        path = dest / name
        if not path.exists():
            try:
                data = _get(urllib.parse.urljoin(url, it["href"]), referer=url)
            except OSError as exc:
                out(f"  {it['title']} を取れませんでした（{exc}）")
                continue
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
