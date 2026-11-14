# 進捗記録

最終更新日: 2026-11-14

## プロジェクトの目的

Ciscoの英語マニュアル（PDF）を取得し、Open Knowledge Format（Frictionless Data Package仕様）へ自動変換するPythonパイプラインを構築する。検証済みの知識基盤を使ったコンフィグ生成を、段階的にAPIから利用可能にする。

処理フェーズ:

0. Discovery: Cisco公式ページから対象マニュアルを自動探索
1. Extraction: `marker-pdf`によるRaw Markdown抽出
2. Chunking: 章立てと文脈を維持した分割
3. Transformation: 生成AIによるMarkdown整形とメタデータ抽出
4. Packaging: `frictionless`によるデータパッケージ生成
5. Indexing: 検証済みパッケージのRAG増分更新（任意）

## 完了した作業

現在地: Catalyst 9200、9300、9500の全46パッケージ、32,375節を同一Qdrantストレージの製品別コレクションへ分割し、製品単位の増分更新と削除判定まで完了。

### 開発環境

- Python 3.12を使用
- `uv`による依存関係管理を導入
- `pyproject.toml`に`marker-pdf`と開発用`pytest`を定義
- `uv sync`の完了を確認
- 社内TLS検査CAを含む結合済みCAバンドルを`certs/local-ca-bundle.pem`へ配置
- バンドル再生成用の社内CA証明書を`certs/fj-sig-ca.pem`へ配置
- `.envrc`で`REQUESTS_CA_BUNDLE`と`SSL_CERT_FILE`をプロジェクト内CAバンドルへ統一
- `.envrc`で検証不能な証明書チェーンを提示するMenlo SecurityのHTTP(S)プロキシを解除
- 端末固有のPEMファイルはGitの追跡対象外

### マニュアル取得

`scripts/download_manuals.py`を実装した。

- `data/manuals.json`から取得対象を読み込む
- 次の規則でPDFを保存する

  ```text
  data/products/catalyst9300/raw/<document_id>/<filename>.pdf
  ```

- 一時ファイルへダウンロード後、成功時に原子的に配置する
- SHA-256、ファイルサイズ、UTC取得日時をマニフェストへ記録する
- 保存済みファイルのSHA-256が一致する場合は再取得しない
- `--force`で強制再取得できる
- 不正な`document_id`とファイル名によるパストラバーサルを拒否する
- HTTP、ネットワーク、ローカル保存エラーを区別して表示する

2026-11-02時点で、`data/manuals.json`に定義された3件のCisco Catalyst 9300マニュアルを取得済み。

### Phase 1: Extraction

`scripts/extract.py`を実装した。

- 入力PDFの存在と拡張子を検証する
- `marker-pdf`パッケージの実CLIである`marker_single`を呼び出す
- `shell=True`を使わず、引数リストでsubprocessを実行する
- 非空の出力ディレクトリを拒否する
- 終了コードと標準エラーを検証する
- 生成された非空のMarkdownファイルを検証して返す
- `--command`で実行ファイル名を上書きできる
- `--disable-ocr`でPDFのテキストレイヤーだけを使用できる

実行例:

```bash
uv run python scripts/extract.py input/manual.pdf --output-dir output/raw
```

取得済みのInterface and Hardware Components Configuration Guideを使い、実PDF抽出を実施した。

```bash
uv run python scripts/extract.py \
  data/products/catalyst9300/raw/c9300_iosxe26_int_hw_cg/c9300_iosxe26_int_hw_cg.pdf \
  --output-dir data/products/catalyst9300/extracted/c9300_iosxe26_int_hw_cg \
  --disable-ocr
```

出力結果:

- Markdown: 1件、531,188 bytes、6,292行
- JPEG: 77件（Markdownからの参照漏れ・不足なし）
- MarkerメタデータJSON: 1件、有効なJSON
- 出力全体: 1,849,419 bytes
- 最終ページ相当の節まで出力され、途中切れなし
- Marker標準の出力階層は`<output-dir>/<PDF stem>/<PDF stem>.md`

実出力:

```text
data/products/catalyst9300/extracted/c9300_iosxe26_int_hw_cg/
└── c9300_iosxe26_int_hw_cg/
    ├── c9300_iosxe26_int_hw_cg.md
    ├── c9300_iosxe26_int_hw_cg_meta.json
    └── _page_*_*.jpeg
```

既知の品質差:

- 本文と見出しは抽出できている
- 一部の表で列構造が崩れている
- 一部に単語間スペースの欠落がある
- CLI設定例はコードブロック化されていない
- Warning、Caution、NoteはGitHub Alerts化されていない
- OCRを無効化したため、画像内文字とPDFテキストから復元できない表は欠落する可能性がある

### Cloud Vision OCR

Google Vision互換の画像OCR／PDF OCRを呼び出す`scripts/cloud_vision.py`を実装した。

- `.env`から画像用エンドポイント、PDF用エンドポイント、APIキーを取得する
- 認証には`api-key`ヘッダーを使用する
- `DOCUMENT_TEXT_DETECTION`で座標付きOCR結果を取得する
- PDFは1始まりのページ番号を1回最大5ページまで指定できる
- HTTPエラー、APIエラー、不正JSON、期待しないレスポンス構造を拒否する
- 入力SHA-256、ファイルサイズ、指定ページ、生の座標付きレスポンスをJSON保存する
- 一時ファイルから原子的に配置する
- 既存出力はAPI呼び出し前に拒否し、`--force`指定時だけ上書きする
- APIキーは出力JSONやログへ保存しない

実APIによるPoC結果:

- 画像OCR: HTTP 200、ネットワーク図から110文字、座標付き1ページを取得
- PDF OCR: HTTP 200、指定した1ページから2,276文字、座標付き1ページを取得

OCR候補をAPI呼び出しなしで選定する`scripts/ocr_candidates.py`も実装した。

- PDFテキストレイヤーの非空白文字数をページ単位で計測する
- 既定では50文字未満のページをPDF OCR候補にする
- Marker画像名から元ページ、画像種別、寸法を取得する
- 既定では10万画素以上の`Diagram`画像を画像OCR候補にする
- 入力PDFのSHA-256と選定条件を候補マニフェストへ記録する
- `scripts/ocr_batch.py`は候補マニフェストの画像を順番にOCRし、画像ごとのJSON結果を保存する
- 入力パス、サイズ、SHA-256が一致する既存結果はスキップするため、中断後も安全に再開できる
- 既存結果が候補画像と一致しない、または不正なJSONの場合は、誤った再利用を避けて明示的に停止する
- `scripts/ocr_integrate.py`は候補画像のMarkdown参照直後に、折りたたみ表示のOCRテキストを追加した別Markdownを生成する
- 統合前にOCR JSONと候補画像のパス、サイズ、SHA-256を検証し、座標付きの生JSONは変更せず保持する
- Marker標準の`data/products/catalyst9300/extracted/<document_id>/<PDF stem>/`という二重ディレクトリ構成を維持する方針を決定した

実マニュアルの選定結果:

- PDF総ページ数: 198
- PDF OCR候補: 0ページ（全ページに98文字以上のテキストレイヤーあり）
- 画像OCR候補: 3件（PDFページ24、72、73相当のネットワーク図）
- 画像候補1件は新CLIでOCRし、`data/products/catalyst9300/ocr/<document_id>/`へ保存済み

### Phase 2: Chunking

`scripts/chunk_markdown.py`を実装した。

- 4文字を1トークンとみなすモデル非依存の概算で、既定の上限を6,000トークンにする
- 見出し境界を優先して連続する節をチャンクへまとめる
- 上限を超える節は、見出し階層を各チャンクへ引き継いで段落境界から分割する
- 入力MarkdownのSHA-256、推定トークン数、見出し階層を`manifest.json`へ記録する
- 各チャンクは`chunk-0001.md`形式で保存し、既存出力ディレクトリは上書きしない
- 実マニュアル（531,188 bytes、406見出し）で25チャンクを生成し、最大推定値は5,994トークンだった

### Phase 3: Transformation

`scripts/transform_markdown.py`を実装した。

- OpenAI互換のChat Completions APIを使用し、プロバイダー固有SDKに依存しない
- `.env`または環境変数からAPI URL、APIキー、モデル、タイムアウトを読み込む
- 日本語のシステムプロンプトで、英語本文の保持、CLIコードブロック化、GitHub Alerts化、メタデータ抽出を指定する
- LLM応答をJSON、Markdown本文、コードフェンス、メタデータの型まで検証する
- JSON互換のYAMLフロントマターを出力へ付与する
- 入力チャンクのSHA-256、出力SHA-256、抽出メタデータを変換マニフェストへ記録する
- 一時ディレクトリから原子的に配置し、既存出力ディレクトリを上書きしない
- 応答不正や途中失敗時は出力先を作成せず、明示的なエラーで停止する
- モデルをURLで指定する社内Cloud GPT向けに`CLOUD_GPT_API_URL`、`CLOUD_GPT_API_KEY`、`api-key`認証へ対応する
- `--resume`でチャンクごとに原子的に保存し、SHA-256が一致する完了済み結果を安全に再利用する
- 画像参照、Markdownリンク、Markerアンカーを保護トークン化し、LLM応答での完全保持を検証して復元する
- チャンク別メタデータの最頻値（同数なら先出）を文書単位メタデータへ統合する

GPT-5.1実APIによるPoC結果:

