# configbrain 実装ガイド

基準日: 2026-11-20

開発・保守の原則、資料準備、個別CLIによる確認、テストと変更手順をまとめる。
コマンドはすべてリポジトリのルートで実行する。
利用者向けの生成・再開手順は[README](../README.md)、
現行仕様は[基本設計](./basic-design.md)・[詳細設計](./detailed-design.md)、
現在の課題と再開地点は[進捗管理](./progress.md)を参照する。

## 1. 開発原則と合格条件

旧`instruction.md`の開発指示を本節へ統合した。
以下には目標要件も含むため、実装済み範囲と混同しない。
特に現行ガイドOKFは公式の章HTMLから生成し、原本PDFに来歴を結び付けている。
PDFからの直接変換ではなく、資料全体の一括切替や内蔵スケジューラーも未提供である。

### 最終目的

Ciscoの公式マニュアルを参照し、日本語で示された要件に対して、根拠を追跡できる有用な
コンフィグ案を生成する。全要求を完全に満たせる根拠がなくても、空の回答で終わらせず、
確認できた設定・根拠・制約・不足情報を示す。

### 成功条件

- Cisco公式マニュアルの更新を定期的に確認し、追加・更新分を検出して処理できる。
- OKFは原本PDFから生成する派生データとして扱い、文書、製品、リリース、ページ、章・Step・表の
  出典位置を保持する。原本PDFを変更しない。
- 検証済みのOKFだけをQdrantに登録する。同じ文書を再処理しても重複せず、更新処理が失敗した場合は
  既存の正常な検索対象を失わない。更新・変換・登録の失敗を利用者が把握できる。
- Qdrantの検索結果は候補の発見に用い、根拠として回答に使う前に原本の出典位置と内容を確認できる。
- 生成案の引用は原本PDFのハッシュを再確認し、引用テキスト断片が同一物理ページにあるか照合する。
  複数候補・未一致は未照合として示し、テキスト照合を意味や適用性の検証と混同しない。
- マニュアルで直接裏付けられるCLIは、要件の一部しか満たさない場合もコンフィグ案として提示する。
- IPアドレスの割当てとIPパケット転送、インターフェイスの管理状態は別の要件として扱う。
  アドレス設定だけから`ip routing`、`no shutdown`、経路やルーティングプロトコルを追加しない。
  実際の転送が必要かを確認し、経路方式は接続先・ネットワーク設計に応じて判断する。
- 要求された設定項目を個別に扱い、`supported`、`partial`、`not_found`、`conflict`、
  `needs_input`のいずれかで状態を示す。未対応項目を警告だけに埋め込まない。
- コンフィグ案の各CLI行に、文書、対象製品・リリース、ページ、章・Step・表などの根拠位置を結び付ける。
- 推論や一般知識による補完は、根拠付きコンフィグへ混ぜず、任意の「推論候補」として分離する。
  候補には適用条件と未確認の前提を分けて示し、適用可能な設定として扱わない。
  静的ルートと動的ルーティングはトポロジーに応じた選択肢であり、一律に両方必要としない。
  追加経路が必要となる宛先範囲など、候補が成立する条件を明示する。
- 結果全体として、利用者が「何を設定でき、何が不足し、次に何を確認すべきか」を判断できる。
  すべての要件が満たされた場合に限って完成と表現する。
- 有用な根拠や制約が見つかった場合に、回答全体を空にしない。直接裏付けられるCLIがない場合も、
  関連する根拠、確認できない点、不足情報、分離した推論候補を提示する。

### 根拠の扱い

- 原本の文書・製品・リリース・ページ・見出し・Step・表の位置を保持する。
- OKFとQdrantの各データから原本PDFの特定箇所まで追跡できるようにする。OKFやベクトル検索結果を
  原本の代替・独立した根拠として扱わない。
- 表は列見出し、行見出し、セルの対応を保って解釈する。手順表はStepの順序と同一手順内の関係を保つ。
- 同じコマンドが別の用途・手順に現れることだけを理由に、根拠を転用しない。
- 値、コマンド、モード遷移が資料から確認できない場合は、その不足を明示し、事実として補わない。
- IP routingの既定状態が資料に記載されていても、IPアドレス設定だけの要件に転送設定を混ぜない。
  転送の要否を確認事項として示し、根拠のない経路やプロトコルを生成しない。
