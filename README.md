# configbrain2

Ciscoの英語マニュアルを、検索・参照可能なOpen Knowledge Formatへ変換し、
公式情報を根拠にCiscoコンフィグを生成するためのPythonパイプラインです。

## このプロジェクトでできること

- 対象製品とIOS XEリリースを限定した公式マニュアルを収集する
- PDFをMarkdownへ変換し、検索に適した単位へ分割・整形する
- 出典や製品情報を保持したData Packageを生成する
- Data PackageをQdrantへ索引化する
- 日本語の要件から、根拠付きのCiscoコンフィグ案を生成する

## なぜ公式マニュアルをRAG化するのか

Web検索だけを利用すると、異なる製品やIOS XEリリース、非公式情報、古い情報を
混同する可能性があります。本プロジェクトでは、対象を明示した公式マニュアルを
管理された知識源として使用します。

これにより、次のことが可能になります。

- 製品、リリース、文書、出典URLを保持した検索
- 同じData Packageと索引を使った検索結果の再現
- 回答の根拠となった文書と該当箇所の提示
- マニュアルの追加、更新、削除の増分反映
- Web検索を利用できない環境での公式情報の参照

RAGはコンフィグの正確性を保証するものではありません。生成結果は、対象機器と
稼働中のリリースに対してレビューし、実機へ投入する前に現在設定との差分、
機能依存関係、到達性、ロールバック手順を確認してください。

## 処理の流れ

| 順序 | 処理 | 主な出力 |
|---:|---|---|
| 1 | Cisco公式ページから文書を検出 | `data/catalog/*.json` |
| 2 | PDFをダウンロード | `data/products/<product>/raw/` |
| 3 | PDFをMarkdownへ変換 | `data/products/<product>/extracted/` |
| 4 | 必要な画像へOCRを適用 | `data/products/<product>/processed/` |
| 5 | Markdownを分割・整形 | `data/products/<product>/chunks/`, `transformed/` |
| 6 | Data Packageを生成 | `data/products/<product>/packages/` |
| 7 | Qdrantへ索引化 | `data/qdrant/` |
| 8 | 根拠付きコンフィグを生成 | MarkdownまたはJSONレポート |

## 利用を始める

### 1. 環境を準備する