- 実マニュアルの先頭チャンク（推定5,864トークン）を正常に変換
- JSON応答、YAMLフロントマター、変換マニフェストの生成に成功
- 本文文字数比は入力比1.002で、見出し17件と画像参照6件をすべて保持
- Note、Caution、WarningをGitHub Alertsへ変換
- 出力のコードフェンス、メタデータ型、SHA-256検証に成功

全25チャンクの実API変換結果:

- API接続切断時に13件のチェックポイントから再開し、全25件を完了
- 保護対象756件を完全一致で保持
- Markerアンカー270件、画像参照77件、Markdownリンク317件をすべて保持
- GitHub Alerts 53件、コードブロック205件を生成
- 文書単位メタデータのtitle、description、product、version、sourceを統合
- 旧方式の出力は比較用に`data/products/catalyst9300/transformed/c9300_iosxe26_int_hw_cg-unprotected/`へ保持

### Phase 4: Packaging

`scripts/package_document.py`を実装した。

- 変換完了マニフェストと全チャンクのSHA-256を検証する
- チャンク別フロントマターを除去し、文書単位フロントマター付きの単一Markdownへ結合する
- Markdown画像参照を`images/`配下へ書き換え、参照画像だけをコピーする
- パストラバーサル、外部画像URL、欠落画像を拒否する
- `data/manuals.json`の取得元URL、PDFチェックサム、サイズ、取得日時をパッケージメタデータへ記録する
- Markdownと各画像を個別resourceとして登録し、サイズとSHA-256を付与する
- Frictionlessライブラリで検証してから、一時ディレクトリを出力先へ原子的に配置する
- 既存出力ディレクトリは上書きしない

実マニュアルのパッケージ生成結果:

- `data/products/catalyst9300/packages/c9300_iosxe26_int_hw_cg/`へ生成
- `datapackage.json`、507,329 bytesの`manual.md`、画像77件を収録
- resourcesはMarkdown 1件と画像77件の合計78件
- 画像参照77件と同梱画像77件が完全一致
- 内部リンク388件はすべて対応する270アンカーのいずれかを参照
- Frictionlessによる再読込・独立検証に成功

残り2文書の処理結果:

- IP Addressing Services Configuration Guide
  - Cloud Vision画像OCR 20件
  - GPT-5.1変換 44チャンク
  - `manual.md` 700,656 bytes、画像90件、resources 91件
  - 内部リンク481件と画像参照90件を検証
- System Management Configuration Guide
  - Cloud Vision画像OCR 3件
  - GPT-5.1変換 55チャンク
  - `manual.md` 1,094,172 bytes、画像104件、resources 105件
  - 内部リンク555件と画像参照104件を検証
- 3パッケージすべてをFrictionlessで再読込し、有効であることを確認
- Cloud VisionとGPT-5.1の接続切断・タイムアウト時は、保存済みチェックポイントから再開して完了
- 大量resourceパッケージのディレクトリrenameが拒否される環境向けに、安全なコピー配置フォールバックを追加

### RAGによるコンフィグ生成評価

`scripts/rag.py`と`scripts/evaluate_rag.py`を実装した。

- Frictionless検証済み3パッケージを見出し単位、最大4,000文字へ再分割する
- `intfloat/multilingual-e5-small`による384次元のローカル多言語埋め込みを使用する
- Qdrantローカル永続ストレージへ1,787節を索引化する
- GPT-5.1で日本語要件を英語検索文3件と候補CLI句へ展開する
- ベクトル検索とBM25字句検索をRRF統合し、実在する候補CLI句を含む節を優先する
- 表紙と目次を検索対象から除外し、文書タイトルの重複による検索ノイズを抑制する
- GPT-5.1は取得根拠だけを使用し、設定、説明、引用ID、警告をJSONで返す
- 引用IDが取得結果に含まれることを検証し、根拠外の引用を拒否する
- 空コンフィグは安全な棄権として、警告がある場合だけ許可する
- API一時障害を3回まで再試行し、評価はケース単位で再開できる
- 評価ケースとインデックスのSHA-256をレポートへ記録する

代表10ケースの結果:

- 単一ベクトル検索の初期値は上位6件で3/10
- CLI-aware Hybrid Search導入後の検索期待語は10/10
- 必須CLIと禁止CLIの静的検証は10/10
- 根拠引用は10/10
- ルーテッドポート、description、access VLAN、DHCP relay/server、IPv6、NTP、Syslog、SNMP、shutdownを評価
- 実機投入評価は未実施であり、現在設定との差分、依存機能、到達性、ロールバックは評価対象外

RAG構築中に、IP AddressingとSystem Managementの文書タイトルが章タイトルへ誤統合されていた問題を検出した。
Phase 3の文書メタデータ統合を最頻値から先頭の非空値へ修正し、2パッケージを再生成・再検証した。

### Catalyst 9500異機種混在評価

Catalyst 9500 / IOS XE 26.x.xの公式ガイド一覧をDiscoveryし、15文書を検出した。
パイロットとして次の4文書を、隔離した`data/products/catalyst9500/`で
DownloadからPackagingまで実行した。

- High Availability Configuration Guide
- Interface and Hardware Components Configuration Guide
- IP Addressing Services Configuration Guide
- System Management Configuration Guide

処理結果:

- 4文書、合計152チャンクをGPT変換
- Cloud Visionは呼び出さず、4文書ともOCRフェーズを安全にスキップ
- `manual.md`は373,266、438,687、843,189、1,151,057 bytes
- Data Package resourcesは57、56、101、105件
- 4パッケージのFrictionless構造、チェックサム、画像参照、内部リンクを検証
- カタログの正規化済み製品・リリースを
  `configbrain.catalogProduct`と`configbrain.release`へ保存

その後、同じ隔離環境と状態DBを使って残り11文書を再開処理し、
Catalyst 9500カタログ全15文書のPackagingまで完了した。

- 全15文書、合計956チャンクをGPT変換
- Cloud Visionは呼び出さず、全15文書のOCRフェーズを安全にスキップ
- `manual.md`合計17,594,408 bytes
- Data Package resources合計2,104件
- 状態DBは`completed` 75フェーズ、`skipped` 15フェーズ
- 全15パッケージのFrictionless構造、リソースチェックサム、
  画像参照、内部リンクを横断検証
- 成果物全体は約320 MB
- Layer 2 Guideは保護トークン不一致で一度停止したが、
  保存済みチェックポイントから再試行して完了
- 再試行開始時のHTTP 429は待機後に解消し、保存済みチャンクを再利用

Catalyst 9300の共通3文書とCatalyst 9500の4文書、合計7パッケージを
`data/trials/mixed-rag/`へまとめ、3,957節の混在Qdrant索引を作成した。

混在時の誤参照を防ぐため、次を実装した。

- Qdrant payloadへ`product`と`release`を保存
- ベクトル検索とBM25字句検索の両方へ完全一致フィルターを適用
- `rag.py generate`で`--product`を必須化し、`--release`を追加
- 生成直前に全取得根拠が対象製品・リリースだけであることを再検証
- 評価ケースへ`product`と`release`を追加
- 評価レポートへ`product_isolation_pass`を追加

混在評価6ケースの結果:

- 検索期待語: 6/6
- 必須CLI・禁止CLI: 6/6
- 根拠引用: 6/6
- 製品・リリース分離: 6/6
- LLM事前知識禁止: `disallow`
- DHCP relayとNTPを9300/9500それぞれで評価
- Catalyst 9500のルーテッドポートとStackWise Virtual DADリンクを評価

この結果は静的な検索・コンフィグ・引用・製品分離評価であり、実機投入時の
構文受理、機能動作、現在設定との差分、ロールバックは未評価。

### 製品別データ統合

Catalyst 9200、Catalyst 9300、Catalyst 9500の全フェーズ成果物を、次の製品別ルートへ統合した。

- `data/products/catalyst9200/`
- `data/products/catalyst9300/`
- `data/products/catalyst9500/`

各製品ルートは`raw`、`extracted`、`ocr`、`processed`、`chunks`、
`transformed`、`packages`と`state.sqlite3`を保持する。
共有ファイルシステムがディレクトリrenameを拒否したため、内容一致確認済みの
コピーを配置し、両製品の全パッケージを検証してから旧配置を削除した。
混在RAGのパッケージ参照用シンボリックリンクも新配置へ更新した。

`pipeline.py`へ`--data-root`を追加し、Catalyst 9300を既定値とした。
別製品の`plan`、`run`、`retry`は`--data-root data/products/<product>`で
実行できる。`status`は誤った製品を暗黙に表示しないよう、
`--product`または`--state-db`の明示を必須とした。

### 製品別Qdrantコレクション統合

Catalyst 9200の13パッケージ、Catalyst 9300の18パッケージ、
Catalyst 9500の15パッケージを、
`data/qdrant`内の製品別コレクションへ格納した。

- `cisco_manual_sections_catalyst9200`: 6,792節
- `cisco_manual_sections_catalyst9300`: 13,220節
- `cisco_manual_sections_catalyst9500`: 12,363節
- 合計: 46文書、32,375節
- 製品・リリースpayload欠落: 0件

増分索引のマニフェストへ文書ごとの正規化済み製品IDを保存し、
入力された製品のコレクションだけを更新するよう変更した。
検索時は`--product`からコレクションを選択し、製品フィルターには
`configbrain.catalogProduct`の正規化値だけを使用する。
旧共有コレクションから既存ベクトルを再計算せずに分割できる
`rag.py migrate-collections`も追加した。

