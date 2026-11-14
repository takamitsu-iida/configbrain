# configbrain2 実装・運用ガイド

Ciscoの英語マニュアルをOpen Knowledge Formatへ変換するパイプラインの、
実装、保守、詳細運用に必要な情報をまとめます。

プロジェクトの概要と基本的な利用手順は、[README](../README.md)を参照してください。

## 開発環境

依存関係は `uv` で管理します。

```bash
uv sync
```

### HTTPS通信用のCAバンドル

社内TLS検査CAを含む結合済みCAバンドルは、次のプロジェクト内パスへ配置します。

```text
certs/local-ca-bundle.pem
certs/fj-sig-ca.pem
```

`local-ca-bundle.pem`は通信時に使う結合済みバンドル、`fj-sig-ca.pem`は将来の再生成に使う社内CA証明書です。証明書は端末固有のためGitには保存しません。新しい環境では、OSのCAバンドルと必要な社内CA証明書を結合し、PEM形式でこのパスへ配置してください。

`.envrc`を読み込むと、Python Requests系と標準SSL系が同じバンドルを使用するよう、次の環境変数が自動設定されます。

```text
REQUESTS_CA_BUNDLE=<project root>/certs/local-ca-bundle.pem
SSL_CERT_FILE=<project root>/certs/local-ca-bundle.pem
```

この実行環境から継承されるMenlo SecurityのHTTP(S)プロキシは、検証可能な証明書チェーンを提示しないため、`.envrc`で解除します。外部HTTPS通信は、プロジェクト内CAバンドルを使った直接接続に統一します。

`direnv`を使用しない場合は、プロジェクトルートで次を実行します。

```bash
source .envrc
```

証明書を更新した場合は、シェルを再読み込みしてからHTTPS通信を実行してください。TLS検証の無効化は行わないでください。
## 文書カタログの作成（Discovery）

Cisco公式の製品リリースページから、指定リリースに属するPDFマニュアルを検出してカタログを作成します。探索対象は `https://www.cisco.com` 上のPDFだけで、別ホスト、HTTP URL、指定外リリースのリンクは除外されます。Ciscoの公式リリースページがHTML版ガイドだけを掲載している場合は、同一パスの`.pdf`へ安全に正規化します。PDF URLはフラグメントを除去して正規化し、文書IDは製品・リリース・URLから安定して生成します。

Catalyst 9300 / IOS XE 26.xでは、次のCisco公式ガイド一覧を`--seed-url`に使用します。

```text
https://www.cisco.com/c/en/us/support/switches/catalyst-9300-series-switches/products-installation-and-configuration-guides-list.html
```

```bash
uv run python scripts/discover_manuals.py \
  --product catalyst9300 \
  --release 26.x \
  --seed-url https://www.cisco.com/c/en/us/support/switches/catalyst-9300-series-switches/products-installation-and-configuration-guides-list.html \
  --output data/catalog/c9300-iosxe26.json
```

異機種混在評価用のCatalyst 9500 / IOS XE 26.xカタログは次のように生成します。

```bash
uv run python scripts/discover_manuals.py \
  --product catalyst9500 \
  --release 26.x \
  --seed-url https://www.cisco.com/c/en/us/support/switches/catalyst-9500-series-switches/products-installation-and-configuration-guides-list.html \
  --output data/catalog/c9500-iosxe26.json
```

カタログにはタイトル、リリースページ上のカテゴリ、URL、ファイル名、安定した文書IDを記録します。同じ出力先へ再実行すると、前回カタログとの差分（`new`、`updated`、`removed`）も保存します。
Packagingはカタログの正規化済み`product`と`release`をData Packageの
`configbrain.catalogProduct`と`configbrain.release`へ保存します。
## マニュアルの取得

`data/manuals.json` に記載したCiscoマニュアルをダウンロードします。PDFは文書ごとに次の階層へ保存されます。

```text
data/products/catalyst9300/raw/<document_id>/<filename>.pdf
```

```bash
uv run python scripts/download_manuals.py
```

ダウンロード成功後、マニフェストの `sha256`、`size_bytes`、`downloaded_at` が更新されます。既にチェックサムが一致するファイルは再利用します。再取得する場合は `--force` を指定します。
## Phase 1: Extraction

`marker_single`（`marker-pdf`パッケージに含まれるCLI）を使って、PDFからRaw Markdownを抽出します。
Markerが初回に取得するSuryaモデルは、社内プロキシとの互換性を避けるため
Hugging Face Xetを無効化し、通常のHTTPS経由でダウンロードします。
Marker subprocessでは継承されたHTTP(S)プロキシを解除し、Suryaのローカル推論サーバー
（`127.0.0.1`）との通信がプロキシへ送信されないようにします。
外部HTTPS通信には「HTTPS通信用のCAバンドル」で設定した環境を使用します。
大規模PDFは既定で50ページずつMarkerへ渡し、Markdown、画像、ページメタデータを
抽出完了後に統合します。これにより、全ページ画像を一括送信する際のメモリ枯渇を防ぎます。