Python 3.12以降と[uv](https://docs.astral.sh/uv/)が必要です。

```bash
uv sync
cp .env.example .env
```

`.env`へ利用するLLM APIの接続情報を設定してください。OCRを利用する場合は
Cloud Vision APIの接続情報も設定します。APIキーはリポジトリへ保存しないでください。

社内CAやプロキシを含む詳しい環境設定は、
[実装・運用ガイド](docs/implementation.md#開発環境)を参照してください。

### 2. 文書カタログを作成する

次はCatalyst 9300 / IOS XE 26.xの例です。

```bash
uv run python scripts/discover_manuals.py \
  --product catalyst9300 \
  --release 26.x \
  --seed-url https://www.cisco.com/c/en/us/support/switches/catalyst-9300-series-switches/products-installation-and-configuration-guides-list.html \
  --output data/catalog/c9300-iosxe26.json
```

### 3. パイプラインを実行する

最初に実行予定を確認します。

```bash
uv run python scripts/pipeline.py plan \
  --catalog data/catalog/c9300-iosxe26.json \
  --data-root data/products/catalyst9300
```

まず1文書を選び、最後まで処理できることを確認することを推奨します。

```bash
uv run python scripts/pipeline.py run \
  --catalog data/catalog/c9300-iosxe26.json \
  --data-root data/products/catalyst9300 \
  --document-id <catalog document_id> \
  --through packaging \
  --resume
```

問題がなければ、未処理文書をまとめて実行します。

```bash
uv run python scripts/pipeline.py run \
  --catalog data/catalog/c9300-iosxe26.json \
  --data-root data/products/catalyst9300 \
  --pending-only \
  --through packaging \
  --resume
```

実行後は、全対象文書の`packaging`が`completed`になったことを確認します。

```bash
uv run python scripts/pipeline.py status \
  --product catalyst9300
```

失敗時の再試行、中断後の再開、OCRの有効化、各オプションについては、
[パイプラインの実行と状態管理](docs/implementation.md#パイプラインの実行と状態管理)を
参照してください。

### 4. Data Packageを検証・索引化する

検証に失敗した場合は索引化へ進まないでください。

```bash
uv run python scripts/validate_packages.py \
  --packages-dir data/products/catalyst9300/packages

uv run python scripts/rag.py index \
  --packages-dir data/products/catalyst9300/packages \
  --qdrant-path data/qdrant \
  --incremental
```

Data Packageを更新した場合も、再検証してから同じ`--incremental`コマンドを
実行してください。

### 5. コンフィグ案を生成する

```bash
uv run python scripts/rag.py generate \
  "Vlan100のDHCP要求を192.0.2.50へ転送してください" \
  --product catalyst9300 \
  --release 26.x \
  --qdrant-path data/qdrant \
  --packages-dir data/products/catalyst9300/packages \
  --output data/evaluation/example.md
```

Markdownレポートには、質問、生成コンフィグ、説明、注意事項、参照根拠、
根拠外の推測、検索結果が含まれます。`--output`へ`.json`で終わるパスを
指定するとJSONを出力します。

`--product`は必須です。`--release`も指定すると、ベクトル検索と字句検索の
両方を対象製品・リリースへ限定できます。

コンフィグ生成の検索計画、根拠検索、生成処理は
[`scripts/config_generation_service.py`](scripts/config_generation_service.py)の
`ConfigurationGenerationService`へ集約しています。CLIはこのサービス層を使用しており、
今後のAPIも同じ入力型と生成処理を共有します。

### 6. 非同期APIからコンフィグ案を生成する

APIは現在、社内・検証用途の非同期MVPです。生成ジョブはSQLiteへ保存されます。
`.env`の`CONFIGBRAIN_API_KEY`へ十分に長いランダム値を設定してください。
APIキーを設定しない場合、APIサーバーは起動しません。

```dotenv
CONFIGBRAIN_API_KEY=<random secret>
```

運用保護を実装していますが、複数利用者向けの権限管理やTLS終端は含まれないため、
インターネットへ直接公開しないでください。

```bash
uv run uvicorn scripts.api:app \
  --host 127.0.0.1 \
  --port 8000
```

#### Web UIを利用する

API起動後、ブラウザで次を開きます。

```text
http://127.0.0.1:8000/
```

画面でAPIキー、要件、製品、IOS XEリリース、知識ポリシー、検索件数を入力すると、
ジョブ受付後の状態を自動確認し、コンフィグ、説明、警告、不足値、未対応要件、
根拠外の補完コマンド、引用を表示します。製品とリリースはドロップダウンで選択し、
製品を変更すると、その製品で索引化済みのリリースだけを表示します。

現在の選択肢:

| 製品 | IOS XEリリース |
|---|---|
| Catalyst 9200 | 26.x |
| Catalyst 9300 | 26.x |
| Catalyst 9500 | 26.x |

選択肢は`data/qdrant/index-manifest.json`の索引済み文書と
`data/catalog/*.json`の製品・リリースを照合して生成します。カタログに存在しても
索引化されていない組み合わせは表示せず、APIへ直接送った場合もHTTP 400で拒否します。

要件欄では、`templates/requirement_templates.tsv`に用意したテンプレートを
選択できます。APIキーを入力して要件テンプレートを開くと候補を読み込みます。
ファイルはUTF-8のTSVで、空行と`#`で始まるコメント行を除き、1行を次の形式で
記述します。

```text
表示名<TAB>要件
```

ファイルを差し替えた後に画面の「再読込」を押すと、APIサーバーを再起動せずに
新しい候補へ更新できます。表示名の重複、空の値、タブが1個でない行は拒否します。

生成完了後は同じ画面に試験フィードバック欄が表示されます。レビュー担当者ID、
匿名化状態、判定、引用の有用性、要件分類の妥当性、期待・修正コンフィグ、
問題分類、危険な誤り、コメントを入力して保存できます。保存先は
`data/evaluation/pilot/feedback.csv`で、ファイルがない場合はヘッダー付きで作成し、
既存行を保持して原子的に更新します。保存後は誤った二重送信を防ぐため入力欄を
無効化します。「保存内容を訂正」を押すと同じ`feedback_id`のレビューを更新できます。
`major_changes`または`unusable`では問題分類が必須です。危険な誤りを`yes`にした
場合はコメントも必須です。

APIキーは`localStorage`や`sessionStorage`へ保存せず、ページ内のメモリだけに
保持します。ページを再読み込みすると再入力が必要です。Web UIも社内試験用であり、
生成結果を実機へ直接投入しないでください。保存後の集計には引き続き
`scripts/report_pilot.py`を使用します。

#### APIを直接利用する

```bash
curl -X POST http://127.0.0.1:8000/v1/config-generations \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: <CONFIGBRAIN_API_KEY>' \
  -H 'Idempotency-Key: ntp-request-001' \
  -H 'X-Request-ID: operation-001' \
  -d '{
    "requirement": "NTPサーバー192.0.2.1を設定してください",
    "product": "catalyst9300",
    "release": "26.x",
    "knowledge_policy": "disallow",
    "top_k": 6
  }'
```

受付時はHTTP 202で`job_id`、`status`、`status_url`を返します。
`status_url`または次のURLを取得してください。

```bash
curl http://127.0.0.1:8000/v1/config-generations/<job_id> \
  -H 'X-API-Key: <CONFIGBRAIN_API_KEY>'
```

状態は`queued`、`running`、`completed`、`failed`のいずれかです。
`completed`では`result`にコンフィグ、説明、引用、警告、不足値、未対応要件、
検索条件、検索根拠が入ります。`failed`では`error_code`と`error`に失敗分類と
理由が入ります。レスポンスの`request_id`と`duration_ms`を監査に利用できます。

「設定例を提示してください」のような参考例の要求では、実投入用の
`configuration`を空に保ち、根拠に記載された例を「そのまま投入不可」の参考情報として
`explanation`へ表示します。Web UIの「引用と検索根拠」から、各根拠のCisco公式
ドキュメントを新しいタブで開けます。

ジョブは既定で`data/api/jobs.sqlite3`へ保存されます。正常終了済みジョブは
再起動後も参照でき、再起動時に残った`queued`ジョブは再開されます。
実行中にプロセスが停止した`running`ジョブは、再起動時に`failed`となります。

任意の`Idempotency-Key`ヘッダーを指定すると、同じキーと同じ入力には既存の
`job_id`を返します。同じキーを異なる入力へ再利用するとHTTP 409になります。
任意の`X-Request-ID`を指定しない場合はUUIDを自動生成し、すべてのレスポンスの
同名ヘッダーへ返します。

運用上限:

- APIキー不正: HTTP 401
- リクエスト本文: 最大64 KiB、超過時はHTTP 413
- `top_k`: 最大20
- 未完了ジョブ: 最大20件、超過時はHTTP 429
- ジョブ実行時間: 最大300秒、超過時は`error_code=timeout`
- 生成ワーカー: 1
- 不正な入力: HTTP 422
- 存在しないジョブ: HTTP 404

監査ログにはリクエストID、ジョブID、製品、リリース、状態、処理時間、
エラー分類だけを記録します。APIキー、要件本文、マニュアル本文、生成結果は
ログへ記録しません。

`GET /v1/configuration-targets`は、Web UIで選択可能な索引済み製品と
IOS XEリリースの組み合わせを返します。

`GET /v1/requirement-templates`は認証後にTSVの要件テンプレートを返します。
`POST /v1/config-generations/{job_id}/feedback`は完了済みジョブのレビューを
既存の試験CSVへ保存します。
`PUT /v1/config-generations/{job_id}/feedback/{feedback_id}`は保存済みレビューを
同じIDのまま訂正します。

生成結果の後処理では、`show`、`clear`、`debug`、`ping`、`traceroute`で始まる
運用コマンドをコンフィグから除外します。「最短」指定で1回しか利用しない
interface-rangeマクロは直接の`interface range`へ正規化します。また、根拠不足を
警告しながらCLIを生成する矛盾がある場合は、安全な空コンフィグとして拒否します。

### 7. APIとCLIの生成結果を評価する

APIサーバーを起動した状態で、合格済みCLI基準10ケースをAPI経由で各3回評価します。

```bash
uv run python scripts/evaluate_api.py \
  --base-url http://127.0.0.1:8000 \
  --baseline-dir data/evaluation/reproducibility-v4 \
  --output-dir data/evaluation/api-equivalence-v1 \
  --runs 3
```

各結果は`<output-dir>/<case>/run-XX.json`へ原子的に保存され、中断後は同じ
CLI基準、API URL、`top_k`、知識ポリシーに一致する結果だけを再利用します。
`summary.json`には合格実行数とケース単位の設定再現性を記録します。

評価項目:

- CLI基準と正規化済みコンフィグが一致する
- 必須CLIが存在し、禁止CLIと根拠外推測がない
- 検索計画にCLI基準の検索条件が含まれる
- 引用が検索結果に含まれ、製品・リリースが分離されている
- 生成拒否／生成成功の判断と拒否理由の有無がCLI基準と一致する
- 同一ケース3回の正規化済みコンフィグが一致する

LLM由来の説明文、警告文、引用順、拒否理由の文言差は許容します。安全な空設定と
拒否理由が維持される限り、不足値と未対応要件の分類差も許容します。

### 8. 限定試験のフィードバックを集計する

対象は社内の3〜5名、期間は2週間、レビュー済みジョブは30件以上を目安にします。
APIの生成結果をそのまま実機へ投入せず、対象機器、稼働リリース、現在設定、
依存機能、到達性、ロールバック手順をレビューしてください。

試験開始時に記録テンプレートをコピーします。

```bash
mkdir -p data/evaluation/pilot
cp templates/pilot_feedback.csv data/evaluation/pilot/feedback.csv
```

1行を1レビューとして、APIの`job_id`と次の評価を記録します。

| 列 | 記録内容・許容値 |
|---|---|
| `feedback_id` | レビューを一意に識別するID |
| `job_id` | APIが返したUUID |
| `reviewer` | 匿名化したレビュー担当者ID |
| `reviewed_at` | ISO 8601形式のレビュー日時 |
| `requirement_anonymized` | `yes` / `no` |
| `verdict` | `ready` / `minor_changes` / `major_changes` / `unusable` |
| `expected_configuration` | 匿名化した期待コンフィグ |
| `corrected_configuration` | 修正が必要な場合の匿名化済みコンフィグ |
| `citations_useful` | `yes` / `no` / `unknown` |
| `requirement_classification_appropriate` | `yes` / `no` / `unknown` |
| `dangerous_or_destructive_error` | `yes` / `no` |
| `issue_categories` | `api;retrieval;generation;requirement;corpus;pipeline`から該当項目を`;`区切りで指定 |
| `comments` | 匿名化した補足 |

要件、コンフィグ、コメントから顧客名、ホスト名、実IPアドレス、認証情報などを
除去してください。集計レポートはこれらの本文を出力しませんが、CSV自体には
記録されるため、CSVとジョブDBをリポジトリへコミットしないでください。

API稼働中は、CSVに記載されたジョブだけをAPIから取得してJSONとMarkdownへ
集計します。APIキーは環境変数、または既定では`.env`の
`CONFIGBRAIN_API_KEY`から読み、コマンドラインやレポートへ出力しません。

```bash
uv run python scripts/report_pilot.py \
  --base-url http://127.0.0.1:8000 \
  --feedback data/evaluation/pilot/feedback.csv \
  --json-output data/evaluation/pilot/report.json \
  --markdown-output data/evaluation/pilot/report.md \
  --require-goals
```

API停止中のオフライン集計では、SQLiteを直接読みます。

```bash
uv run python scripts/report_pilot.py \
  --jobs-db data/api/jobs.sqlite3 \
  --feedback data/evaluation/pilot/feedback.csv \
  --json-output data/evaluation/pilot/report.json \
  --markdown-output data/evaluation/pilot/report.md \
  --require-goals
```

入力不正または存在しない`job_id`は終了コード1、出口基準未達は
`--require-goals`指定時に終了コード2です。危険または破壊的な誤りを1件でも
記録した場合は`safety_stop=true`となるため、試験を停止して原因を確認します。

出口基準:

- レビュー済みジョブ30件以上
- ジョブ成功率95%以上
- 製品・リリース分離違反、引用不整合、根拠外CLIが0件
- 危険または破壊的な誤りが0件
- `ready`または`minor_changes`が80%以上
- 引用有用性と要件分類妥当性がそれぞれ90%以上
- 全要件が匿名化済み

### 9. Cisco公式マニュアルを横断検索する

検索専用モードはLLMを呼び出さず、製品別のQdrant索引を意味検索と
キーワード検索で横断します。結果にはスコア、文書、章・節、原文、
製品、IOS XEリリース、Cisco公式の出典URLが含まれます。

```bash
uv run python scripts/rag.py search \
  "DHCP relayを設定する方法" \
  --qdrant-path data/qdrant \
  --limit 10
```

`--product`、`--release`、`--document`で完全一致の絞り込みができます。
機械処理する場合は`--format json`を指定してください。

## 生成時の知識ポリシー

LLMの事前知識をどこまでコンフィグ生成へ使用できるかは、
`--knowledge-policy`で指定します。既定値は`disallow`です。

| 値 | 動作 |
|---|---|
| `disallow` | 取得した根拠で構文を確認できないCLIを生成しない |
| `limited` | 根拠に対象機能と操作がある場合だけ、一般的なIOS XE構文を補完する |
| `allow` | 要件を満たすためにIOS XEの事前知識を使用できる |

`limited`または`allow`で補完したCLI行は、結果の`inferred_commands`と
`warnings`へ明示されます。

## 出力先

製品ごとに成果物と状態DBを分離します。

```text
data/products/<product>/
├── raw/
├── extracted/
├── ocr/
├── processed/
├── chunks/
├── transformed/
├── packages/
└── state.sqlite3
```

Catalyst 9300が既定の`--data-root`です。別製品を操作するときは、
`--data-root data/products/<product>`を明示してください。

## 実装者向け情報

パイプライン各フェーズの仕様、個別スクリプト、状態管理、障害復旧、
評価方法、テストについては[実装・運用ガイド](docs/implementation.md)を
参照してください。

<!--

githubには中間生成物は置けないため、別マシンで実行するにはデータを渡す必要がある。
対象製品ディレクトリをrsync等で転送するか、dataディレクトリをまるごとtarで固めて転送する。

rsync -av --progress \
  data/products/catalyst9300/ \
  user@fast-machine:/path/configbrain2/data/products/catalyst9300/

別マシンでは uv sync の実行と .env の設定が必要。

-->