### Catalyst 9200通し品質確認

Catalyst 9200 / IOS XE 26.x.xの公式ガイド一覧をDiscoveryし、13文書を検出した。
製品別の`data/products/catalyst9200/`でDownloadからPackagingまで一括実行した。

- 13文書、458チャンクをGPT変換
- Cloud Visionは呼び出さず、13文書ともOCRフェーズを安全にスキップ
- 状態DBは`completed` 65フェーズ、`skipped` 13フェーズ、失敗0
- `manual.md`合計9,175,793 bytes
- Data Package resources合計1,249件（Markdown 13件、画像1,236件）
- 内部リンク2,495件、画像参照1,236件を含む全13パッケージを横断検証
- Frictionless構造、リソースチェックサム、画像参照、内部リンクを検証
- 成果物全体は159 MB

検証済み13パッケージを製品別Qdrantコレクションへ増分索引化し、
6,792節を登録した。DHCP relay、NTP、スタッキングの検索スモークテストでは、
各10件の結果がすべてCatalyst 9200 / 26.xに限定され、期待するCLIまたは
機能語を取得した。

### 小規模オーケストレーション試行

Catalyst 9300の公式Installation and Configuration GuidesページをDiscoveryした結果、15文書を検出した。実ページはPDF直リンクではなくCisco HTMLガイドを列挙していたため、DiscoveryはCisco公式HTMLガイドURLを同一パスのPDF URLへ正規化するよう拡張した。

Cisco DNA Service for Bonjour Configuration Guide 1件を、試行専用の`data/trials/c9300-smoke/`へ実行した。

- Download、Extraction、OCRスキップ、Chunkingを完了
- `marker-pdf`は`--disable-marker-ocr`で実行
- Cloud Visionは`--skip-cloud-vision`で呼び出さず、抽出MarkdownをそのままChunkingへ引き継いだ
- 処理済みMarkdown: 296,676 bytes
- 生成チャンク: 15件
- 試行成果物全体: 9.4 MB
- LLM Transformation、Packaging、RAG更新は未実行

### テスト

以下を実装済み。

- `tests/test_download_manuals.py`
- `tests/test_extract.py`
- `tests/test_chunk_markdown.py`

最終確認結果:

```text
89 passed
```

実行コマンド:

```bash
uv run pytest
```

## 調査・解決した問題

### `marker-pdf`のCLI名

パッケージ名は`marker-pdf`だが、現在のバージョンが提供する単一ファイル用CLIは`marker_single`だった。実装のデフォルトを`marker_single`に修正した。

### pytestのimportエラー

pytestから`scripts`をimportできなかったため、次を追加した。

- `scripts/__init__.py`
- pytest設定の`pythonpath = ["."]`

### Cisco PDF取得時のプロキシとTLS

環境に次のHTTPSプロキシ変数が設定されている場合、Menlo Security経由となりPythonで証明書検証エラーが発生した。

```bash
https_proxy
HTTPS_PROXY
```

Cisco PDFを取得する際は、現在の環境では次のように解除する必要がある。

```bash
unset https_proxy HTTPS_PROXY
uv run python scripts/download_manuals.py
```

現在は`.envrc`で大文字・小文字のHTTP(S)プロキシ変数と`ALL_PROXY`を解除し、外部HTTPS通信を直接接続へ統一している。

### Cisco CDNのUser-Agent判定

直接接続後も、独自User-AgentはCisco CDNからHTTP 403を返された。

```text
configbrain2-manual-downloader/0.1 -> HTTP 403
Wget/1.21.4                      -> HTTP 200
```

そのため、取得スクリプトではCiscoとの互換性が確認できた`Wget/1.21.4`を使用している。

### marker-pdfのモデル取得と社内TLS検査CA

`marker_single`の初回実行では、フォントとHugging Faceモデルを取得する。現在の環境では、TLS検査CA(Menlo)がLinuxのCAバンドルに含まれず、証明書検証に失敗した。

Windowsの信頼済みルートから同CAをエクスポートし、OSのCAバンドルと結合した`certs/local-ca-bundle.pem`を次の両方へ指定すると、TLS検証を無効化せず取得できた。設定は`.envrc`で標準化している。

```text
REQUESTS_CA_BUNDLE=<project root>/certs/local-ca-bundle.pem
SSL_CERT_FILE=<project root>/certs/local-ca-bundle.pem
```

Hugging Face Xet経由の取得も失敗したため、初回モデル取得時は次を指定した。

```text
HF_HUB_DISABLE_XET=1
```

`datalab-to/surya_layout2`（142 MB）はローカルキャッシュへ取得済み。

### CPU環境でのOCR依存関係

現在の環境ではCUDAが使用できない。SuryaのCPU OCRバックエンドは`llama-server`を必要とするが未導入のため、OCR有効時は表OCRフォールバックで失敗した。

今回のPDFはテキストレイヤーを持つため、`--disable-ocr`を追加して実抽出を完了した。OCRを本番利用する場合は、`llama.cpp`の導入とGGUFモデルの容量・CPU性能を別途検証する必要がある。

## 現在の主要ファイル

```text
configbrain/
├── data/
│   ├── manuals.json
│   ├── raw/<document_id>/<filename>.pdf
│   ├── extracted/<document_id>/<PDF stem>/
│   │   ├── <PDF stem>.md
│   │   ├── <PDF stem>_meta.json
│   │   └── _page_*_*.jpeg
│   ├── ocr/<document_id>/
│   │   ├── candidates.json
│   │   └── <OCR result>.json
│   └── chunks/<document_id>/
│       ├── chunk-0001.md
│       └── manifest.json
├── scripts/
│   ├── __init__.py
│   ├── api.py
│   ├── chunk_markdown.py
│   ├── cloud_vision.py
│   ├── config_generation_jobs.py
│   ├── config_generation_service.py
│   ├── download_manuals.py
│   ├── evaluate_api.py
│   ├── extract.py
│   ├── ocr_batch.py
│   ├── ocr_candidates.py
│   ├── ocr_integrate.py
│   ├── package_document.py
│   ├── rag.py
│   ├── evaluate_rag.py
│   └── transform_markdown.py
├── tests/
│   ├── test_api.py
│   ├── test_chunk_markdown.py
│   ├── test_cloud_vision.py
│   ├── test_config_generation_jobs.py
│   ├── test_download_manuals.py
│   ├── test_evaluate_api.py
│   ├── test_extract.py
│   ├── test_ocr_batch.py
│   ├── test_ocr_candidates.py
│   ├── test_ocr_integrate.py
│   ├── test_package_document.py
│   ├── test_rag.py
│   └── test_transform_markdown.py
├── instruction.md
├── pyproject.toml
├── README.md
└── PROGRESS.md
```

## 次回の開始地点

マニュアルの自動取得・変換・索引化、RAG再現性評価、コンフィグ生成サービス層、同期API、永続化された非同期ジョブ、API運用保護、CLI同等性評価は完了した。個人試験9件のフィードバックを基に生成ガードとレビュー訂正機能を実装したため、APIを再起動して修正版でVTP、interface-range、VLAN名の3ケースを再評価する。

2026-11-14に、パイプラインの完全自動化を先に拡張するのではなく、APIの縦切りMVPを優先する方針を決定した。実利用で明らかになった入力形式、応答時間、引用情報、エラー表現の課題を、パイプラインとRAGへフィードバックする。

完了したオーケストレーションの初期スコープ:

- 製品: Catalyst 9300
- リリース: CLIで指定したIOS XEリリースに固定
- 探索元: Cisco公式リリースページ
- 実行方式: 手動CLI起動、文書・フェーズ単位で再開可能
- 既存の各フェーズCLIは独立利用可能なまま維持

実装ロードマップ:

1. `scripts/discover_manuals.py`（完了）
   - Cisco公式リリースページからPDFリンク、タイトル、カテゴリを抽出する
   - `www.cisco.com`以外、対象リリース外、非PDFリンクを拒否する
   - URL正規化、重複排除、安定した`document_id`生成を行う
   - `data/catalog/<product>-<release>.json`へ原子的に保存する
   - 前回カタログとの差分（新規、更新、削除）を記録する
2. SQLite状態管理と`pipeline.py plan/status`（完了）
   - カタログ文書と6フェーズの状態をSQLiteで管理
   - 入出力SHA-256、フィンガープリント、試行回数、エラー、実行時刻の列を確保
   - `plan`でカタログを同期し、カタログ差分とGPT変換呼び出し見積りを表示
   - 文書のカタログ変更は`stale`、カタログ削除は`skipped`へ遷移
   - `status`で全フェーズの永続状態を表示
3. `pipeline.py run`（完了）
   - Download、Extraction、OCR、Chunking、Transformation、Packagingを文書・フェーズ単位で実行
   - `--document-id`、`--from`、`--through`、`--resume`を提供
   - 失敗は文書・フェーズ単位でSQLiteへ記録し、他文書の処理を継続
   - OCR候補が0件の場合は抽出MarkdownをコピーしてOCRフェーズを安全に`skipped`とする
4. 差分無効化（完了）
   - PDF変更はExtraction以降、OCR条件変更はOCR以降、チャンク条件変更はChunking以降を`stale`にする
   - モデル・プロンプト変更はTransformation以降、パッケージ仕様変更はPackaging以降を再実行する