```bash
uv run python scripts/extract.py input/manual.pdf --output-dir output/raw
```

出力先が既に存在し、空でない場合は、既存データとの混在を防ぐため処理を中止します。
実行ファイル名を変更する必要がある場合は、`--command` で指定できます。

```bash
uv run python scripts/extract.py input/manual.pdf \
  --output-dir output/raw \
  --command marker_single
```

Marker OCRは既定で無効で、PDFのテキストレイヤーだけを使用します。
`--disable-ocr`は既定動作を明示する互換オプションです。
スキャンPDFや画像化された表の文字は抽出されないため、必要な場合だけ
`--enable-ocr`でMarker OCRを有効化してください。

```bash
uv run python scripts/extract.py input/manual.pdf \
  --output-dir output/raw \
  --enable-ocr
```

1回に処理するページ数は`--page-batch-size`で変更できます。

```bash
uv run python scripts/extract.py input/manual.pdf \
  --output-dir output/raw \
  --page-batch-size 50
```

### Cloud Vision OCR

`.env`に次の設定を定義します。APIキーをリポジトリへ保存しないでください。

```text
CLOUD_VISION_API_URL=<image OCR endpoint>
CLOUD_VISION_FILE_API_URL=<PDF OCR endpoint>
CLOUD_VISION_API_KEY=<API key>
```

最初にPDFのテキストレイヤーとMarker抽出画像を分析し、OCR候補マニフェストを生成します。既定ではテキストが50文字未満のPDFページと、10万画素以上の`Diagram`画像を選びます。この処理はCloud Vision APIを呼び出しません。

```bash
uv run python scripts/ocr_candidates.py input/manual.pdf \
  --images-dir output/raw/manual \
  --output data/products/catalyst9300/ocr/document-id/candidates.json
```

画像OCRは、座標情報を含むAPIレスポンスをJSONへ保存します。

```bash
uv run python scripts/cloud_vision.py image input/diagram.png \
  --output data/products/catalyst9300/ocr/document-id/diagram.json
```

PDF OCRは1始まりのページ番号を指定します。同期ファイルAPIの制約に合わせ、1回に指定できるのは最大5ページです。

```bash
uv run python scripts/cloud_vision.py pdf input/manual.pdf \
  --pages 24,25 \
  --output data/products/catalyst9300/ocr/document-id/pages-24-25.json
```

出力には入力ファイルのSHA-256、ファイルサイズ、指定ページ、Cloud Visionの座標付きレスポンスが含まれます。既存ファイルの上書きには`--force`が必要です。

候補マニフェスト内の画像は、次のコマンドでまとめてOCRできます。画像ごとの結果を
`--output-dir`へ保存するため、中断後に同じコマンドを実行すると、入力パス・サイズ・SHA-256が一致する完了済み結果をスキップして再開します。

```bash
uv run python scripts/ocr_batch.py \
  data/products/catalyst9300/ocr/document-id/candidates.json \
  --output-dir data/products/catalyst9300/ocr/document-id
```

候補画像の内容を変更した場合や、既存結果を意図的に再作成する場合は`--force`を指定します。

候補画像に対応するOCR結果を、Marker Markdownの画像参照直後へ折りたたみ表示で統合できます。元のMarkdownと座標付きOCR JSONは変更されません。OCR JSONの入力情報が候補画像と一致しない場合、または候補画像の参照がMarkdownにない場合は停止します。

```bash
uv run python scripts/ocr_integrate.py \
  data/products/catalyst9300/extracted/document-id/document-id/document-id.md \
  --manifest data/products/catalyst9300/ocr/document-id/candidates.json \
  --ocr-dir data/products/catalyst9300/ocr/document-id \
  --output data/products/catalyst9300/processed/document-id/document-id.md
```

統合箇所は次の形式です。

````markdown
![](_page_23_Diagram_3.jpeg)

<details>
<summary>Cloud Vision OCR text for _page_23_Diagram_3.jpeg</summary>

```text
OCR text
```
</details>
````

### 抽出結果のディレクトリ構成

Markerが出力するディレクトリ構成をそのまま保持します。`document_id`は処理単位を区切り、PDFのstemは同一文書内のMarker出力を区切ります。後続のOCR候補選定・OCR統合でも、この構成を前提にします。

```text
data/products/catalyst9300/extracted/<document_id>/<PDF stem>/
├── <PDF stem>.md
├── <PDF stem>_meta.json
└── _page_*_*.jpeg
```
## Phase 2: Chunking

Markdownを見出し境界を優先して、Phase 3のLLM処理用チャンクへ分割します。トークン数は外部モデルに依存しない概算として、4文字を1トークンとみなし、既定で6,000トークンに制限します。見出しセクションが上限を超える場合は、見出し階層を各チャンクへ引き継いだ上で段落境界から分割します。

