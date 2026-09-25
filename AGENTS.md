# AGENTS.md — sounder のセットアップと運用（AI エージェント向け）

この文書だけで、別の Mac に sounder を入れて動かせるように書いています。
上から順に実行し、各手順の「確認」が通ってから次へ進んでください。
**人の操作が要る手順** は 👤 印を付けています（画面の許可ダイアログ、スマホの操作など）。エージェントはそこで止めて、利用者に頼んでください。

## 0. 全体像

- `sounder/` … 本体（Python 標準ライブラリのみ）。HTTP サーバ（既定 `127.0.0.1:8777`）＋ 1 秒ごとのスケジューラ。
  音は `afplay`、標準の読み上げは `say`。
- `web/` … 画面（ビルド不要の HTML/JS/CSS）。本体が配信する。
- 任意の部品（どれも別プロセス、無くても本体は動く。止まっていれば標準の `say` で代わりに読む）

| 部品 | 置き場 | LaunchAgent のラベル | ポート | ログ |
| --- | --- | --- | --- | --- |
| 本体 | `sounder/` | `com.local.sounder` | 8777 | `data/service.log`・`data/events.log` |
| Qwen3-TTS | `tts/qwen_server.py`（`.venv-tts/`） | `com.local.sounder-tts` | 127.0.0.1:8778 | `data/tts.log` |
| AivisSpeech | `/Applications/AivisSpeech.app` | `com.local.sounder-aivis` | 127.0.0.1:10101 | `data/aivis.log` |
| カレンダー連携 | `tools/calendar/`（`SounderCalendar.app`） | `com.local.sounder-calendar` | なし（5 分ごとに `data/calendar.json` を書く） | `data/calendar.log` |

Qwen3-TTS と AivisSpeech は常駐させません。本体が必要なときに `launchctl kickstart` で起こし、
設定「休ませる」（既定 30 分）使われなければ止めます。`scripts/status.sh` で全部の状態が見られます。

## 1. 前提の確認

```sh
sw_vers -productVersion        # macOS 14 以上を想定（カレンダー連携は 14 以上が必須）
uname -m                       # arm64 なら Qwen3-TTS も使える（x86_64 は Qwen3-TTS 不可）
python3 --version              # 3.9 以上（macOS 付属の /usr/bin/python3 3.9 でも可）
which afplay say               # どちらもあること
```

Mac がスリープすると鳴らないので、👤 システム設定 → エネルギー で「ディスプレイがオフのときに自動でスリープさせない」をオンにしてもらう。

## 2. 本体

```sh
git clone https://github.com/kotamorishi/sounder.git ~/sounder && cd ~/sounder
python3 -m unittest discover -s tests -q        # 確認: 最後に OK
```

### 2a. この Mac の中だけで使う

```sh
scripts/install-service.sh                     # ログイン時に自動起動（127.0.0.1:8777）
```

### 2b. スマホ（同じ LAN）からも使う

合言葉（トークン）を必ず付ける。推測されにくい文字列を作って渡す。

```sh
TOKEN=$(python3 -c "import secrets;print(secrets.token_urlsafe(12))")
scripts/install-service.sh 8777 0.0.0.0 "$TOKEN"
```

確認:

```sh
scripts/status.sh              # 「サーバ: 応答あり」と、合言葉つきの URL（スマホ用も）が出る
curl -s "http://127.0.0.1:8777/api/now?t=$TOKEN" | head -c 200
```

- 初回起動で内蔵チャイムを `sounds/builtin/` に生成する（数秒）。
- 設定・予定は `data/config.json`（これだけバックアップすればよい）。
- 👤 スマホで `http://<MacのIP>:8777/?t=<合言葉>` を開いてもらう。以後はブラウザが合言葉を覚える。
- LAN 直は HTTPS ではない。家の外から使う・PWA にするなら「7. Tailscale」。

## 3. 効果音（任意・約 290MB）

効果音ラボ（soundeffect-lab.info）の音を各自の Mac に取り込む。**再配布禁止なのでリポジトリには入れない**（`sounds/effects/` は git 対象外）。