5. 再試行・失敗隔離（完了）
   - HTTP 429/5xx、タイムアウト、DNS一時障害、接続切断を指数バックオフで再試行する
   - チェックサム不一致、不正JSON、保護トークン欠落、画像欠落、Frictionless検証失敗は自動再試行しない
   - 1文書の失敗で他文書の処理を止めない
   - `pipeline.py retry --failed`で失敗文書だけを完了済みフェーズから再開する
6. パッケージ横断検証とRAG増分更新（完了）
   - 完了パッケージのresource、画像参照、内部リンクを一括検証する
   - 変更文書だけをQdrantへ再索引化する
7. Cisco公式マニュアル横断検索CLI（完了）
   - Qdrant索引を、コンフィグ生成だけでなくマニュアル探索にも利用する
   - マニュアル名や章を事前に知らなくても、自然文から15文書を意味検索できるようにする
   - `scripts/rag.py search "<質問>" --qdrant-path data/qdrant --limit 10`形式で提供する
   - 検索結果ごとに類似度、文書名、章・節、該当本文、製品、IOS XEリリース、出典URLを表示する
   - 製品、リリース、文書などのメタデータによる絞り込みを可能にする
   - LLMによる回答生成を伴わない検索専用モードとし、公式マニュアルの原文を直接確認できるようにする
   - 意味検索だけでは不足する固有コマンドやエラー文字列に備え、キーワード検索を併用する
   - 同一箇所の重複結果を抑えつつ、前提条件、設定手順、制約事項など関連する複数箇所を横断的に提示する

### 次フェーズ: コンフィグ生成API

初期方針:

- 最初は社内・検証用途に限定し、インターネットへ直接公開しない
- APIは既存RAG処理を再実装せず、共通のPythonサービス層をCLIと共有する
- 生成処理は非同期ジョブとして受け付け、`202 Accepted`と`job_id`を返す
- 製品、IOS XEリリース、要件、知識ポリシーを明示的な入力にする
- コンフィグだけでなく、引用、警告、不足値、未対応要件、処理状態を返す
- 既存CLIとパイプラインは独立利用可能なまま維持する

段階的実装ロードマップ:

1. サービス層の切り出し（完了）
   - `scripts/rag.py`のコンフィグ生成処理を、CLIとAPIの両方から呼べるPython APIへ分離する
   - CLIの既存入出力と評価結果を変更しない
   - 既存RAGテストがすべて成功することを完了条件とする
   - `ConfigurationGenerationRequest`で要件、製品、リリース、知識ポリシー、検索件数を受け取る
   - `ConfigurationGenerationService`で検索計画、根拠検索、根拠付き生成を一つのユースケースとして実行する
   - `ConfigurationGenerationOutcome`で生成結果と評価用の検索根拠を返す
   - 検索・生成関数を依存注入し、サービス層をCLIやWebフレームワークから独立させる
   - `scripts/rag.py generate`と`scripts/evaluate_rag.py`を同じサービス層へ移行した
   - サービス層テスト6件、既存RAGを含む対象テスト46件、全テスト123件が成功した
2. 同期APIによる最小疎通（完了）
   - `POST /v1/config-generations`で入力検証から生成結果返却までを通す
   - 製品、リリース、要件、`knowledge_policy`を受け付ける
   - 設定、引用、警告、不足値、未対応要件を構造化JSONで返す
   - この段階の同期処理はAPI契約確認用とし、外部公開しない
   - FastAPIアプリを`scripts/api.py`へ実装し、Uvicornから起動可能にした
   - `requirement`、`product`、任意の`release`、`knowledge_policy`、`top_k`を厳格に検証する
   - 未定義フィールドと空文字列をHTTP 422で拒否する
   - サービス層の入力エラーをHTTP 400、RAG実行エラーをHTTP 500で明示する
   - アプリ生成時にサービス層を注入でき、テストではモデルや外部APIを呼び出さない
   - API・サービス層・既存RAGの対象テスト49件が警告なしで成功した
   - 全テスト126件が成功し、Uvicorn実起動後にOpenAPIのHTTP 200応答を確認した
3. 非同期ジョブ化（完了）
   - `POST /v1/config-generations`は`202 Accepted`と`job_id`を返す
   - `GET /v1/config-generations/{job_id}`で`queued`、`running`、`completed`、`failed`を確認可能にする
   - ジョブ状態と結果を永続化し、プロセス再起動後も参照可能にする
   - 同一リクエストの重複実行を防ぐため、冪等性キーを扱えるようにする
   - ジョブ入力、SHA-256、状態、結果、エラー、実行時刻を`data/api/jobs.sqlite3`へ保存する
   - 単一バックグラウンドワーカーでローカルQdrantと埋め込みモデルへの同時アクセスを避ける
   - 任意の`Idempotency-Key`で同一入力の既存ジョブを返し、異なる入力への再利用をHTTP 409で拒否する
   - 再起動時に`queued`ジョブを再投入し、中断された`running`ジョブを理由付きの`failed`へ遷移する
   - ジョブ失敗をHTTP受付成功と混同せず、GET結果の`status`と`error`へ永続化する
   - 非同期受付、完了、失敗、冪等性、再起動後参照、再起動回復を含む対象テスト53件が成功した
   - 全テスト130件が成功し、Uvicorn実起動後にPOSTのHTTP 202契約とGETのHTTP 200契約を確認した
4. 運用保護（完了）
   - 認証、リクエストサイズ上限、タイムアウト、同時実行数制限を追加する
   - 内部エラーと利用者入力エラーを区別し、成功形式へ偽装せず返す
   - APIキー、マニュアル本文、生成結果を不要にログへ残さない
   - リクエストID、処理時間、使用製品・リリース、終了状態を監査可能にする
   - `.env`または環境変数の`CONFIGBRAIN_API_KEY`を必須とし、`X-API-Key`を定数時間比較する
   - 未設定時はサーバー起動を拒否し、不正なキーはHTTP 401で拒否する
   - 本文64 KiB、`top_k` 20、未完了ジョブ20件、実行300秒、単一ワーカーへ制限する
   - 本文超過はHTTP 413、容量超過は`Retry-After`付きHTTP 429で返す
   - タイムアウトを`failed`、`error_code=timeout`として永続化し、完了まで次ジョブを開始しない
   - `X-Request-ID`を検証または自動生成し、レスポンス、ジョブDB、監査ログへ関連付ける
   - ジョブDBへ`request_id`、`duration_ms`、`error_code`を後方互換マイグレーションで追加する
   - 監査ログはジョブID、リクエストID、製品、リリース、状態、処理時間、エラー分類に限定する
   - APIキー、要件本文、マニュアル本文、生成結果を監査ログへ記録しない
   - 認証、本文上限、容量、タイムアウト、監査、DB移行を含む運用保護テスト14件が成功した
   - 全テスト137件が成功し、実サーバーで未認証401、認証済み404、本文超過413、リクエストID応答を確認した
5. CLI同等性評価（完了）
   - 既存の難化ケース10件をAPI経由で各3回実行する
   - CLI評価と同じ設定、引用、拒否判定になることを確認する
   - 全30回成功、実行エラー0、正規化後設定の3回一致を完了条件とする
   - `scripts/evaluate_api.py`で202受付、状態ポーリング、ケース単位保存、条件一致時の再開を実装した
   - 合格済み`reproducibility-v4`の各3結果から要件、検索条件、必須・禁止CLI、拒否判断を復元する
   - CLI基準との設定一致、検索計画、引用整合、製品・リリース分離、拒否判断、根拠外推測を評価する
   - 説明・警告・引用順の表現差と、安全な空設定を維持した不足値／未対応分類の差を許容する
   - API経由30回すべてで全評価項目が合格し、全10ケースの正規化済み設定が3回一致した
   - 実行エラーと失敗ジョブは0件、`inferred_commands`は全30回で空だった
   - ジョブ処理時間は最小36,086 ms、最大89,327 ms、平均53,930 msだった
   - 結果を`data/evaluation/api-equivalence-v1/`へ30件と`summary.json`として保存した
   - API評価スクリプトテスト5件と全テスト142件が成功した