```bash
uv run python scripts/chunk_markdown.py \
  data/products/catalyst9300/processed/document-id/document-id.md \
  --output-dir data/products/catalyst9300/chunks/document-id
```

出力先には`chunk-0001.md`のようなMarkdownファイルと、入力チェックサム、推定トークン数、見出し階層を記録した`manifest.json`を保存します。既存の出力ディレクトリは上書きしません。
一時ディレクトリのrenameが拒否される共有ファイルシステムでは、
検証済み一時出力を安全にコピーして配置します。
## Phase 3: Transformation

チャンクをOpenAI互換のChat Completions APIへ送り、英語本文の整形とメタデータ抽出を行います。
APIキーは環境変数または`.env`に保存し、リポジトリへコミットしないでください。

```text
LLM_API_URL=https://api.example.test/v1/chat/completions
LLM_API_KEY=<API key>
LLM_MODEL=<model name>
LLM_TIMEOUT=180
```

モデル名をURLに含み、`api-key`ヘッダーを使う社内Cloud GPTエンドポイントでは、次の別名も使用できます。この場合、`LLM_MODEL`は不要です。

```text
CLOUD_GPT_API_URL=https://api.example.test/path/to/model
CLOUD_GPT_API_KEY=<API key>
```

各出力Markdownの先頭へYAMLフロントマターを付け、処理内容を`manifest.json`へ記録します。
LLMがJSON以外を返した場合、コードブロックが閉じていない場合、または既存出力がある場合は停止します。
画像参照、Markdownリンク、Markerの内部リンク用アンカーは構文を残したまま
URLまたはIDだけを保護トークンへ一時置換し、
LLM応答内で完全に保持されたことを検証してから復元します。
Markerが生成する`Return to ... on page ...`形式の重複ナビゲーション行は、
リンク保護前に決定的に除去し、LLMによる保護トークン欠落を防ぎます。
LLM応答のコードフェンスまたは保護トークン検証に失敗した場合は、
元入力から1回だけ自動再生成し、再度失敗した場合は出力を保存せず停止します。

```bash
uv run python scripts/transform_markdown.py \
  data/products/catalyst9300/chunks/document-id \
  --output-dir data/products/catalyst9300/transformed/document-id \
  --resume
```

`--resume`を指定するとチャンクごとに出力とマニフェストを原子的に保存します。
通信中断後に同じコマンドを実行すると、入力・出力SHA-256が一致する完了済みチャンクをスキップします。
全チャンクのメタデータは、空でない値の最頻値（同数なら先に出現した値）で
`manifest.json`の`document_metadata`へ統合します。
## Phase 4: Packaging

変換済みチャンクを単一の`manual.md`へ結合し、参照画像と
`datapackage.json`を含むFrictionless Data Packageを生成します。

```bash
uv run python scripts/package_document.py \
  data/products/catalyst9300/transformed/document-id \
  --assets-dir data/products/catalyst9300/extracted/document-id/pdf-stem \
  --document-id document-id \
  --output-dir data/products/catalyst9300/packages/document-id
```

生成前に各チャンクのSHA-256と変換完了状態を検証します。チャンクごとの
フロントマターは除去し、文書単位のフロントマターを1件だけ付与します。
画像参照は`images/`配下へ書き換え、参照されている画像だけを同梱します。
Marker抽出時点から参照先アンカーが存在しない内部リンクは、表示テキストを保持したまま
リンク構文だけを解除し、解除件数を`datapackage.json`へ記録します。
各resourceにはファイルサイズとSHA-256を記録し、出力配置前に
Frictionlessライブラリでパッケージ全体を検証します。既存の出力先は上書きしません。

出力構成:

```text
data/products/catalyst9300/packages/<document_id>/
├── datapackage.json
├── manual.md
└── images/
    └── <referenced images>
```
## パイプラインの実行と状態管理

オーケストレーションの状態はSQLiteで管理します。`plan` はカタログを状態DBへ同期し、文書・各フェーズの状態、カタログ差分、既知のチャンク数から算出したGPT変換呼び出し数を表示します。新規文書は全フェーズを `pending` で登録し、カタログ上の変更は `stale`、削除は `skipped` として保存します。

```bash
uv run python scripts/pipeline.py plan \
  --catalog data/catalog/c9300-iosxe26.json \
  --data-root data/products/catalyst9300
```

既定の製品ルートは`data/products/catalyst9300`、状態DBは
`<data-root>/state.sqlite3`です。状態には `pending`、`running`、`completed`、
`failed`、`stale`、`skipped` を使用し、フェーズごとに入力・出力SHA-256、
実行フィンガープリント、試行回数、エラー、実行時刻を保持します。

```bash
uv run python scripts/pipeline.py status --product catalyst9300
```

`status`は誤った製品の状態を暗黙に表示しないよう、`--product`または
`--state-db`のどちらか一方を必須とします。