```sh
python3 -m sounder.effects      # 声素材以外の全分類。1 秒に 1 ファイル、約 30 分。取得済みは飛ばす
ls sounds/effects/*/manifest.json   # 確認: 分類ごとに manifest.json がある
```

分類を選ぶなら URL を渡す（例: `python3 -m sounder.effects https://soundeffect-lab.info/sound/button/`）。
本体の再起動は不要（一覧は毎回ディスクから読む）。

## 4. Qwen3-TTS（任意・Apple Silicon のみ）

自然な日本語・英語の声。話者ごとの「アナウンサー風」、言葉で説明して声を作る機能もある。

```sh
brew install uv                 # uv が無ければ
scripts/install-tts.sh          # .venv-tts/ を作り、LaunchAgent を登録して一度起こす
```

- 初回は Hugging Face からモデル（CustomVoice 約 3.6GB）を取る。声を作る機能を使うと VoiceDesign・Base（各約 3.6GB）も取る。
- 動いている間のメモリは 3〜7GB。16GB 以上の Mac を推奨。

確認:

```sh
tail -f data/tts.log            # 「待ち受けを始めました」が出るまで待つ（初回は数分）
curl -s http://127.0.0.1:8778/speakers    # speakers に ono_anna などが並ぶ
```

既定の声にするなら: `curl -s -X PATCH "http://127.0.0.1:8777/api/settings?t=$TOKEN" -H 'Content-Type: application/json' -d '{"default_voice":"qwen:ono_anna@announcer"}'`

## 5. AivisSpeech（任意・日本語の声を増やす）

```sh
# Apple Silicon 版を /Applications に入れる（Intel Mac は x64 版の zip）
curl -L -o /tmp/aivis.zip https://github.com/Aivis-Project/AivisSpeech/releases/latest/download/AivisSpeech-macOS-arm64-$(curl -s https://api.github.com/repos/Aivis-Project/AivisSpeech/releases/latest | python3 -c "import json,sys;print(json.load(sys.stdin)['tag_name'])").zip
ditto -x -k /tmp/aivis.zip /tmp/aivis && ditto /tmp/aivis/AivisSpeech/AivisSpeech.app /Applications/AivisSpeech.app
xattr -dr com.apple.quarantine /Applications/AivisSpeech.app
scripts/install-aivis.sh        # エンジンだけを LaunchAgent に登録して一度起こす（初回はモデル取得で 1〜3 分）
```

確認: `curl -s http://127.0.0.1:10101/speakers | head -c 300`（「まお」が出る）

声を増やす（AivisHub の公開モデル。一覧 API: `https://api.aivis-project.com/v1/aivm-models/search?sort=download&limit=20`）:

```sh
curl -X POST http://127.0.0.1:10101/aivm_models/install \
  -F "url=https://api.aivis-project.com/v1/aivm-models/<aivm_model_uuid>/download?model_type=AIVMX"
```

エンジン側のダウンロードがタイムアウトする大きいモデルは、`curl -L -o x.aivmx <上の URL>` で取ってから `-F "file=@x.aivmx"` で入れる。
画面のある AivisSpeech アプリを同時に使うとポート 10101 がぶつかることがある（その場合は `scripts/uninstall-aivis.sh`）。

## 6. カレンダー連携（任意・macOS 14 以上）

カレンダー.app に出ている予定（iCloud の共有カレンダー・購読カレンダーなど）を読み上げる。ネットワークは使わない。

```sh
xcode-select -p || xcode-select --install     # swiftc が要る
scripts/install-calendar.sh                   # SounderCalendar.app を作り、5 分ごとに data/calendar.json へ書き出す
```

- 👤 **Mac の画面に「“sounder カレンダー連携”がカレンダーへのアクセスを求めています」が出るので「フルアクセスを許可」を押してもらう。**
  押されるまで最初の実行は待ったまま。出ない・拒否した場合は システム設定 → プライバシーとセキュリティ → カレンダー でオン。
- 👤 共有カレンダーを読むには、その Mac が iCloud にサインインし「カレンダー」がオンであること。
- 確認: `scripts/status.sh` に「カレンダー連携: authorized / N 件」。
- 👤 どのカレンダーを読むか・何分前に読むかは、画面の 設定 → カレンダー連携 で選んでもらう（既定はオフ）。