6. 試験提供とフィードバック（個人試験・改善中）
   - 社内の限定利用者3〜5名へ2週間提供し、レビュー済みジョブ30件以上を収集する
   - 実運用要件、期待コンフィグ、修正コンフィグを匿名化して記録する
   - `templates/pilot_feedback.csv`へ1レビュー1行のCSVテンプレートを追加した
   - 判定は`ready`、`minor_changes`、`major_changes`、`unusable`の4段階とする
   - 問題を`api`、`retrieval`、`generation`、`requirement`、`corpus`、`pipeline`へ分類する
   - 引用の有用性、要件分類の妥当性、危険または破壊的な誤り、匿名化状態も記録する
   - `scripts/report_pilot.py`でCSV記載ジョブをジョブDBと照合し、JSONとMarkdownへ原子的に集計する
   - 集計には成功率、処理時間p50/p95、製品・リリース分離、引用整合、根拠外CLI、採用判定、問題分類を含める
   - 要件本文、期待・修正コンフィグ、コメント、レビュー担当者は集計結果へ含めない
   - 不正CSV、重複`feedback_id`、不正日時、不正UUID、未知の分類、存在しない`job_id`は明示的に拒否する
   - 危険または破壊的な誤りが1件でもあれば`safety_stop`として試験を停止する
   - 合格条件は、レビュー済み30ジョブ以上、ジョブ成功率95%以上、製品分離・引用整合・根拠外CLI違反0件、危険設定0件とする
   - 品質条件は`ready`または`minor_changes`が80%以上、引用有用性90%以上、要件分類妥当性90%以上、全要件匿名化とする
   - `--require-goals`指定時は合格条件未達を終了コード2で通知し、入力不正は終了コード1とする
   - 実施前に利用者へ実機投入禁止、レビュー項目、匿名化、危険設定発見時の停止・連絡手順を共有する
   - 試験終了後、API固有の問題とRAG・コーパス・パイプライン側の問題を分離し、件数と重大度で次の改善を決める
   - 集計テスト4件で合格ケース、安全停止、不正分類、存在しないジョブ、機密本文非出力を確認した
   - ヘッダーのみのテンプレートから0件レポートを安全に生成でき、全146テストが成功した
   - FastAPIと同一オリジンで動作する試験用Web UIを追加した
   - Web UIから生成条件を入力し、非同期ジョブの状態を自動確認して結果をレビューできる
   - 製品をCatalyst 9200、9300、9500のドロップダウンから選択できる
   - 選択製品に応じ、索引化済みのIOS XEリリースだけを連動ドロップダウンへ表示する
   - 現在は3製品ともIOS XE 26.xに対応する
   - 索引マニフェストとカタログの共通文書から選択肢を生成し、未索引の組み合わせを除外する
   - `GET /v1/configuration-targets`で有効な組み合わせを返し、POSTでも不正な組み合わせをHTTP 400で拒否する
   - `templates/requirement_templates.tsv`へ代表要件をUTF-8 TSVで追加した
   - TSVは`表示名<TAB>要件`の1行1件とし、空行と`#`コメントを許可する
   - 認証付き`GET /v1/requirement-templates`でファイルを要求ごとに読み、サーバー再起動なしの差し替えに対応する
   - Web UIでテンプレートを選ぶと要件本文へ反映し、「再読込」で差し替え後の内容を取得できる
   - 生成完了後だけ試験フィードバックフォームを表示する
   - `POST /v1/config-generations/{job_id}/feedback`でレビューを既存の`data/evaluation/pilot/feedback.csv`へ保存する
   - CSVは既存行を保持して一時ファイルから原子的に更新し、重複`feedback_id`を拒否する
   - 保存項目は既存テンプレートと同じ13列とし、集計スクリプトとの互換性を維持する
   - コンフィグ、説明、警告、不足値、未対応要件、根拠外CLI、引用と検索根拠を項目別に表示する
   - APIキーはWeb Storageへ保存せず、ページ内メモリだけに保持する
   - HTMLはCSP、`no-referrer`、`nosniff`、`no-store`を指定し、結果表示はテキストとして扱う
   - デスクトップとモバイル幅で表示を確認し、ブラウザから生成完了まで疎通した
   - Web UI、要件テンプレート、CSVフィードバック保存を含むAPIテスト15件と全151テストが成功した
   - 個人試験9件を集計し、7件`ready`、2件`unusable`、成功率100%、`ready`または`minor_changes`率77.78%を確認した
   - `show vtp status`の設定混入、最短指定に対する一回限りのinterface-rangeマクロ、VLAN名の根拠不足警告と設定生成の矛盾を改善対象とした
   - `show`、`clear`、`debug`、`ping`、`traceroute`で始まる運用コマンドをconfigurationから除去する後処理を追加した
   - 最短指定かつマクロ再利用要求がない場合、一回限りのinterface-rangeマクロを直接の`interface range`へ正規化する
   - 根拠不足警告とCLI生成が矛盾する場合、知識ポリシーに従って安全な空設定へ拒否する
   - 静的RAG評価へinterface-range、VTP、VLAN名を追加し、評価ケースを10件から13件へ拡張した
   - 評価器で運用コマンド混入を全ケース共通の違反として判定する
   - `major_changes`または`unusable`では問題分類を、危険な誤りではコメントを必須にした
   - `PUT /v1/config-generations/{job_id}/feedback/{feedback_id}`とWeb UIの「保存内容を訂正」で既存レビューを原子的に更新できる
   - API稼働中のSQLiteロックを避けるため、`report_pilot.py --base-url`でCSV記載ジョブだけを認証付きAPIから取得できる
   - APIキーは環境変数または`.env`から読み、CLI引数とレポートへ含めない
   - 稼働中APIから実データ9件を集計し、JSONとMarkdownの生成を確認した
   - VLAN名要件で検索計画が誤った`vlan name`を使い、公式の`name vlan-name`構文節を取得できない問題を再現した
   - VLAN名要件では`name vlan-name`を決定的に検索語へ追加し、最優先で字句検索するよう修正した
   - `name vlan-name`検索で`show vlan name`の確認節が設定手順節より上位になる問題を再現した
   - 非運用CLIの検索では、`show`などの運用コマンド内だけに一致する節より設定文脈に一致する節を優先する
   - フィードバック保存後の無効ボタンが待機カーソルを表示し続ける問題を修正し、通信中だけ`aria-busy`で待機表示する
   - Web UIアセットへバージョン付きURLと`Cache-Control: no-store`を設定し、旧CSS・JavaScriptがブラウザキャッシュから再利用される問題を防止した
   - 設定例の提示要求では、実投入用configurationを空に保ち、根拠内の例を「そのまま投入不可」として説明へ表示する
   - 検索根拠へCisco公式URLを保持し、Web UIの引用から安全な新規タブリンクで開ける
   - 修正後の全160テストとWeb UI JavaScript構文検査が成功した

### Web UIによる個人試験

`.env`へ`CONFIGBRAIN_API_KEY`を設定し、APIを起動する。

```bash
uv run uvicorn scripts.api:app \
  --host 127.0.0.1 \
  --port 8000
```

ブラウザで`http://127.0.0.1:8000/`を開き、次の順で実行する。

1. APIキーを入力する。キーはブラウザへ永続保存されない。
2. 要件テンプレートを選ぶか、要件を直接入力する。ファイル差し替え後は「再読込」を押す。
3. 製品を選択して、その製品に表示されたIOS XEリリースを選択する。
4. 知識ポリシーと検索件数を確認する。
5. 「コンフィグ案を生成」を押す。
6. `受付済み`、`生成中`から`完了`または`失敗`になるまで待つ。
7. コンフィグ、説明、警告、不足値、未対応要件、根拠外CLI、引用をレビューする。
8. 表示された試験フィードバック欄を入力し、「フィードバックを保存」を押す。
9. `scripts/report_pilot.py --base-url http://127.0.0.1:8000`でCSVの途中経過を集計する。

ページを再読み込みするとAPIキーと表示中のジョブ情報は消えるが、ジョブ自体は
SQLiteへ保存されている。Web UIは生成、進捗確認、結果表示、フィードバック保存を
対象とし、集計レポートの画面表示は初期スコープに含めない。

### curlによる個人試験の実施手順

最初は1件だけ、API受付、生成完了、レビュー記録、レポート生成までを順に実行する。
生成コンフィグは実機へ投入せず、必ず人がレビューする。

#### 1. APIキーを設定する

`.env`の`CONFIGBRAIN_API_KEY`へ十分に長いランダム値を設定する。APIキーは
リポジトリへ保存せず、コマンド例の`<CONFIGBRAIN_API_KEY>`を実際の値へ置き換える。

```dotenv
CONFIGBRAIN_API_KEY=<random secret>
```

#### 2. APIを起動する

```bash
uv run uvicorn scripts.api:app \
  --host 127.0.0.1 \
  --port 8000
```

APIは試験中このターミナルで起動したままにする。インターネットへ直接公開しない。

#### 3. 試験ジョブを1件登録する

別ターミナルから次を実行する。再実行時は`Idempotency-Key`を
`personal-trial-002`のように変更する。

```bash
curl -sS -X POST http://127.0.0.1:8000/v1/config-generations \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: <CONFIGBRAIN_API_KEY>' \
  -H 'Idempotency-Key: personal-trial-001' \
  -H 'X-Request-ID: personal-trial-001' \
  -d '{
    "requirement": "Vlan100のDHCP要求を192.0.2.50へ転送してください",
    "product": "catalyst9300",
    "release": "26.x",
    "knowledge_policy": "disallow",
    "top_k": 6
  }' \
  | jq
```

実行例。

```bash
iida@FCCLS0073460:~/git/configbrain$ curl -X POST http://127.0.0.1:8000/v1/config-generations \
>   -H 'Content-Type: application/json' \
>   -H 'X-API-Key: trial' \
>   -H 'Idempotency-Key: personal-trial-001' \
>   -H 'X-Request-ID: personal-trial-001' \
>   -d '{
>     "requirement": "Vlan100のDHCP要求を192.0.2.50へ転送してください",
>     "product": "catalyst9300",
>     "release": "26.x",
>     "knowledge_policy": "disallow",
>     "top_k": 6
>   }'
{"job_id":"41c05179-a838-4e38-a8c3-4913a6f5fc38","request_id":"personal-trial-001","status":"queued","status_url":"http://127.0.0.1:8000/v1/config-generations/41c05179-a838-4e38-a8c3-4913a6f5fc38","created_at":"2026-11-14T03:21:28.342536+00:00"}
```