`run` はカタログを同期してから、指定文書・指定フェーズを実行します。文書ごとの失敗はSQLiteへ記録し、他文書の処理は継続します。完了済みまたはOCR候補なしで安全にスキップ済みのフェーズは再実行しません。
コストとCiscoマニュアルのテキストレイヤー品質を考慮し、Marker OCRとCloud Vision OCRは
どちらも既定で無効です。必要な文書だけ`--enable-marker-ocr`または
`--enable-cloud-vision`で有効化してください。

```bash
uv run python scripts/pipeline.py run \
  --catalog data/catalog/c9300-iosxe26.json \
  --document-id catalyst9300-26-x-example-aaaaaaaaaaaa \
  --from download \
  --through packaging \
  --resume
```

終了時の`Pipeline run`は、今回の呼び出しで処理したフェーズ数の集計です。
例えば`completed=1, failed=1`は「1フェーズが完了し、次の1フェーズが失敗した」という意味であり、パイプライン全体の完了ではありません。失敗したフェーズとエラーは`status`で確認できます。

`--document-id` はカタログに記載されているものを指定します。複数個指定できます。省略すると全文書が対象になりますが、まずは1文書ずつ進めるのが安全です。

`--from` と `--through` で実行範囲を絞り、`--resume` は中断済みのLLM変換を既存チェックポイントから再開します。Cloud Vision OCRの候補が0件の場合、抽出Markdownをそのまま後続フェーズ用に保存してOCRフェーズを `skipped` とします。

既定ではCloud Visionを呼ばず、候補マニフェストだけを生成して抽出Markdownを
そのままChunkingへ渡します。Cloud Visionを利用する場合だけ
`--enable-cloud-vision`を指定します。

### `pipeline.py`のコマンドとオプション

基本構文:

```bash
uv run python scripts/pipeline.py <command> [options]
```

コマンドは次の4種類です。

| コマンド | 用途 |
|---|---|
| `plan` | カタログを状態DBへ同期し、予定されている処理を集計表示する |
| `status` | 状態DBに保存された文書・フェーズ別の状態とエラーを表示する |
| `run` | カタログ内の文書について、指定範囲のフェーズを実行する |
| `retry` | `failed`を含む文書を、完了済みフェーズを保持して再実行する |

#### `plan`

| オプション | 必須 | 既定値 | 説明 |
|---|:---:|---|---|
| `--catalog PATH` | Yes | - | Discoveryで生成した文書カタログ |
| `--data-root PATH` | No | `data/products/catalyst9300` | 製品別の全成果物ルート |
| `--state-db PATH` | No | `<data-root>/state.sqlite3` | 状態を保存するSQLite DB |
| `--chunks-root PATH` | No | `<data-root>/chunks` | 既知のチャンク数を集計するディレクトリ |

#### `status`

| オプション | 必須 | 既定値 | 説明 |
|---|:---:|---|---|
| `--product ID` | 選択必須 | - | `data/products/<product>/state.sqlite3`を表示 |
| `--state-db PATH` | 選択必須 | - | 表示対象のSQLite状態DB |

`--product`と`--state-db`は同時に指定できません。

#### `run`

| オプション | 必須 | 既定値 | 説明 |
|---|:---:|---|---|
| `--catalog PATH` | Yes | - | 実行対象を含む文書カタログ |
| `--data-root PATH` | No | `data/products/catalyst9300` | 製品別の全成果物ルート |
| `--state-db PATH` | No | `<data-root>/state.sqlite3` | 状態を保存するSQLite DB |
| `--document-id ID` | No | 全文書 | 対象の`document_id`。複数指定する場合はオプションを繰り返す |
| `--pending-only` | No | 無効 | `pending`フェーズを持つ未処理文書だけを対象にし、完了済み文書を保持する |
| `--from PHASE` | No | `download` | 実行範囲の開始フェーズ |
| `--through PHASE` | No | `packaging` | 実行範囲の終了フェーズ |
| `--resume` | No | 無効 | Transformationの既存チェックポイントから再開する |
| `--enable-marker-ocr` | No | 無効 | ExtractionでMarker内蔵OCRを有効にする |
| `--disable-marker-ocr` | No | 有効 | Marker内蔵OCRを無効にする。既定動作を明示する互換オプション |
| `--enable-cloud-vision` | No | 無効 | Cloud Vision OCRを有効にする |
| `--skip-cloud-vision` | No | 有効 | Cloud Visionを呼ばない。既定動作を明示する互換オプション |
| `--raw-root PATH` | No | `<data-root>/raw` | ダウンロードPDFの出力ルート |
| `--extracted-root PATH` | No | `<data-root>/extracted` | Marker抽出結果の出力ルート |
| `--ocr-root PATH` | No | `<data-root>/ocr` | OCR候補と結果の出力ルート |
| `--processed-root PATH` | No | `<data-root>/processed` | OCR統合済みMarkdownの出力ルート |
| `--chunks-root PATH` | No | `<data-root>/chunks` | 分割済みチャンクの出力ルート |
| `--transformed-root PATH` | No | `<data-root>/transformed` | LLM変換結果の出力ルート |
| `--packages-root PATH` | No | `<data-root>/packages` | Data Packageの出力ルート |
| `--max-retries N` | No | `3` | 一時エラー発生後の最大再試行回数。初回実行は回数に含まない |
| `--retry-base-delay SEC` | No | `1.0` | 指数バックオフの初回待機秒数 |
| `--quota-stop-percent PERCENT` | No | `95` | LLM応答の`X-Quota-Usage-Percent`がこの値以上なら、チャンク保存後に停止する |