- 根拠の引用と推論候補を区別し、引用箇所から出力CLIまで追跡可能にする。

リファレンス統合後も実行用CLIの根拠はガイドの手順表に限定する。
リファレンスは説明専用とし、保存HTMLのハッシュ・コマンド範囲に照合する。
リファレンスにPDF・OKF・PDFページ対応は要求せず、HTML確認をPDF照合済みと表示しない。
デフォルトの説明があってもCLIを自動省略したり、`no`コマンドを追加したりしない。

### 初期スコープと継続する境界

- まずはCisco Catalyst 9300 IOS XE 26.xの公式マニュアル群を対象にする。他の製品・リリースは含めない。
- 最初の実装は「公式掲載情報の確認 → PDFの追加・更新検出と取得 → OKF変換 → 検証 → Qdrant登録」
  の定期実行可能な取り込みパイプラインとする。
- 更新確認は公式情報を基準とし、文書の識別情報と更新状況を記録する。原本PDFとローカル認証情報は
  Gitに追加しない。
- 変更のない文書は不要な再変換・再登録を避ける。更新分の変換・検証・登録が完了する前に、既存の
  正常な検索対象を無効化しない。
- 取り込みパイプラインの後に、マニュアル群から要件に関連する根拠を検索し、
  「要件ごとの充足判定 → 根拠付き部分コンフィグと不足情報の提示」を評価する。
- 表を含む手順を重視し、更新検知、再実行時の重複防止、失敗時の既存データ保全、各CLI行の根拠、
  未充足項目、推論候補の分離を評価する。
- 実機・仮想機へのコンフィグ投入や本番環境の変更は行わない。

### 開発の進め方

1. 実装前に、公式掲載元、対象文書の識別方法、更新判定基準、更新確認頻度を定義する。
2. 新規・更新・変更なし・取得失敗・変換失敗を含む小さなテストケースを用意する。
3. 取り込みパイプラインを最小限実装し、同じ入力の再実行、失敗時の既存データ保全、
   Qdrant登録結果を検証する。
4. 次に、具体的な日本語要件例と期待する部分出力、根拠位置、未充足項目を定義し、
   マニュアル検索から回答までを評価する。
5. 各段階で必要な自動化・依存関係だけを導入し、評価結果で有用性を確認する。
6. 原本PDFとローカル認証情報をコミットせず、元資料や機器設定を無断で変更しない。

対話と説明は日本語で行う。コード、識別子、ログは英語を基本とする。

## 2. 開発環境

Python 3.12以上、uv、wgetを準備する。依存定義は[pyproject.toml](../pyproject.toml)、
固定された依存解決は[uv.lock](../uv.lock)を使う。

```sh
uv sync --dev
```