HTTP 202応答の`job_id`を控える。

#### 4. ジョブの完了を確認する

```bash
curl -sS http://127.0.0.1:8000/v1/config-generations/<job_id> \
  -H 'X-API-Key: <CONFIGBRAIN_API_KEY>' \
  | jq -r '
    "Status: \(.status)",
    "Duration: \(.duration_ms // "-") ms",
    "",
    "=== Configuration ===",
    (.result.configuration // "(not available)"),
    "=== Explanation ===",
    (.result.explanation // "(none)"),
    "",
    "=== Warnings ===",
    (if ((.result.warnings // []) | length) == 0
     then "(none)" else (.result.warnings[] | "- \(.)") end),
    "",
    "=== Missing values ===",
    (if ((.result.missing_required_values // []) | length) == 0
     then "(none)" else (.result.missing_required_values[] | "- \(.)") end),
    "",
    "=== Unsupported requirements ===",
    (if ((.result.unsupported_requirements // []) | length) == 0
     then "(none)" else (.result.unsupported_requirements[] | "- \(.)") end),
    "",
    "=== Inferred commands ===",
    (if ((.result.inferred_commands // []) | length) == 0
     then "(none)" else (.result.inferred_commands[] | "- \(.)") end),
    "",
    "=== Citations ===",
    (if ((.result.citations // []) | length) == 0
     then "(none)" else (.result.citations[] | "- \(.)") end),
    (if .error
     then "\n=== Error ===\n\(.error_code // "unknown"): \(.error)"
     else empty end)
  '
```

完全なJSONをインデントして確認する場合は、次の短いコマンドを使用する。

```bash
curl -sS http://127.0.0.1:8000/v1/config-generations/<job_id> \
  -H 'X-API-Key: <CONFIGBRAIN_API_KEY>' \
  | jq
```

実行例。

```bash
curl http://127.0.0.1:8000/v1/config-generations/41c05179-a838-4e38-a8c3-4913a6f5fc38 \
  -H 'X-API-Key: trial'

iida@FCCLS0073460:~/git/configbrain$ curl http://127.0.0.1:8000/v1/config-generations/41c05179-a838-4e38-a8c3-4913a6f5fc38 \
>   -H 'X-API-Key: trial'
{"job_id":"41c05179-a838-4e38-a8c3-4913a6f5fc38","request_id":"personal-trial-001","status":"completed","request":{"requirement":"Vlan100のDHCP要求を192.0.2.50へ転送してください","product":"catalyst9300","release":"26.x","knowledge_policy":"disallow","top_k":6},"result":{"requirement":"Vlan100のDHCP要求を192.0.2.50へ転送してください","knowledge_policy":"disallow","configuration":"ip dhcp relay information option vpn\ninterface Vlan100\n ip helper-address 192.0.2.50\n","explanation":"要件は「Vlan100のDHCP要求を192.0.2.50へ転送」することなので、SVIであるinterface Vlan100上にDHCPリレーの転送先(IP helper-address)を設定する必要があります。\n\n根拠E3およびE6の例では、DHCPリレーを有効化するためにグローバル設定として「ip dhcp relay information option vpn」を投入し、各Vlanインターフェース上で「ip helper-address <DHCPサーバIP>」または「ip helper-address global <DHCPサーバIP>」を設定しています。要件ではVRFやglobal指定の有無が示されていないため、E3の構文に従い、単純な「ip helper-address 192.0.2.50」を使用しました。\n\nVlan100のIPアドレスやVRF名などは要件に含まれていませんが、DHCP要求の転送先IPアドレスは指定されているため、その部分のみを設定しています。","citations":["catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321:section-0352","catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321:section-0373","catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321:section-0370"],"warnings":["Vlan100のSVIに設定するIPアドレスが要件に記載されていないため、IPアドレス設定は省略しています。","Vlan100のVRF情報が要件に記載されていないため、vrf forwardingの設定は省略しています。","DHCPリレーに関するその他のオプション（ip dhcp relay source-interface、ip dhcp compatibility suboptionなど）の要否が要件に記載されていないため、設定していません。"],"inferred_commands":[],"unsupported_requirements":[],"missing_required_values":[],"retrieval":[{"citation_id":"catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321:section-0352","score":1.3768115942028984,"document_id":"catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321","product":"catalyst9300","release":"26.x","heading_path":["How to Configure DHCP Relay in a BGP EVPN VXLAN Fabric","Configuring DHCP Relay on a VTEP","Procedure"]},{"citation_id":"c9300_iosxe26_int_hw_cg:section-0017","score":1.0,"document_id":"c9300_iosxe26_int_hw_cg","product":"catalyst9300","release":"26.x","heading_path":["Configuring Interface Characteristics","Information About Interface Characteristics","Switch Ports","Switch Virtual Interfaces"]},{"citation_id":"catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321:section-0373","score":2.375,"document_id":"catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321","product":"catalyst9300","release":"26.x","heading_path":["Configuration Examples for DHCP Relay in a BGP EVPN VXLAN Fabric","Figure 20: DHCP Relay Deployment in <sup>a</sup> BGP EVPN VXLAN Fabric","Example: DHCP Server and DHCP Clientare in the Same Tenant VRF","DHCP Configuration Snippet"]},{"citation_id":"catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321:section-0377","score":1.0441176470588234,"document_id":"catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321","product":"catalyst9300","release":"26.x","heading_path":["Configuration Examples for DHCP Relay in a BGP EVPN VXLAN Fabric","Example: DHCPClient and DHCP Server are in Different Tenant VRFs","DHCP Configuration Snippet"]},{"citation_id":"catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321:section-0823","score":1.0,"document_id":"catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321","product":"catalyst9300","release":"26.x","heading_path":["Example: Configuring TRM with PIM-SM for IPv4 and IPv6 Multicast Traffic when the RP is Inside the BGP EVPN VXLAN Fabric"]},{"citation_id":"catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321:section-0370","score":1.0,"document_id":"catalyst9300-26-x-26x-bgp-evpn-vxlan-9300-cg-0082138a0321","product":"catalyst9300","release":"26.x","heading_path":["Configuration Examples for DHCP Relay in a BGP EVPN VXLAN Fabric","Figure 20: DHCP Relay Deployment in <sup>a</sup> BGP EVPN VXLAN Fabric","Example: DHCP Server is in the Layer 3 Default VRF and the DHCP Client is in the Tenant VRF","VTEP 1"]}],"search_queries":["Vlan100のDHCP要求を192.0.2.50へ転送してください","Cisco Catalyst 9300 IOS XE 26.x configure DHCP relay for VLAN interface to forward DHCP requests to specific DHCP server","Cisco IOS XE 26.x how to configure ip helper-address on SVI for VLAN to forward DHCP requests to DHCP server","Cisco Catalyst 9300 configure DHCP relay agent on VLAN interface using ip helper-address command"],"cli_terms":["interface vlan","ip address","ip helper-address","ip dhcp relay","ip dhcp relay information","ip dhcp relay information option","ip dhcp relay information trusted","ip dhcp relay information policy","ip dhcp relay destination","ip dhcp relay destination address"]},"error_code":null,"error":null,"duration_ms":45375,"created_at":"2026-11-14T03:21:28.342536+00:00","updated_at":"2026-11-14T03:22:13.720283+00:00","started_at":"2026-11-14T03:21:28.344964+00:00","completed_at":"2026-11-14T03:22:13.720283+00:00"}
```

`queued`または`running`の場合は少し待って再実行する。`failed`の場合は
`error_code`と`error`を記録し、成功として扱わない。`completed`になったら
次の項目を確認する。

- 要件を満たすコンフィグか
- 対象製品とIOS XEリリースが正しいか
- 引用先が生成コマンドの根拠になっているか
- 不要、危険、破壊的なコマンドが含まれていないか
- 要件にない値やCLIを勝手に推測していないか
- 現在設定、依存機能、到達性、ロールバックを考慮すると修正が必要か

#### 5. フィードバックCSVを準備する

初回だけ次を実行する。

```bash
mkdir -p data/evaluation/pilot
cp templates/pilot_feedback.csv data/evaluation/pilot/feedback.csv
```

`data/evaluation/pilot/feedback.csv`へ1レビュー1行で記録する。最初の行は
次を目安にする。

- `feedback_id`: `personal-001`
- `job_id`: 手順3で取得したUUID
- `reviewer`: `reviewer-01`など匿名化したID
- `reviewed_at`: ISO 8601形式の日時
- `requirement_anonymized`: `yes`
- `verdict`: `ready`、`minor_changes`、`major_changes`、`unusable`のいずれか
- `expected_configuration`: 匿名化した期待コンフィグ
- `corrected_configuration`: 修正が必要な場合だけ匿名化して記録
- `citations_useful`: `yes`、`no`、`unknown`のいずれか
- `requirement_classification_appropriate`: `yes`、`no`、`unknown`のいずれか
- `dangerous_or_destructive_error`: `yes`または`no`
- `issue_categories`: 問題があれば分類を`;`区切りで記録
- `comments`: 匿名化した補足

顧客名、実ホスト名、実IPアドレス、認証情報などを、要件、コンフィグ、
コメントへ記録しない。

#### 6. 途中経過レポートを生成する

30件未満の間は出口基準未達が正常なので、`--require-goals`を付けずに実行する。