`--from`と`--through`に指定できるフェーズは、`download`、`extraction`、`ocr`、`chunking`、`transformation`、`packaging`です。開始フェーズは終了フェーズ以前である必要があります。

カタログ全体を実行する場合は、

```bash
uv run python scripts/pipeline.py run \
  --catalog data/catalog/c9300-iosxe26.json \
  --pending-only \
  --through packaging \
  --resume
```

##### `Ctrl-C`で中断した処理を再開する

長時間実行を中断する場合は、`Ctrl-C`を1回押してプロセスの終了を待ってください。
中断したフェーズは状態DBで`running`のまま残ることがありますが、完了済みフェーズとその出力は保持されます。

最初に状態を確認します。

```bash
uv run python scripts/pipeline.py status --product catalyst9300
```

次に、`--pending-only`を付けずに同じカタログを再実行します。

```bash
uv run python scripts/pipeline.py run \
  --catalog data/catalog/c9300-iosxe26.json \
  --through packaging \
  --resume
```

完了済みまたは安全にスキップ済みのフェーズは再実行されません。
Transformationは保存済みチェックポイントの次のチャンクから再開し、それ以外の中断フェーズは原則としてフェーズの先頭から再実行します。

`--pending-only`は`running`だけが残った文書を選択できない場合があるため、中断後の再開には使用しません。
また、`retry --failed`は`failed`だけを対象とし、`running`は対象にしないため、`Ctrl-C`後の再開には使用しません。

#### `retry`

| オプション | 必須 | 既定値 | 説明 |
|---|:---:|---|---|
| `--failed` | Yes | - | `failed`フェーズを持つ文書だけを対象にする |
| `--catalog PATH` | Yes | - | 実行対象を含む文書カタログ |
| `--data-root PATH` | No | `data/products/catalyst9300` | 製品別の全成果物ルート |
| `--state-db PATH` | No | `<data-root>/state.sqlite3` | 状態を保存するSQLite DB |
| `--resume` | No | 無効 | Transformationの既存チェックポイントから再開する |
| `--enable-marker-ocr` | No | 無効 | ExtractionでMarker内蔵OCRを有効にする |
| `--disable-marker-ocr` | No | 有効 | Marker内蔵OCRを無効にする。既定動作を明示する互換オプション |
| `--enable-cloud-vision` | No | 無効 | Cloud Vision OCRを有効にする |
| `--skip-cloud-vision` | No | 有効 | Cloud Visionを呼ばない。既定動作を明示する互換オプション |
| `--raw-root PATH` | No | `<data-root>/raw` | ダウンロードPDFの出力ルート |
| `--extracted-root PATH` | No | `<data-root>/extracted` | Marker抽出結果の出力ルート |
| `--ocr-root PATH` | No | `<data-root>/ocr` | OCR候補と結果の出力ルート |
| `--processed-root PATH` | No | `<data-root>/processed` | OCR統合済みMarkdownの出力ルート |
| `--chunks-root PATH` | No | `<data-root>/chunks` | 分割済みチャンクの出力ルート |
| `--transformed-root PATH` | No | `<data-root>/transformed` | LLM変換結果の出力ルート |
| `--packages-root PATH` | No | `<data-root>/packages` | Data Packageの出力ルート |
| `--max-retries N` | No | `3` | 一時エラー発生後の最大再試行回数 |
| `--retry-base-delay SEC` | No | `1.0` | 指数バックオフの初回待機秒数 |
| `--quota-stop-percent PERCENT` | No | `95` | LLM応答の`X-Quota-Usage-Percent`がこの値以上なら、チャンク保存後に停止する |

LLM呼び出しごとに、レスポンスヘッダーから取得したクオータ使用率を表示します。
ヘッダーが返されない場合は`LLM quota usage: unavailable`と表示します。

```text
LLM quota usage: 42.5%
Transformed chunk 10/120: chunk-0010.md
```

各コマンドの機械的な最新一覧は`--help`でも確認できます。

```bash
uv run python scripts/pipeline.py --help
uv run python scripts/pipeline.py run --help
uv run python scripts/pipeline.py retry --help
```

### 小規模試行

