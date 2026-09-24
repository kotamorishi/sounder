# 画面の見た目を確かめる

`screenshot.sh` は Chrome をヘッドレスで動かして、スマホ幅と Mac 幅の画面を PNG に撮ります。
見た目の崩れ（はみ出し・重なり）は目で見ないと分からないので、画面を直したら撮って確認します。

```sh
# 見本データを入れた別インスタンスを立てて撮るのが安全（本番の設定を汚さない）
python3 -m sounder --home /tmp/sounder-demo --port 8799 &
tests/ui/screenshot.sh /tmp/sounder-shots 8799
```

`_frame.html` は撮影用の器です。ヘッドレス Chrome は macOS だとウィンドウ幅が 500px 未満に
ならないため、iframe に実寸（390px など）を与えてその中身を撮ります。撮影中だけ `web/` に
コピーされ、終了時に消えます。

画面の中身（描画がエラーなく終わるか）は `tests/test_web_lib.py` が自動で確かめます。
Chrome の場所が違うときは `CHROME=/path/to/chrome tests/ui/screenshot.sh ...` のように渡してください。