```bash
uv run python scripts/report_pilot.py \
  --base-url http://127.0.0.1:8000 \
  --feedback data/evaluation/pilot/feedback.csv \
  --json-output data/evaluation/pilot/report.json \
  --markdown-output data/evaluation/pilot/report.md
```

`data/evaluation/pilot/report.md`と`report.json`を確認する。
`safety_stop=true`または危険・破壊的な誤りを1件でも発見した場合は、
次の試験へ進まず原因を調査する。

#### 7. 試験ケースを増やす

1件目が正常に完了したら、DHCP、NTP、Syslog、SNMP、VLAN、IPv6などへ
広げ、最初は5〜10件を自分でレビューする。各ジョブで手順3〜6を繰り返す。
同じ要件を再評価する場合も、新しい`Idempotency-Key`を使用する。

#### 8. 30件到達後に出口基準を判定する

レビュー済みジョブが30件以上になったら、同じ集計コマンドへ
`--require-goals`を追加する。

```bash
uv run python scripts/report_pilot.py \
  --base-url http://127.0.0.1:8000 \
  --feedback data/evaluation/pilot/feedback.csv \
  --json-output data/evaluation/pilot/report.json \
  --markdown-output data/evaluation/pilot/report.md \
  --require-goals
```

終了コード0は全出口基準達成、1は入力または集計エラー、2は出口基準未達を示す。

初期MVPのAPI案:

```text
POST /v1/config-generations
GET  /v1/config-generations/{job_id}
GET  /v1/configuration-targets
GET  /v1/requirement-templates
POST /v1/config-generations/{job_id}/feedback
PUT  /v1/config-generations/{job_id}/feedback/{feedback_id}
```

初期MVPに含めないもの:

- インターネットへの一般公開
- 複数利用者向けの権限管理や課金
- 定期実行によるマニュアル探索・更新
- 実機へのコンフィグ自動投入
- APIサーバー内での重いマニュアル変換処理

API化の途中でパイプラインの重大な欠陥が判明した場合だけ、該当部分を先に修正する。スケジューラーや無人更新などの完全自動化は、API試験提供の結果から必要性を判断する。

想定する主要コマンド:

```bash
uv run python scripts/discover_manuals.py \
  --product catalyst9300 \
  --release 26.x \
  --seed-url <Cisco公式リリースページ> \
  --output data/catalog/c9300-iosxe26.json

uv run python scripts/pipeline.py plan \
  --catalog data/catalog/c9300-iosxe26.json

uv run python scripts/rag.py search \
  "DHCP relayを設定する方法" \
  --qdrant-path data/qdrant \
  --limit 10
```

検索は製品別Qdrantコレクションを横断し、意味検索とキーワード検索を統合する。
`--product`、`--release`、`--document`で完全一致のメタデータ絞り込みができ、
`--format json`では機械処理可能な結果を出力する。

```bash
uv run python scripts/rag.py search \
  "DHCP relay configuration" \
  --product catalyst9300 \
  --release 26.x \
  --format json \
  --output data/evaluation/search.json
```

```bash
uv run python scripts/pipeline.py run \
  --catalog data/catalog/c9300-iosxe26.json \
  --resume

uv run python scripts/pipeline.py status
uv run python scripts/pipeline.py retry --failed
```

初期並列数:

- Download: 2
- Marker: 1
- Cloud Vision: 2
- GPT Transformation: 1
- Packaging: 2

`data/manuals.json`は既存互換用として当面維持するが、自動化の正本は探索カタログとSQLite状態DBへ移行する。

### RAG難化評価計画

基本ケースと初回の難関ケースで確認した検索・生成・拒否動作をさらに検証するため、
次の10ケースを順番に実評価する。

1. 対応機能＋未対応機能の複合要件（合格）
   - Catalyst 9300へNTPサーバーを設定し、同時に未対応のStackWise Virtual
     Fast Hello Dual-Active-Detectionリンクを要求する
   - 期待結果: NTPだけを部分生成せず、`configuration`全体を空にする
   - 未対応の主要機能を`unsupported_requirements`と`warnings`へ記録する
   - 実評価結果: NTPの根拠取得に成功し、生成候補の
     `ntp server 192.0.2.123`を部分設定として破棄した
   - `configuration`は空、`unsupported_requirements`、警告、引用、
     製品・リリース分離はすべて期待どおり
2. 矛盾する要件（合格）
   - 同一インターフェースをアクセスポートかつトランクポートとして要求する
   - 期待結果: 一方を推測で選択せず、設定を生成しない
   - 実評価結果: accessとtrunk双方の根拠取得に成功し、矛盾する主要要件を
     `unsupported_requirements`へ記録した
   - `configuration`は空で、部分的なインターフェース設定も生成しなかった
3. 情報不足（改善後合格）
   - 送信先IPアドレスを指定せずにSyslog設定を要求する
   - 期待結果: マニュアルの例示IPを流用せず、設定を生成しない
   - 初回結果: 例示IPは流用しなかったが、`logging on`だけを部分生成した
   - 改善: 主要機能の必須値不足を`missing_required_values`へ記録し、
     関連する部分設定をすべて破棄するガードを追加した
   - 再評価結果: SyslogサーバーIP不足を検出し、`configuration`を空にした
   - 例示IPの流用、根拠外推測、部分的な`logging on`生成はいずれもなし
4. 否定表現（改善後合格）
   - 既存NTPサーバー設定の削除を要求する
   - 期待結果: `no ntp server ...`を生成し、追加用の`ntp server ...`を生成しない
   - 初回結果: 追加用コマンドは生成しなかったが、`no ntp server`の根拠を
     上位取得できず、`configuration`は空になった
   - 改善: 削除・無効化の否定表現を検索計画へ保持し、マニュアル構文の
     `[ no ] command`表記も`no command`検索で一致させた
   - 再評価結果: `no ntp server 192.0.2.123`を生成し、
     肯定形の`ntp server 192.0.2.123`は生成しなかった
5. 例示値の混入防止（改善後合格）
   - 認証なしのNTPサーバー設定を要求する
   - 期待結果: マニュアル例のVRF名、key番号、sourceなどをコピーしない
   - 初回結果: 例示値は混入しなかったが、認証付きNTP節へ検索が偏り、
     `configuration`を空にした
   - 改善: 「認証なし」などの除外条件を検索計画へ保持し、矛盾するCLI候補を
     除外するとともに、除外対象を主題とする章・節の順位を下げた
   - 再評価結果: `ntp server 192.0.2.123`だけを生成し、VRF名、key番号、
     source、prefer、version、認証コマンドは生成しなかった
6. 複数対象と優先指定（改善後合格）
   - NTPサーバーを2台設定し、一方だけを`prefer`に指定する
   - 期待結果: 2台とも欠落なく生成し、指定されたサーバーだけへ`prefer`を付与する
   - IPアドレス、`prefer`対象、引用根拠の取り違えがないことを確認する
   - 初回結果: 取り違えや例示値の混入はなかったが、`prefer`構文の根拠を
     取得できず`configuration`を空にした
   - 改善: `ntp server ... prefer`のように可変引数を挟むCLI構文を、
     最大16トークンの可変部分を許容する順序付きトークン検索で一致させた
   - 再評価結果: `ntp server 192.0.2.123`と
     `ntp server 192.0.2.124 prefer`を生成し、`prefer`対象を正しく限定した
   - VRF、key、source、version、認証キー関連の例示値は混入しなかった
7. 依存関係を持つ複合設定（合格）
   - VLAN 120を作成し、GigabitEthernet1/0/20をVLAN 120のアクセスポートにする
   - 期待結果: VLAN作成とポート割り当てを両方生成し、trunkや別VLANを混入させない
   - 設定順序と、各機能の根拠が取得されていることを確認する
   - 実評価結果: `vlan 120`を先に生成し、その後にインターフェースと
     `switchport access vlan 120`、`switchport mode access`を生成した
   - trunk、別VLAN、`no switchport`の混入はなく、各機能の根拠取得も合格した
8. 削除時の必須値不足（改善後合格）
   - 対象IPアドレスを指定せずにNTPサーバー設定の削除を要求する
   - 期待結果: マニュアルの例示IPを流用せず、`configuration`を空にする
   - 不足する対象IPを`missing_required_values`へ記録する
   - 初回結果: 例示IPを流用せず`configuration`を空にし、削除対象の
     IPアドレスまたはホスト名を`missing_required_values`へ記録した
   - `no ntp server`の厳密一致候補が字句検索上位3件の事前カットで落ち、
     `retrieval_pass`だけが不合格となった
   - 改善: コーパス全体へ順序付き一致を先に適用し、一致候補だけをBM25で順位付けした
   - `show`、`clear`、`debug`などの確認・運用コマンドを生成根拠の加点対象から除外した
   - 再評価結果: `no ntp server`の根拠取得、空設定、必須値不足記録がすべて合格した