LLMとCloud Visionを呼ばずに1文書だけをDownloadからChunkingまで試す場合は、状態DBと各出力ルートを隔離してください。

```bash
uv run python scripts/pipeline.py run \
  --catalog data/catalog/c9300-iosxe26.json \
  --state-db data/trials/c9300-smoke/state.sqlite3 \
  --document-id <catalog document_id> \
  --from download \
  --through chunking \
  --raw-root data/trials/c9300-smoke/raw \
  --extracted-root data/trials/c9300-smoke/extracted \
  --ocr-root data/trials/c9300-smoke/ocr \
  --processed-root data/trials/c9300-smoke/processed \
  --chunks-root data/trials/c9300-smoke/chunks \
  --transformed-root data/trials/c9300-smoke/transformed \
  --packages-root data/trials/c9300-smoke/packages
```

### 差分無効化

`plan` と `run` は開始前に完了済み出力のチェックサムと設定フィンガープリントを再検証します。不一致があれば、最初に影響するフェーズと下流だけを `stale` にします。対象はPDF変更（Extraction以降）、OCR候補条件変更（OCR以降）、チャンク条件変更（Chunking以降）、LLMモデルまたはプロンプト変更（Transformation以降）、パッケージ仕様または文書メタデータ変更（Packaging以降）です。欠落または手動変更された出力も同様に検出します。

### 再試行と失敗隔離

各文書のフェーズ失敗はSQLiteに記録し、他文書の処理は継続します。HTTP 429／5xx、タイムアウト、DNS・接続障害だけを対象に、既定で最大3回、1秒・2秒・4秒の指数バックオフで再試行します。チェックサム不一致、不正JSON、保護トークン不一致、画像欠落、Frictionless検証失敗などの整合性エラーは再試行しません。`--max-retries` と `--retry-base-delay` で実行時に変更できます。

失敗済み文書だけを、完了済みフェーズを再実行せずに再開するには次を実行します。

```bash
uv run python scripts/pipeline.py retry \
  --failed \
  --catalog data/catalog/c9300-iosxe26.json \
  --resume
```

成功後は同じ文書の後続フェーズも順番に実行されます。最終的に`packaging completed`となった後、必要な場合だけ「RAGによるCiscoコンフィグ生成評価」の手順でIndexingを実行します。
## RAGによるCiscoコンフィグ生成評価

Catalyst 9300とCatalyst 9500のData Packageを見出し単位に再分割し、ローカル多言語埋め込みと
同じQdrantストレージ内の製品別コレクションへ索引化します。
初回実行時は`intfloat/multilingual-e5-small`を取得します。

- `cisco_manual_sections_catalyst9300`: 18文書、13,220節
- `cisco_manual_sections_catalyst9500`: 15文書、12,363節

製品ごとにコレクションを分割するため、各コレクションはQdrantローカルモードが
非推奨とする20,000ポイント未満です。`--product`から対象コレクションを自動選択し、
payloadの製品完全一致フィルターも重ねて適用します。

旧共有コレクションを使用している環境では、次のコマンドを一度だけ実行すると、
既存ベクトルを再計算せずに製品別コレクションへ分割します。

```bash
uv run python scripts/rag.py migrate-collections \
  --qdrant-path data/qdrant
```

```bash
uv run python scripts/rag.py index \
  --packages-dir data/products/catalyst9300/packages \
  --qdrant-path data/qdrant \
  --incremental
```

すでに作成済みの索引では、`datapackage.json`のSHA-256を比較して変更・追加・削除された文書だけを更新できます。
削除判定とコレクション再構築は`--packages-dir`に含まれる製品だけが対象です。
別製品のコレクションと文書には影響しません。

```bash
uv run python scripts/validate_packages.py --packages-dir data/products/catalyst9300/packages
uv run python scripts/rag.py index \
  --packages-dir data/products/catalyst9300/packages \
  --qdrant-path data/qdrant \
  --incremental

uv run python scripts/validate_packages.py --packages-dir data/products/catalyst9500/packages
uv run python scripts/rag.py index \
  --packages-dir data/products/catalyst9500/packages \
  --qdrant-path data/qdrant \
  --incremental
```