## 7. Tailscale で HTTPS・PWA（任意）

tailnet の中だけに HTTPS で出す（インターネットには出ない）。前提: この Mac が Tailscale に参加し、tailnet で HTTPS 証明書が有効。

```sh
TS=/Applications/Tailscale.app/Contents/MacOS/Tailscale   # CLI は PATH に無いことが多い
$TS status --json | python3 -c "import json,sys;d=json.load(sys.stdin);print(d['Self']['DNSName'], d.get('CertDomains'))"
$TS serve --bg --https=443 http://127.0.0.1:8777          # 設定は再起動後も残る。解除: $TS serve --https=443 off
curl -s -o /dev/null -w "%{http_code}\n" "https://<DNSName>/?t=$TOKEN"   # 確認: 200
```

👤 スマホにも Tailscale を入れて同じアカウントでログイン → `https://<DNSName>/?t=<合言葉>` を開く →
共有メニュー「ホーム画面に追加」（Android の Chrome は「アプリをインストール」）。iPhone のホーム画面版は Cookie が別なので初回だけ合言葉を入力。

## 8. 運用

```sh
scripts/status.sh                                        # 全部品の状態・直近のログ
launchctl kickstart -k gui/$(id -u)/com.local.sounder    # コードを更新したら本体を再起動（web/ は再起動不要）
launchctl kill SIGTERM gui/$(id -u)/com.local.sounder-tts   # tts/qwen_server.py を更新したら（次に使うとき新しいコードで起きる）
```

- 解除: `scripts/uninstall-service.sh` / `uninstall-tts.sh` / `uninstall-aivis.sh` / `uninstall-calendar.sh`。
- 環境変数（通常は不要）: `SOUNDER_HOME`（データの置き場）、`SOUNDER_TOKEN`、`SOUNDER_TTS_URL`、`SOUNDER_AIVIS_URL`、
  `SOUNDER_MANAGE_TTS=0`（エンジンを起こす・止めるをしない。テストはこれで本物の LaunchAgent に触らない）。
- 読み上げは `data/tts-cache/` に取り置き、30 日使われなければ消える（消しても作り直すだけ）。
- TDSB の休校日データは年度ごと。翌年度は公式の Key Dates PDF（https://www.tdsb.on.ca/About-Us/School-Year-Calendar）から
  `sounder/daysoff.py` の `TDSB_YEARS` に同じ形で 1 年分を足し、`tests/test_daysoff.py` にテストを足す。

## 9. よくあるつまずき

| 症状 | 原因と対処 |
| --- | --- |
| `python3 -m sounder.effects` が証明書エラー | 旧版の名残。現在は macOS の `curl` で取るので、最新の main を使う |
| `launchctl bootstrap` が `5: Input/output error` | 解除直後の再登録。スクリプトは自動で再試行する。手で打つなら数秒待つ |
| 機械学習の声が一覧にない | エンジンが一度も起きていない。`scripts/status.sh` を見て、`launchctl kickstart gui/$(id -u)/com.local.sounder-tts`（または `-aivis`） |
| 読み上げが鳴らず `say` の声になる | エンジンが起きられなかった。`data/tts.log` / `data/aivis.log` を見る |
| カレンダーが 0 件・`denied` | 👤 プライバシー設定でカレンダーのアクセスを許可。アプリを作り直すと許可を求め直されることがある |
| 何も鳴らない | 設定の「すべての予定を鳴らす」・禁止時間・Mac のスリープ・音量（本体の音量 × 設定の音量） |

## 10. コードを変えるとき

- 本体は Python 標準ライブラリのみ（pip で何も入れない）。機械学習系は `tts/` など別プロセスに閉じ込める。
- 設定項目を足したら `sounder/config.py` の `DEFAULT_SETTINGS` にも必ず足す（無い項目は起動時に読み捨てられる）。
- テスト: `python3 -m unittest discover -s tests`（macOS 付属の Python 3.9 と新しい版の両方で通すこと）。
- 画面を変えたら `tests/ui/README.md` の手順でスクリーンショットを撮って確認する。
- コメント・画面の文言は日本語。