9. 正常要件＋矛盾要件の複合（改善後合格）
   - 正常なNTP設定と、同一ポートへのaccess/trunk同時指定を要求する
   - 期待結果: NTPだけを部分生成せず、`configuration`全体を空にする
   - 矛盾要件を`unsupported_requirements`へ記録する
   - 実評価結果: NTP設定に加え、同一ポートへaccessとtrunkの両設定を生成した
   - 警告では矛盾を認識したが、`unsupported_requirements`は空のままだった
   - access側の検索根拠は取得したが、trunk側の2構文が最終根拠から欠落し、
     `retrieval_pass`も不合格となった
   - 改善: 同一インターフェースへのaccess/trunk同時指定を生成後にも決定的に検出し、
     正常部分を含む全CLIを破棄して、理由を`unsupported_requirements`へ記録する
   - 順序付き検索が文書前方の離れた同一先頭語で打ち切られる不具合を修正し、
     後続の先頭語から照合を再開するようにした
   - 再評価結果: 全CLIを空にし、access/trunk矛盾を`unsupported_requirements`へ記録した
   - NTP、access、trunkの全検索条件、引用、製品分離も合格した
10. 過剰設定防止（改善後合格）
    - 送信先IPアドレスだけを指定してSyslog転送を要求する
    - 期待結果: 必要最小限のSyslog設定だけを生成する
    - trapレベル、facility、timestamp、buffer、VRFなどを追加しない
    - 実評価結果: `logging host 192.0.2.200`と、指定された禁止項目の
      非生成は合格した
    - 一方で`enable`、`configure terminal`、`logging on`、`end`を追加したため、
      最小構成1行だけという合格条件を満たさなかった
    - 検索根拠、引用、製品・リリース分離は合格した
    - 改善: 要件にない`enable`、`configure terminal`、`end`を生成後の
      構造検証で除外する
    - 最小限のSyslog転送要件では、明示されていない`logging on`も任意設定として除外する
    - 再評価結果: `logging host 192.0.2.200`の1行だけを生成し、
      trap、facility、timestamp、buffer、source-interface、VRFを含む追加CLIはなかった
    - 検索根拠、引用、製品・リリース分離も合格した

### RAG難化ケースの再現性評価計画

10ケースの単発評価完了後、LLM出力と検索計画の揺れを確認するため、各ケースを
独立した出力先で3回ずつ、合計30回実行する。既存結果の再利用を避けるため、
出力先は次の形式とする。

```text
data/evaluation/reproducibility/<case-id>/run-01.json
data/evaluation/reproducibility/<case-id>/run-02.json
data/evaluation/reproducibility/<case-id>/run-03.json
```

各ケースの合格条件:

- 3回すべてで`retrieval_pass`、`configuration_pass`、`citation_pass`、
  `product_isolation_pass`が合格する
- 3回の正規化済み`configuration`が一致する
- 生成拒否ケースは3回すべてで`configuration`が空になり、
  `unsupported_requirements`または`missing_required_values`へ理由を記録する
- 生成成功ケースは必須CLIが3回すべて存在し、禁止CLIが一度も混入しない
- `inferred_commands`は`knowledge_policy=disallow`の全実行で空とする

結果の扱い:

- 3/3合格を再現性あり、2/3以下を再現性なしと判定する
- 警告文、説明文、引用順の表現差は許容するが、CLI、製品、リリース、
  未対応・不足判定の意味が変わる差異は失敗とする
- 失敗時は検索結果、機能別検索計画、CLI候補、生成結果を比較し、
  原因を検索揺れ、根拠配分、生成判断、構造検証へ分類する
- まず新規ケース6～10を単発評価し、必要な改善を反映した後に30回評価を開始する

### 再現性評価結果（30回）

10ケースを各3回、独立した出力先で実行した。30個の評価結果JSONは
`data/evaluation/reproducibility/<case-id>/run-XX.json`へ保存した。

| ケース | 内容 | 結果 | 正規化設定 |
|---|---|---:|---|
| 1 | 対応機能＋未対応機能の複合 | 3/3 | 一致 |
| 2 | 矛盾する要件 | 3/3 | 一致 |
| 3 | 情報不足（Syslog送信先なし） | 2/3 | 不一致 |
| 4 | 否定表現（NTP削除） | 3/3 | 一致 |
| 5 | 例示値の混入防止 | 2/3 | 不一致 |
| 6 | 複数対象と優先指定 | 2/3 | 不一致 |
| 7 | 依存関係を持つ複合設定 | 3/3 | 一致 |
| 8 | 削除時の必須値不足 | 2/3 | 不一致 |
| 9 | 正常要件＋矛盾要件の複合 | 3/3 | 一致 |
| 10 | 過剰設定防止 | 2/3 | 不一致 |

結果は、全30回のうち25回が全項目合格、5ケースが3/3の再現性あり、
5ケースが2/3で再現性なしとなった。失敗内容は次のとおり。

- ケース3: 送信先不足時に空設定となる回と、`logging trap`・timestamp等の
  部分設定を残す回があり、生成判断と不足値検出が揺れた
- ケース5: 認証なし要件を空設定として拒否する回があり、例示値を除いた
  NTP設定を生成する回と拒否判定が揺れた
- ケース6: NTPサーバー2台と`prefer`は維持したが、`no ntp authenticate`
  や認証設定の追加有無が揺れた
- ケース8: 対象IP不足時に空設定とした回と、`no ntp server ip-address`を
  部分生成した回があった
- ケース10: 送信先指定を正しく生成する回と、根拠不足として全拒否する回があった

なお、初回の一括実行ではケース10 run-01が検索語展開のCLI候補数上限超過
（`Query expansion returned too many CLI terms`）で出力前に停止したため、
ケース10は独立再試行した3結果を正式集計に使用した。この実行エラー自体も
検索語展開の再現性リスクとして記録する。

### 優先度1「要件前処理の決定化」実装結果

再現性評価で揺れた情報不足・明示的除外条件に対し、LLM生成前の要件検証を追加した。

- Syslog送信先のIPアドレスまたはホスト名がない場合は、LLMを呼び出さず
  `missing_required_values`へ記録して空設定を返す
- NTP削除で対象IPアドレスまたはホスト名がない場合は、同じく空設定を返す
- 認証なしNTPは、`ntp server`構文の根拠が取得できない場合だけ
  `unsupported_requirements`へ記録して拒否する
- 前処理による抑止理由を`warnings`へ明示し、`inferred_commands`は空にする

検証結果:

- RAGテスト: **35件成功**
- ケース3（Syslog送信先不足）: 検索・設定・引用・製品分離が合格
- ケース5（認証なしNTP）: 検索・設定・引用・製品分離が合格
- ケース8（NTP削除対象不足）: 検索・設定・引用・製品分離が合格

ケース5・8の同時実行時にはQdrantローカルストレージの排他ロックが発生したため、
順次実行で再評価した。

### 優先度2「生成結果の決定的正規化」実装結果

生成後のCLIを要件へ合わせて正規化する処理を追加した。

- 「認証は使用しません」を認証なし条件として認識する
- 認証なしNTPでは`ntp authenticate`、`ntp authentication-key`、
  `ntp trusted-key`、`key`、`source`、`version`、未指定の`prefer`を除外する
- 明示されたNTPサーバーIPと`prefer`だけを保持する
- `no ntp server ip-address`など、削除対象のプレースホルダーを除外する
- 最小限Syslogでは`logging trap`、`logging facility`、`service timestamps`、
  `logging buffered`などの任意設定を除外する

検証結果:

- RAGテスト: **37件成功**
- ケース6（複数NTP＋prefer）: 全項目合格
  - `ntp server 192.0.2.123`
  - `ntp server 192.0.2.124 prefer`
- ケース8（削除対象不足）: 全項目合格
- ケース10（過剰設定防止）: 全項目合格

### 優先度3「検索語展開の安定化」実装結果

LLMが返すCLI候補が上限を超えた場合に評価を異常終了させないよう改善した。

- CLI候補を重複除去し、機能ごとの先頭候補を先に確保する
- NTP、Syslog、`prefer`など要件から決定できる必須候補を優先する
- 指定値がある場合は`ntp server ip-address`、
  `logging host ip-address`の構文候補も保持する
- 削除要件では`no`形式を優先する
- 残りの候補は出現順に最大24件へ切り詰め、上限超過で例外にしない

検証結果:

- RAGテスト: **38件成功**
- ケース10（過剰設定防止）: 検索語上限エラーなし、全項目合格
- 最終設定: `logging host 192.0.2.200`のみ

### 優先度4「再現性評価」最終結果

優先度1〜3の改善と`ntp peer`から`ntp server`への要件整合補正後に、
難化ケース10件を各3回、合計30回順次実行した。

- 全30回で検索・設定・引用・製品分離が合格
- 全10ケースで正規化後の`configuration`が3回一致
- 全30回で`inferred_commands`は空
- `evaluation-errors.log`は空で、実行エラーなし
- ケース3と8の生成前検証による拒否理由は3回完全一致
- ケース1、2、9の拒否理由は判定内容が同じである一方、LLM由来の説明文と
  不足値の表現には揺れが残る

初回評価の25/30、改善途中のv2・v3評価の29/30に対し、最終v4評価では
**30/30合格、全ケース3/3再現**となった。評価結果は
`data/evaluation/reproducibility-v4/`へ保存した。

### 検証環境準備後の保留事項

保留事項:

1. ユーザー提供の実運用要件・正解コンフィグを追加し、評価ケースを拡張する
2. 検証用Catalyst 9300または仮想機で投入前後の動作を評価する
3. 現在設定との差分、依存機能、到達性、ロールバック手順を含む実機検証を行う

## 未決定事項

- `Diagram`以外の画像をOCR候補へ含める条件
- Cloud Visionを採用した後も`llama.cpp`を導入するか
- Phase 4のFrictionless Data Packageメタデータ仕様