日本語要件はGPT-5.1で英語検索文と候補CLI句へ展開し、多言語ベクトル検索と
BM25字句検索をRRF統合します。候補CLI句は実際にその句を含む根拠節の順位付けだけに使い、
根拠に存在しないコマンドの生成には使いません。展開した複数の検索文と候補CLI句は、
1回だけ読み込んだ製品・リリース別の字句検索コーパスを共有します。複合要件は独立した
機能へ分解し、各機能の上位根拠を最大2件ずつ確保してから全体順位の根拠を追加します。
生成時は、要件にないVRF名や認証キーなどをマニュアルの例からコピーしないよう制約します。
削除・解除・無効化の要件では`no`形式のCLI句を検索へ追加し、マニュアルの
`[ no ] command`表記も`no command`として字句一致させます。
「認証なし」など明示的な除外条件は機能別検索計画へ保持し、矛盾するCLI候補を
除外するとともに、除外対象を主題とする章・節の順位を下げます。
`ntp server ... prefer`のようにオプションや値を挟むCLI句は、最大16トークンの
可変部分を許容しながら語順を維持して照合し、字句検索の順位へ加点します。
CLI候補句は字句検索コーパス全体で順序付き一致を先に適用し、
一致した候補だけをBM25で順位付けして上位3件を根拠候補へ加えます。
`show`、`clear`、`debug`などの運用・確認コマンドは生成構文の根拠枠を
占有しないよう、CLI候補句の加点対象から除外します。
同一インターフェースへのaccess/trunk同時指定は生成後にも決定的に検出し、
正常な別要件を含む全CLIを破棄して`unsupported_requirements`へ理由を記録します。
順序付き一致は、文書前方の同じ先頭語が最大可変幅を超えた場合も、後続の先頭語から
照合を再開します。
生成後の構造検証では、要件にない`enable`、`configure terminal`、`end`を除外します。
最小限のSyslog転送を明示した要件では、送信先指定とは別の`logging on`も除外します。
認証なしNTPでは認証CLIと未指定のオプションを生成後に除外し、削除CLIに
`ip-address`などのプレースホルダーが残る場合も除外します。
LLMが返すCLI候補は重複除去後、機能ごとの先頭候補と要件から決定できる
主要構文を優先して最大24件へ決定的に切り詰めます。上限超過では検索計画を
異常終了させず、削除要件では`no`形式を優先します。
生成結果には、設定、説明、引用ID、警告、検索結果が含まれます。
生成前の要件検証では、Syslog送信先やNTP削除対象のIPアドレス・ホスト名が
不足している場合、LLMを呼び出さず空設定と不足値を返します。認証なしNTPでは、
`ntp server`構文の根拠が取得できない場合だけ生成前に未対応として拒否します。

### デモの実行

以下の3つのデモは、Catalyst 9300/9500の製品別コレクション選択、
製品固有の根拠取得、コンフィグ生成を確認するためのものです。
いずれも回答をターミナルへ表示すると同時にMarkdownレポートへ保存します。

初回実行時は埋め込みモデルの取得とキャッシュ作成があるため、特に時間がかかります。
キャッシュ作成後も、`generate`を起動するたびにモデルをメモリへ読み込み、
製品コレクションのベクトル検索とBM25字句検索を行うため、WSL環境では完了まで
数分かかる場合があります。処理中は進捗を表示せず、Markdownレポートも完了時に
書き出します。別のターミナルで次を実行し、`rag.py generate`のPythonプロセスが
CPUを使用していれば処理は継続中です。

```bash
ps -eo pid,stat,etime,pcpu,pmem,args | grep '[r]ag.py generate'
```

#### デモ1: 同じDHCP Relay要件を9300と9500で比較

同じ要件を製品だけ変えて実行し、それぞれの製品コレクションから根拠が取得されることを確認します。

```bash
uv run python scripts/rag.py generate \
  "Catalyst 9300のVlan100で受信するDHCP要求をDHCPサーバー192.0.2.50へ転送してください。" \
  --product catalyst9300 \
  --release 26.x \
  --qdrant-path data/qdrant \
  --packages-dir data/products/catalyst9300/packages \
  --output data/evaluation/demo-c9300-dhcp-relay.md

uv run python scripts/rag.py generate \
  "Catalyst 9500のVlan100で受信するDHCP要求をDHCPサーバー192.0.2.50へ転送してください。" \
  --product catalyst9500 \
  --release 26.x \
  --qdrant-path data/qdrant \
  --packages-dir data/products/catalyst9500/packages \
  --output data/evaluation/demo-c9500-dhcp-relay.md
```

両方とも`interface vlan 100`と`ip helper-address 192.0.2.50`が生成候補になりますが、
9300のレポートは9300文書だけ、9500のレポートは9500文書だけを参照します。

#### デモ2: Catalyst 9500の物理ポートをルーテッドポート化

L2ポート設定を無効化し、IPv4アドレスを設定する例です。

```bash
uv run python scripts/rag.py generate \
  "Catalyst 9500のFortyGigabitEthernet1/0/1をルーテッドポートにし、IPv4アドレス192.0.2.1/24を設定してください。" \
  --product catalyst9500 \
  --release 26.x \
  --qdrant-path data/qdrant \
  --packages-dir data/products/catalyst9500/packages \
  --output data/evaluation/demo-c9500-routed-port.md
```

生成結果では`no switchport`と`ip address 192.0.2.1 255.255.255.0`の根拠を確認します。
アクセスポート用の`switchport mode access`が混入していないことも確認してください。

#### デモ3: Catalyst 9500のStackWise Virtual DADリンク

Catalyst 9500の機種固有機能を検索・生成できることを確認します。