LLM接続設定は[READMEのセットアップ](../README.md#セットアップ)を参照する。
単体テストではHTTP・LLM・埋め込みを原則固定応答に置き換える。
実APIによる評価、公式資料の取得、実埋め込みによる索引登録は別の統合確認として扱う。
接続先・認証値をログや文書に保存しない。

## 3. 資料の準備と更新

### 3.1 全体の順序と保存場所

1. 公式掲載情報から候補カタログを作成し、追加・変更・削除を確認する。
2. ガイドの原本PDFを取得・検証する。リファレンスPDFの取得は任意。
3. ガイドをOKF、リファレンスをコマンドJSON・保存HTMLへ変換する。
4. 全資料を検証し、候補DBへガイドの索引を登録する。
5. 代表要件で生成・引用・警告を確認してから資料一式を切り替える。

| 保存先 | 内容 |
| --- | --- |
| `data/catalog/` | 製品・リリースごとのカタログ |
| `data/source_manuals/<document_id>/` | 原本PDF、取得状態 |
| `data/okf/<document_id>/` | ガイド1冊分の`manual.md`、来歴、Data Package、画像 |
| `data/command_references/<document_id>/` | `commands.json`、`html/<chapter_order>.html` |
| `data/qdrant/db/` | 稼働中のローカルDB |
| `data/qdrant/candidate-db/` | 別資料一式の検証用DBの例 |
| `data/runs/<run>/` | 生成パイプラインの実行結果 |

同じ保存先を複数プロセスで更新しない。
ガイド1冊を試験変換して本文・表・CLI・画像・ページ対応を確認してから、残りにも適用する。
生成パイプラインはガイドとリファレンスを両方必須とするため、ガイド1冊の変換だけでは起動条件を満たさない。
登録済みカタログの全資料を揃え、一部の章だけで文書全体を登録済みと見せない。

### 3.2 カタログ探索

初回や探索元を変更する場合は、両文書種別の探索元を明示する。
稼働カタログを直接上書きせず、候補を作る。

```sh
uv run python scripts/discover_manuals.py \
  --product catalyst9300 \
  --release 26.x \
  --configuration-guide-url https://www.cisco.com/c/en/us/support/switches/catalyst-9300-series-switches/products-installation-and-configuration-guides-list.html \
  --command-reference-url https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/command_reference/b_26x_9300_cr.html \
  --output data/catalog/c9300-iosxe26.candidate.json
```

両URLオプションは繰り返し指定でき、一覧ページまたは文書目次URLを受け付ける。
`--seed-url`はガイド探索元の互換用別名。
探索元を省略すると出力先の既存カタログに記録された探索元を再利用する。
**一つでも指定した場合、その指定が探索元の全体になる。**
ガイドだけを指定すれば、既存リファレンスは削除候補になる。
PDF URLは公式目次のリンクから確定し、HTML URLの拡張子置換で推測しない。
取得失敗・種別不一致・必要な目次の欠落時は、既存カタログを更新しない。

### 3.3 PDF取得

```sh
uv run python -m configbrain.download_manuals \
  --catalog data/catalog/c9300-iosxe26.candidate.json
```

省略時はカタログ全体が対象。繰り返し指定可能な`--document-id`で対象を限定できる。
ガイドだけ取得する場合は各ガイドのIDを指定する。
未知のIDは通信前に拒否する。保存先は`--destination`で変更できる。
リファレンスの変換・検索にはPDFを要求しない。

HTTPのETag／Last-Modifiedとローカルハッシュで更新を確認し、
同じ版・ハッシュなら再取得を省略する。更新判定用ヘッダーがなければ再取得する。
一時取得と検証後に置換し、PDF・状態保存の失敗時は既存PDFの復元を試みる。
復元自体の失敗はバックアップの場所とともに通知する。
強制終了に対する2ファイル同時更新の保証ではない。
別URLへのリダイレクトは受理しない。

### 3.4 ガイドの変換

VLANガイドを変換する例。他のガイドもカタログの文書IDを指定して同様に変換する。

```sh
uv run python -m configbrain.convert_manual \
  --catalog data/catalog/c9300-iosxe26.candidate.json \
  --document-id catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc
```

章HTMLから本文を変換し、全章を1つの`manual.md`に保存する。
節・手順見出し、表、コードを保持し、表はHTMLとして扱う。
`provenance.json`にPDF・章HTML・画像の来歴とハッシュ、
`datapackage.json`にリソースを記録する。
取得できない画像は元URLと未取得状態を明示する。
PDFページ対応は一意な章先頭だけであり、各手順の掲載ページではない。
`--source-dir`でPDFルート、`--output`でOKFルートを変更できる。

### 3.5 リファレンスの抽出と確認

```sh
uv run python -m configbrain.convert_manual \
  --catalog data/catalog/c9300-iosxe26.candidate.json \
  --document-id catalyst9300-26-x-b-26x-9300-cr-ee0d2de4d607
```

PDFの事前取得は不要。保存先は`data/command_references/`、
変更する場合は`--reference-output`を指定する。
取得・抽出失敗では旧データを置き換えず、同じ内容の再実行は`unchanged`となる。
未解析・目次不一致は隠さず、確定項目に混ぜない。

ローカル検索の例。外部通信・LLM・Qdrantは使用しない。

```sh
uv run python -m configbrain.search_commands \
  --catalog data/catalog/c9300-iosxe26.candidate.json \
  --document-id catalyst9300-26-x-b-26x-9300-cr-ee0d2de4d607 \
  --command media \
  --mode 'VLAN configuration' \
  --parent vlan
```

```sh
uv run python -m configbrain.search_commands \
  --catalog data/catalog/c9300-iosxe26.candidate.json \
  --document-id catalyst9300-26-x-b-26x-9300-cr-ee0d2de4d607 \
  --command 'switchport access vlan'
```

`--reference-root`で保存ルートを変更できる。モード・親は省略可能だが、
子コマンドの確定には両方が必要。
名前は大文字小文字・連続空白を正規化して完全一致で照合する。
結果は`unique_match`、`candidate`、`ambiguous`、`not_found`、`unparsed`を区別する。
`coverage: partial`は文書内に未解析項目が残っていることを示す。
子の説明と親の全文を保持し、親のデフォルトを子のデフォルトとして流用しない。
保存HTMLのハッシュと再抽出結果を毎回確認するため、名前のJSON照合だけより時間がかかる。

### 3.6 索引登録

```sh
uv run python -m configbrain.index_manuals \
  --catalog data/catalog/c9300-iosxe26.candidate.json \
  --reference-root data/command_references \
  --database data/qdrant/candidate-db
```

混在カタログ全体を検証するが、埋め込むのはガイドだけ。
CPU上の`intfloat/multilingual-e5-small`を使い、資料・索引形式の世代を記録する。
リファレンスJSON・全章HTML・抽出版も世代計算に含む。
新コレクションの点数・IDを検証後にエイリアスを切り替える。
同じ入力での再実行は重複登録せず、登録失敗時は旧索引を保持する。
リファレンスだけのカタログは登録対象にせず、単体のコマンド検索で利用する。

ガイドの別保存先は`--okf-root`・`--source-pdf-root`で指定する。
稼働資料の更新時は、DBだけでなくPDF・OKF・リファレンスも別ルートに揃える。
変更のない資料も含めた一式の検証が必要で、候補DBだけを分けても稼働資料の保護にはならない。

### 3.7 評価と切替

候補カタログ・DB・資料ルートを指定して生成する。

```sh
uv run python -m configbrain.pipeline \
  --requirement 'Catalyst 9300でVLAN120を生成し、GigabitEthernet1/0/1をアクセスポートとしてVLAN 120に割り当ててください。' \
  --catalog data/catalog/c9300-iosxe26.candidate.json \
  --reference-root data/command_references \
  --database data/qdrant/candidate-db \
  --output-dir data/runs/vlan120-candidate
```

保存先を変えた資料は同じルート指定を引き継ぐ。
索引の整合性と、説明・適用条件の品質は別に確認する。
原本・カタログ・派生物・索引の切替は全体トランザクションではない。
生成を止めたメンテナンス中に一式を切り替え、失敗時に戻せる旧一式を保持する。
資料・索引の更新後は新しい出力先で実行し、旧runのハッシュを書き換えて再利用しない。
TLSエラーを証明書検証の無効化で回避しない。

## 4. 個別CLIによる調査

通常利用は[一括生成](../README.md#コンフィグ案を生成する)を使う。
以下は段階ごとの調査用で、外部APIを呼ぶ選定・生成は必要な場合だけ実行する。
例はリポジトリ直下の一時出力を使うため、既存ファイルがあれば別の出力先を指定する。

### 4.1 全カタログからの章選定

```sh
uv run python -m configbrain.select_manuals \
  --catalog data/catalog/c9300-iosxe26.json \
  --requirement 'VLAN 120を作成し、アクセスポートに割り当てる' \
  --output selection.json
```

要件と全カタログの文書・章情報をAPIへ送る。現在の入力はタイトル中心で、本文要約は含まない。
短い選択IDを正規の文書ID・章番号・URLへ解決する。構造違反は具体的なエラーと
前回応答を返して最大3回まで修正するが、関連性・要件分解の正しさを保証するものではない。
通信・認証・途中終了の応答は、この検証修正ループでは再試行しない。
候補のない要件項目も残す。

### 4.2 選定章内検索

```sh
uv run python -m configbrain.search_manuals \
  --selection selection.json \
  --catalog data/catalog/c9300-iosxe26.json \
  --limit 20 \
  --output search-results.json
```

ガイドの選定章だけをローカルQdrantで検索する。クラウドLLMは使わない。
`--limit`は項目×章ごとの最大件数。順位・スコアは根拠の正しさではない。
リファレンス章はQdrantへ問い合わせず、`command_lookup_pending`として保持する。
リファレンスだけの選定ならDB・埋め込みモデルは不要。
DBは`--database`、リファレンスは`--reference-root`で指定できる。

### 4.3 手順全体と注意事項の取得

```sh
uv run python -m configbrain.expand_context \
  --search-results search-results.json \
  --catalog data/catalog/c9300-iosxe26.json \
  --output contexts.json
```

検証済み資料から手順節全体・祖先導入・注意事項候補を取得する。
LLM・埋め込みモデル・Qdrantは使わず、文字数での切り捨てもしない。
ガイド手順の構文から同一資料一式のリファレンスを補完し、選定外の取得理由を区別する。
文脈の本文・行範囲・資料ハッシュを保持するが、ここでは引用のPDF本文照合は未実施。
節外参照は自動追跡せず、注意事項の収集は適用性の証明ではない。
`--okf-root`・`--source-pdf-root`・`--reference-root`で保存先を変更できる。

### 4.4 設定案の生成と表示

```sh
uv run python -m configbrain.generate_config \
  --contexts contexts.json \
  --search-results search-results.json \
  --catalog data/catalog/c9300-iosxe26.json \
  --output config-draft.json

uv run python -m configbrain.report \
  --draft config-draft.json \
  --output report.md
```

生成は要件・本文をAPIへ送る。元の検索JSONから文脈を再取得して一致を確認するため、
カタログ・元資料も必要。
`--generation-rounds`は既定3で、CLI生成、各項目の補足、意味審査に別々に適用する。
生成後にガイドPDF・保存HTMLの引用を照合し、説明の主張と適用条件を別に審査する。
未解決の説明は保留し、検証済みCLIを保持する。
API呼び出しの上限と状態規則は[詳細設計](./detailed-design.md)を参照する。
reportは保存結果の表示だけであり、原本や意味を再検証しない。

選定・検索・文脈・生成の単体CLIは、`--output`省略時に標準出力へJSONを表示し、
指定時は成功した場合に既存ファイルを置き換える。
既存出力を拒否する一括pipelineとは異なるので、調査時は保存先を確認する。
引数・既定値の一覧は[詳細設計のCLIインターフェース](./detailed-design.md#12-cliインターフェース)、
各コマンドの`--help`も参照する。

## 5. テストと品質評価

変更に直接関係する既存テストをまず実行する。テスト一覧は[tests](../tests/)を参照。

```sh
uv run pytest -q tests/test_select_manuals.py
```

生成・引用・説明審査をまたぐ変更の関連試験例:

```sh
uv run pytest -q \
  tests/test_semantic_review.py tests/test_generate_config.py \
  tests/test_index_manuals.py tests/test_report.py \
  tests/test_pipeline.py tests/test_verify_pdf.py
```

全体の回帰確認が必要な場合:

```sh
uv run pytest -q
```

専用の型検査器・lintツールは現在の依存定義にない。
モック試験の成功を実LLMの正解率や実機の動作確認とは扱わない。
意味審査では、VLAN groupの誤転用、VTP条件の欠落、`no shutdown`とLayer3化の混同に関する
人手ラベル正例・負例を使い、不当支持・不当な適用確定・過剰保留を区別する。
`semantic_review.review_metrics`の回帰ゲートは全ラベル一致かつ不当承認ゼロで、
全件保留でも合格しない。
過去の試験結果と実LLM評価は[進捗管理](./progress.md)に日付・条件付きで記録する。

## 6. 変更と文書更新

1. [進捗管理](./progress.md)の現在地・次の着手点を確認し、未コミット変更を一括で巻き戻さない。
2. 現行契約と変更案を分け、入力・出力・失敗時の挙動と受け入れ条件を決める。
3. 関連する既存の抽出・検証ヘルパーを使い、CLIの構文解析等を二重実装しない。
4. 根拠IDの項目境界・CLIの出所・未確認状態を維持し、必要な最小範囲を変更する。
5. 変更に関係する試験と、必要なら保存済み入力の読み取り専用確認を行う。
6. CLIや操作を変えたら本書と必要な利用者向けREADME、仕様を変えたら基本・詳細設計を更新する。
7. 完了範囲・検証条件・残課題・再開地点を進捗管理に記録する。

過去runを新仕様に合わせて書き換えない。警告付きで完了した生成は通常の`--resume`で再生成されないため、
改善の比較評価には新しい出力先を使う。
説明用の生応答・反証は採用済みCLIや引用の構造から分離する。
根拠のないCLIを生成可能にしたり、未知IDを推測補正したりして試験を通さない。