```bash
uv run python scripts/rag.py generate \
  "Catalyst 9500のFortyGigabitEthernet1/0/3をStackWise VirtualのFast Hello Dual-Active-Detectionリンクとして設定してください。" \
  --product catalyst9500 \
  --release 26.x \
  --qdrant-path data/qdrant \
  --packages-dir data/products/catalyst9500/packages \
  --output data/evaluation/demo-c9500-stackwise-virtual-dad.md
```

生成結果では対象インターフェース配下の`stackwise-virtual dual-active-detection`と、
Catalyst 9500 High Availability Guideの引用を確認します。

生成後、`data/evaluation/demo-*.md`をVS CodeのMarkdownプレビューで表示してください。
各レポートには次の情報が含まれます。

- 質問
- 生成コンフィグ
- 説明
- 注意事項
- 参照根拠
- 根拠外の推測
- 検索結果

参照根拠の引用IDと検索結果の文書名は、`--packages-dir`で指定した
`data/products/<product>/packages/<document_id>/manual.md`へのリンクです。
VS CodeのMarkdownプレビューからクリックして元文書を確認できます。

`.md`または`.markdown`を指定すると、質問、生成コンフィグ、説明、注意事項、
参照根拠、根拠外の推測、検索結果を含むMarkdownレポートを生成します。
それ以外の拡張子ではJSONを生成します。

LLMの事前知識をどこまでコンフィグ生成に使用できるかは、
`--knowledge-policy`で指定します。既定値は`disallow`です。

- `disallow`: 取得した根拠で構文を確認できないCLIを生成しない
- `limited`: 根拠に対象機能と操作がある場合に限り、一般的なIOS XE構文を補完する
- `allow`: 要件を満たすためにIOS XEの事前知識を使用できる

`limited`または`allow`で補完したCLI行は、結果の`inferred_commands`と
`warnings`へ明示されます。`disallow`では`inferred_commands`が1行でもあれば
生成を失敗させます。この検査はLLMによる申告を検証するものであり、未申告の推測を
機械的に完全検出するものではありません。

主要機能を実現するCLIの根拠が不足する場合、生成結果はその要件を
`unsupported_requirements`へ記録します。この配列が空でなければ、
インターフェース選択などの部分設定も含めて`configuration`を空にし、
破棄した設定を`warnings`へ記録します。

要求された操作に不可欠なIPアドレス、インターフェース、識別子などが不足する場合は、
`missing_required_values`へ記録します。任意パラメータは不足扱いにせず、この配列が
空でない場合も部分設定を破棄して`configuration`を空にします。

```bash
uv run python scripts/rag.py generate \
  "NTPサーバー192.0.2.123を設定してください" \
  --product catalyst9300 \
  --release 26.x \
  --knowledge-policy limited
```

`--product`は必須で、Discoveryカタログの`product`と完全一致する値を指定します。
`--release`を指定すると、製品に加えてリリースも完全一致で絞り込みます。
ベクトル検索と字句検索の両方へ同じフィルターを適用し、生成直前にも取得根拠が
指定製品・リリースだけであることを検証します。製品別コレクションの選択後にも
payloadフィルターを適用し、別機種の根拠が混入した場合は生成を停止します。

Catalyst 9300とCatalyst 9500の7パッケージを混在させたパイロットでは、
3,957節を索引化し、DHCP relay、NTP、ルーテッドポート、StackWise Virtual
Dual-Active-Detectionを含む6ケースで、検索、必須／禁止CLI、引用、
製品・リリース分離がすべて6/6となりました。評価ケースは
`data/evaluation/mixed_platform_cases.json`、結果は
`data/trials/mixed-rag/results.json`に保存します。

代表10ケースの静的評価では、検索期待語、必須CLI、禁止CLI、引用の有無を
決定的に検証します。各ケースには`product`と任意の`release`を定義し、
取得結果が対象製品・リリースだけであることも検証します。
中断後は、評価ケースとインデックスのSHA-256が一致する
完了済みケースを再利用します。

```bash
uv run python scripts/evaluate_rag.py \
  --qdrant-path data/qdrant \
  --knowledge-policy disallow \
  --output data/evaluation/results.json
```

追加の難関評価では、アクセスポートとトランクポートの誤選択、複数機能の同時設定、
対象機種で根拠を確認できない要求に対する生成抑止を検証します。
`expect_no_configuration`が`true`のケースは、設定が空で、警告があり、
根拠外の推測コマンドがない場合だけ成功します。

```bash
uv run python scripts/evaluate_rag.py \
  --cases data/evaluation/challenging_cases.json \
  --qdrant-path data/qdrant \
  --knowledge-policy disallow \
  --output data/evaluation/challenging-results.json
```

この評価は構文と根拠性のPoCです。実機投入前には、対象機器の現在設定との差分、
機能依存関係、到達性、ロールバック手順を別途検証してください。
## テスト

```bash
uv run pytest
```
