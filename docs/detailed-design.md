# configbrain 詳細設計書（現行実装）

基準日: 2026-11-20

## 1. 対象と参照関係

本書は[基本設計書](./basic-design.md)を具体化した現状仕様である。
改善候補・評価履歴・再開地点は[進捗管理](./progress.md)、
資料準備・個別CLI・テスト手順は[実装ガイド](./implementation-guide.md)にまとめる。
以降の「検証」は、特に明記しない限りプログラムによる形式・整合性検証を指す。
LLMによる意味判断、PDFとの文字列照合、実機での動作確認とは区別する。

## 2. 構成要素と責務

| モジュール | 主な入口 | 責務 |
| --- | --- | --- |
| [discover_manuals](../scripts/discover_manuals.py) | `discover_catalog` | 公式掲載元の探索、カタログ差分、保存 |
| [download_manuals](../configbrain/download_manuals.py) | `download_manuals` | PDF更新確認・取得、取得状態の保存 |
| [convert_manual](../configbrain/convert_manual.py) | `convert_manual` | 文書種別分岐、ガイドOKF変換、パッケージ検証・昇格 |
| [command_references](../configbrain/command_references.py) | `convert_command_reference`、`CommandReferenceIndex` | リファレンス抽出・保存・再検証・名前照合 |
| [index_manuals](../configbrain/index_manuals.py) | `load_validated_corpus`、`index_manuals` | 資料検証、ガイド分割・埋め込み・世代切替 |
| [select_manuals](../configbrain/select_manuals.py) | `select_manuals`、`request_json` | 要件分解・章選定、共通のLLM API通信 |
| [search_manuals](../configbrain/search_manuals.py) | `search_manuals` | 選定章を条件としたガイド検索 |
| [expand_context](../configbrain/expand_context.py) | `expand_context` | ヒット検証、全文脈取得、リファレンス補完 |
| [guide_commands](../configbrain/guide_commands.py) | `procedure_rows`、`command_contexts` | 手順表の構文・引数・モード・親の抽出 |
| [generate_config](../configbrain/generate_config.py) | `build_evidence`、`validate_draft`、`generate_config` | 根拠台帳、CLI生成・修正、状態・依存グラフの組立て |
| [reference_evidence](../configbrain/reference_evidence.py) | `register_reference_evidence`、`verify_reference_citations` | 説明用根拠台帳、HTML引用照合 |
| [verify_pdf](../configbrain/verify_pdf.py) | `verify_pdf_citations` | 引用単位のPDF文字列照合 |
| [semantic_review](../configbrain/semantic_review.py) | `review_explanations`、`validate_saved_reviews` | 主張審査・説明修正・保存結果検証・評価集計 |
| [report](../configbrain/report.py) | `render_report`、`load_report` | 保存結果の検証とMarkdown表示、失敗診断 |
| [pipeline](../configbrain/pipeline.py) | `run_pipeline` | 事前確認、5段階の実行・保存・再開 |
| [search_commands](../configbrain/search_commands.py) | CLI | リファレンスの単体検索用入口 |

CLI群はファイルを介して接続する。生成用の5段階は単体CLIとしても実行できる。
単体実行では統合パイプラインの全事前確認が必ず実行されるとは限らない。

## 3. 保存構造とデータ契約

### 3.1 既定配置

| 配置 | 内容 |
| --- | --- |
| [data/catalog/](../data/catalog/) | 文書カタログ。既定は`c9300-iosxe26.json` |
| `data/source_manuals/<document_id>/` | 原本PDF、`.download-state.json` |
| `data/okf/<document_id>/` | `manual.md`、`provenance.json`、`datapackage.json`、取得画像 |
| `data/command_references/<document_id>/` | `commands.json`、`html/<chapter_order>.html` |
| `data/qdrant/db/` | ローカルQdrantの永続データ |
| `data/runs/<run>/` | 5段階の成果物と`pipeline-state.json` |

原本・派生物・runはGit管理外とする。保存パスを含む出力の移動可能性は保証しない。
以下のJSON一覧は主要項目の説明であり、追加項目を省略した模式定義である。

### 3.2 カタログ

現行出力は`schema_version: 2`。旧バージョン1も読める入口がある。
製品・リリース、探索元、文書配列を持つ。
文書は`document_id`、`document_type`、文書名・分類、PDF URL・ファイル名、
HTML目次URL、章の`order`・`title`・`url`等を持つ。

`document_type`は`configuration_guide`または`command_reference`。
文書IDは製品・リリース・PDF URLに由来する安定IDで、LLMが生成する値ではない。
カタログのファイルバイト列のSHA-256を後段へ引き継ぐ。

### 3.3 ガイドOKF

- `manual.md`: 文書情報、章、公式HTMLから変換した本文。表はHTMLとして保持する。
- `provenance.json`: 文書・製品・リリース、PDF URL・SHA-256・ページ数、
  章HTMLのURL・ハッシュ、章先頭PDF対応、画像取得状態とハッシュ、変換統計。
- `datapackage.json`: Frictionless Data Packageのリソース一覧とハッシュ・サイズ等。

保存単位はガイド1冊であり、全章を1つの`manual.md`へ連結する。
本文内部の節・手順構造と、索引のチャンク単位、選定の章単位は区別する。

PDFページ番号は1始まりの物理ページである。
章タイトルとCHAPTER標識の一意な一致だけを章先頭として採用する。
章先頭対応を、そのまま個別手順の掲載ページとして扱わない。

### 3.4 リファレンスJSON

`schema_version: 1`、`extractor_version: 1`。
文書情報に加え、章配列に保存HTMLのファイル名・SHA-256・解析状態を保持する。
コマンドは`command_id`、名前、モード、出典、`content_html`、節、子コマンド、
未解析情報、目次照合状態等を持つ。

出典には章URL・アンカー・DOM位置を含む。親の全文と、節・子の範囲を関連付ける。
子コマンドの抽出範囲は`explicit_lists_only`で、すべての構文を解析する文法器ではない。

### 3.5 run成果物

| ファイル | 主な内容・接続情報 |
| --- | --- |
| `selection.json` | 製品・リリース、全体要件、モデル、カタログハッシュ、項目別候補章、選定試行・エラー |
| `search-results.json` | schema 1、選定ハッシュ、索引世代、モデル、項目・章・ヒット・スコア・来歴 |
| `contexts.json` | 検索結果を拡張し、検索ハッシュ、文脈方針版、ガイド全文脈、リファレンス照合結果を追加 |
| `config-draft.json` | schema 1、根拠台帳版、状態、項目、引用、依存グラフ、生成・補足・意味審査履歴 |
| `report.md` | 人向けの結果、または処理停止時の診断 |
| `pipeline-state.json` | schema 1、入力条件、完了段階・ファイルハッシュ、各段階状態、警告・失敗理由 |

選定JSONには他の段階と共通の`schema_version`があると仮定しない。
生成JSONは全`evidence_atoms`をそのまま保存せず、採用引用と審査用根拠等を保存する。

## 4. 取り込み処理

### 4.1 資料探索

公式ガイド一覧・リファレンス目次等を探索元として登録する。
URL、文書種別、製品・リリース、PDFリンクと章目次を検証する。
PDF URLは拡張子置換だけで推定せず、掲載リンクから確認する。
同じ出力先の既存カタログと比較し、追加・変更・削除を報告する。
取得・解析失敗時に、不完全なカタログで既存カタログを置き換えない。

探索元をすべて省略すると既存カタログの探索元を再利用する。
一つでも指定した場合は、その指定一式を探索元全体として扱う。
ガイドだけを指定すると、以前のリファレンスが削除候補となり得る。

### 4.2 PDF取得

1. HEADでETag、Last-Modified、Content-Lengthを取得する。
2. ETag優先、なければLast-Modifiedで前回状態と比較する。
3. ローカルのサイズ、PDF先頭、SHA-256も一致した場合だけ`unchanged`とする。
4. 更新識別ヘッダーがない場合は再取得する。
5. wgetで一時ファイルへ取得する。HTTPS限定、リダイレクト不許可、最大3試行。
6. PDF先頭・サイズ・ハッシュを確認し、PDFと取得状態を置き換える。
7. 保存失敗を検出した場合は既存PDFを復元する。復元失敗時はバックアップ場所を残して通知する。

PDFと状態の二ファイルを、強制終了を含めて不可分に更新する保証はない。
同じ保存先への並行実行も想定しない。

### 4.3 変換と昇格

ガイドの場合は原本PDFが必要。章HTMLを取得し、表・整形済みブロック・画像を保持して
Markdown化する。画像取得状態や章先頭の未対応も来歴へ記録する。
一時ディレクトリでパッケージを組み立て、Frictionless検証後に昇格する。
既存と一致すれば出力置換を省略する。既存正常パッケージの保全は文書単位で行う。

リファレンスの場合はPDF不要で別出力先へ分岐する。
章直下の`article.reference`等の構造を確認し、コマンドを抽出する。
入れ子・重複アンカー・構造欠落等は未解析として記録し、確定コマンドに見せない。
有効なコマンドが一つもなければ変換を失敗させる。
保存HTMLを含む一時パッケージを作成し、比較・昇格する。

### 4.4 読み込み時の検証

ガイドはData Package、文書ID、製品・リリース、PDFハッシュ、章来歴、画像等を検証する。
リファレンスはメタデータ・解析器版・章数・保存HTMLハッシュに加え、
HTMLを再抽出した結果と保存JSONの一致を確認する。
解析が一部不完全でも不完全性を保持して読み込めるが、不確定な検索結果は採用しない。

### 4.5 索引作成・世代切替

- 埋め込みモデル: `intfloat/multilingual-e5-small`。
- ローカルQdrant、Cosine距離、既定エイリアス`c9300_iosxe26_manuals`。
- ガイドだけを登録する。リファレンスも資料世代の計算には含む。
- 見出し・段落・表の行等を抽出し、出典参照を保持してモデルのトークン上限内へ分割する。
- passage接頭辞と文脈を付ける。上限を超えたまま黙って切り捨てない。
- 世代は索引形式版、モデル名、ガイドPDF・章HTML、リファレンスJSON・HTML等から計算する。
- ポイントIDは世代・文書・チャンク内容等から決定し、payloadに章・節・出典・世代を持たせる。

新コレクションへ登録し、件数・ID集合を検証してからエイリアスを切り替える。
同じ索引が存在すれば再登録を省略する。
切替成功後に旧コレクションを削除するため、世代別索引の長期履歴は保持しない。
索引形式版は現在3である。

## 5. 生成パイプラインの制御

### 5.1 入口と事前確認

`run_pipeline`は要件、出力先、資料・DBの各パス、検索件数、生成ラウンド数、再開指定を受け取る。
空要件、不正な件数・ラウンド数、資料やDBの内部への出力、入力との衝突等を拒否する。

LLM設定読み込みより前に、次を検査する。

1. 同一対象のガイド・リファレンスがカタログに両方存在する。
2. 資料一式を現在の検証器で読み込める。
3. 既存DBと有効エイリアスがある。
4. 全ポイントが現在資料の世代に属し、各ガイドのポイントが存在する。

失敗時はpreflightの診断を保存し、ガイドのみ等の代替経路で続行しない。
この確認は、各章に必要な手順がすべて揃うことや索引の全ポイント完全性までは保証しない。
索引構築時の件数・ID集合の検証とは役割が異なる。

### 5.2 選定

全カタログの文書・章情報を渡す。ベクトル検索で事前に章候補を削らない。
入力は製品・リリース・要件、文書のタイトル・分類・種別、
章の短いID・タイトル。節・手順の見出し、本文要約、収録コマンドは含まない。
所在付きの意味メタデータによる強化は[進捗管理](./progress.md)の未実装計画である。
LLMの章指定は、呼び出し時に割り当てた`c1`等の短いIDを用いる。
応答は要件、非空の不確実性、候補の`chapter_id`・理由・不確実性に制限する。
存在するIDか厳密に検査し、正規の文書ID・章番号・URLへ変換する。
類似IDの曖昧補正はしない。

構造検証失敗時は、エラーと前回の解析済み応答を渡して最大3回まで試みる。
通信失敗はこの選定検証ループでは再試行しない。
保存される候補は正規情報であり、短いIDを永続参照にはしない。

### 5.3 選定章内検索

ガイドの候補章ごとに、製品・リリース・文書・章で検索を限定する。
項目要件をquery形式へ変換し、ローカルモデルで埋め込む。IPv4 CIDRの検索補助も行う。
既定で項目×選定章あたり最大20ヒットとし、スコアを事実認定には使用しない。
章の未登録、payloadの不一致、索引世代混在、不正なベクトル・スコアはエラーとする。

選定されたリファレンス章は`command_lookup_pending`として記録し、
Qdrant検索を行わず後段のガイドコマンド照合を待つ。

### 5.4 文脈展開とコマンド補完

検索結果のカタログ・モデル・資料世代・ヒット出典を現在資料と検証する。
ガイドヒットから、該当手順全体、祖先節の導入、章内の注意事項候補を収集する。
別タスクの注意事項を無条件に混ぜず、採用した範囲には節・章内行・OKF内行を付ける。
節外への相互参照は自動追跡しない。注意見出しの検出も適用性の証明ではない。

手順表のコマンド列から構文・リテラル接頭辞・引数を抽出する。
モードは例のプロンプトやPurposeのモード遷移から得るが、例の値を要件値にはしない。
同一表内でモード・親を追跡し、不一致や不明な遷移では確定を解除する。

コマンド名を、登録リファレンスの名前のうち一致する最長の接頭辞へ対応付ける。
選定章だけでなく同じ資料一式のリファレンスへ補完検索する。
結果は`unique_match`、`candidate`、`ambiguous`、`not_found`、`unparsed`で区別する。
一意で、必要なモードと親が確定したものだけを説明用に採用する。
子コマンド単体はモード・親がなければ確定しない。

文脈方針版は現在3。generation開始時にも文脈を再構成し、入力JSONとの完全一致を要求する。

## 6. 根拠台帳とCLI生成

### 6.1 台帳

`build_evidence`は次を内部生成する。

| 要素 | 内容 |
| --- | --- |
| `sources` | 文書・章・節・行・PDF／HTML来歴と対象文脈 |
| `evidence_atoms` | 引用できる本文、位置、PDF照合用断片 |
| `commands` | 手順表の構文、引数、出典・引用 |
| `items` | 項目ごとに使用可能なsource・command・evidence ID集合 |
| `normalized_ipv4_networks` | 要件に明記されたCIDRの決定的な計算結果。資料根拠ではない |

ガイドの根拠IDは`evidence-v1-<sha256>`。
文書、PDF・HTMLハッシュ、章・節・文脈行、位置、本文等に由来し、単なる配列番号ではない。
`command-1`や`source-1`等のローカルIDとは区別する。
引用原文と位置はプログラムが台帳から付与する。

リファレンスは`reference-source-<sha256>`と`reference-evidence-v1-<sha256>`を用いる。
`evidence_role: explanation_only`を持ち、ガイドのCLI台帳へは混ぜない。

### 6.2 CLI応答と値の検証

応答は項目ごとのstatus、assessment、4種類のコマンド配列、説明・制約、不足・仮定、
保留手順、推論候補、暫定依存、全体のnext_checks等を持つ。

コマンドは`command_id`、1行の`cli`、`bindings`を持つ。
bindingの値は要件のリテラル値または`<INTERFACE_ID>`等のプレースホルダーに限定する。
出典例の値や一般知識による補完は認めない。
整数値は実装上文字列へ正規化する。インターフェース名の大文字小文字は要件表記へ復元し記録する。

手順構文の必須選択・任意選択・引数とCLIを照合し、項目外のIDや構文逸脱を拒否する。
IPv4 CIDRからのマスク変換は、明記された同一IPアドレスとの組合せに限り許可し、計算来歴を残す。
これは資料に書かれていないネットワーク値の推定ではない。

| 配列 | 内容 |
| --- | --- |
| `configuration` | 未解決値のない設定案 |
| `templates` | 未指定値を含む設定ブロック。解決前に適用しない |
| `verification` | 値を確定できた確認用コマンド |
| `verification_templates` | 未指定値を含む確認ブロック |

showコマンドは確認側へ分類する。テンプレートには必要な固定行・モード遷移も含め、
未解決のブロックから固定部分だけを設定案として抜き出さない。
全般的なCLI文法・モード遷移の完全な形式検証器ではない。

### 6.3 項目状態・安全側の後処理

`needs_input`には不足情報、`conflict`には引用付き制約等を要求する。
仮定や未解決入力、必須の保留手順がある項目を`supported`としない。
直接の根拠がある部分出力を、別の部分が不正なことだけで全削除しない。
個別に通った要素を残す場合は検証エラーと生成未完了を記録する。

明示的な要求がない`no shutdown`は保留する。
`no switchport`に文書化された既存設定への影響、FortyGigabitEthernetの実機適合、
IPアドレス割当てとIP転送の区別等に関する確認事項を追加する。
これらは限定的なルールであり、すべての機種・コマンドの安全性を自動判定しない。

### 6.4 依存関係と再生成

LLMが返す`depends_on`は仮説として扱う。
同一の手順表にあるコマンドの出典順で裏付けられる依存だけを保持する。
要件文から連想した依存や、別手順の順序をそのまま確定しない。
依存グラフ・所有項目・共有モード遷移等を構築し、逆向き・循環・重複等を警告する。

初回は各項目を別依頼とし、その後は生成エラーや整合性警告に関係する項目を
依存・警告の関係でグループ化して修正依頼する。
前回応答と検証結果を渡す。上限は既定3ラウンドで、進展のないグループは止める。
`generation_requests`、`validation_repairs`、`review_rounds`等に記録する。

この依存検証は全ネットワーク依存関係を証明するものではない。
後段の意味修正で依存グラフや採否は更新しないため、説明との整合は現在の改善対象である。

## 7. リファレンス補足と引用照合

### 7.1 補足生成

CLI生成後、既存plan、同一項目のガイド・リファレンス根拠と全文脈を別リクエストへ渡す。
受け付ける応答は`reference_notes`だけで、各補足は種類、説明、条件、根拠IDを持つ。

- 種類: `default` / `mode` / `guideline` / `conflict` / `not_applicable`。
- 全補足に同一項目のリファレンス根拠を必須とする。
- ガイドの補助引用を許可するが、ガイドだけの補足は拒否する。
- conflictは両種類の根拠、defaultは条件を必須とする。
- 重複・項目外・不明なID、CLI変更等を拒否する。

既定最大3回。注記番号・違反ID・理由を集約し、前回応答とともに修正へ返す。
通信失敗があっても直前の意味ある検証フィードバックを保持する。
上限後はCLIを残し、補足失敗を記録する。
`reference_status`は`complete` / `failed` / `not_available`で、意味的正しさの判定ではない。

### 7.2 HTML引用照合

引用のID・本文・位置・説明専用ロールを台帳と比較する。
保存HTMLのハッシュと解析器版を検査し、コマンドを再抽出する。
親のHTML・出典・節／子の引用範囲を照合し、一致した引用に`html_verification`を付ける。
リファレンスに`pdf_verification`を付与しない。

### 7.3 PDF引用照合

1. itemsとrequirement_graph内の引用を収集し、同一IDで異なる本文を拒否する。
2. 台帳の本文・source IDとの一致を検査する。
3. ガイドの章先頭ページと次章の対応から、検索可能な章範囲を決める。
4. PDFハッシュを検査し、ページごとの抽出文字列を正規化する。
5. 引用を構成する断片がすべて同じ物理ページに存在するか検査する。

正規化は英数字等を残して大小文字を吸収する方式で、表の視覚構造は復元しない。
表はセル等に由来する断片を用いる。1引用の断片を複数ページにまたがって合算しない。
ページ範囲を確定できない場合も照合済みとはしない。

| 引用単位の状態 | 意味 |
| --- | --- |
| `matched` | 同一ページ上で一意に一致 |
| `ambiguous` | 複数ページが一致候補 |
| `not_found` | 検索範囲内に全断片が揃うページがない |
| `unmapped` | 章範囲等を安全に決定できない |

ガイド引用が全件matchedなら全体`verified`、一部未確定なら`partial`、
対象がなければ`not_verified`とする。PDFのハッシュ不一致・読み取り失敗等は停止エラーになる。
not_foundは原文の不存在やCLIの誤りを直接意味しない。

## 8. 主張の意味審査

### 8.1 対象と入力

対象はassessment、evidence、constraints、reference_notesの説明と条件、
withheld_steps.reason。空の説明を除きローカルなtarget IDを割り当てる。
文の区切りでunit IDを作り、複合文はLLMへ原子的主張への分解を要求する。
日本語の句点等と一部の英文境界を使う単純な分割であり、構文解析器ではない。

各項目の全対象根拠本文と位置、ガイドの親文脈、リファレンス親コマンドを含む原文の根拠、
要件、仮定、不足情報を渡す。対象根拠の本文を黙って切り詰めない。
PDF照合用の重複断片等は審査入力から除く。
対象は「取得できた文脈」であり、未取得の全マニュアルを審査するわけではない。

assumptions、missing_informationは入力条件として使うが、独立した審査対象には分解しない。
next_checks、推論候補、依存グラフの全説明もこの審査の直接対象ではない。

### 8.2 応答と検証規則

```json
{
  "reviews": [{
    "unit_id": "u1",
    "claims": [{
      "text": "検証対象原文の一部",
      "target": "対象",
      "operation": "操作",
      "support": "supported",
      "applicability": "conditional",
      "conditions": ["未確認の機器条件"],
      "reason": "判定理由",
      "evidence": [{"evidence_id": "...", "quote": "根拠内の正確な部分文字列"}]
    }]
  }]
}
```

- 全unitを重複なく審査し、claimのtext連結が元unitを完全再現することを要求する。
- 支持軸: `supported` / `contradicted` / `insufficient_evidence`。
- 適用軸: `confirmed` / `conditional` / `unknown`。
- confirmedは要件上適用可であり、実機を確認したという意味ではない。
- conditionalとunknownは、支持軸にかかわらず非空のconditionsが必須。
- supportedとcontradictedは原文の証明断片が必須。
- IDは同じ項目の対象根拠、quoteはその根拠本文の完全な部分文字列に限定する。
- supportedは、その説明が実際に添付した引用だけを証明に使用できる。
- assessmentは項目内で添付済みの引用集合を使用する。
- 未引用根拠は反証に使える。支持に必要なら根拠不足として引用修正へ回す。

一つでも構造違反があればその応答全体を受理しない。
これは全文被覆・ID・原文一致の検査であり、分解の原子性や推論の正しさを機械的に証明しない。

### 8.3 説明修正と終了

受理した審査に不支持の主張があれば、そのtargetだけを修正依頼する。
応答はreplacementsとunresolved_target_idsに限定し、対象の削除・欠落・重複を認めない。
assessmentと保留理由は本文だけ、ガイド説明は本文・ガイド引用、
リファレンス補足は種類・本文・条件・適格な引用だけを変更できる。

修正候補を引用規則で検証し、HTML／PDFを再照合してから採用し、意味審査をやり直す。
CLI・束縛値・項目状態・依存・手順採否は修正対象外。
generationの最後にも4種類のCLI配列の不変を確認し、引用を再照合する。
引用の原本整合を壊す失敗は、通常の意味審査失敗と違い生成の停止エラーになる。

ラウンド上限Nに対し、項目ごとに審査最大N回、修正最大N−1回。変更なしなら早期停止する。
未解決の適用条件だけなら説明を条件付き表示とし、CLI修正は行わない。
審査エラー・不支持が残れば保留表示とし、全体生成partial・充足undeterminedにする。

**現在は審査応答の形式検証が完了しない限り、説明修正に進めない。**
demo24の条件欠落や大量の単位欠落は、この境界で止まった実例である。

### 8.4 保存・表示時検証

| 生成JSONルートの項目 | 内容 |
| --- | --- |
| `semantic_reviews` | 項目ごとのtargets・units・受理済みclaims、状態、モデル、入力文脈指紋 |
| `semantic_review_evidence` | 受理済み主張の判定に使用した原文の台帳エントリ |
| `semantic_review_requests` | 項目・試行・review/repair、入力ハッシュ、モデル、解析済み応答、結果・エラー |

審査状態はcomplete / issues_found / failed / not_required。
`semantic_review_error`と`semantic_review_pending`を項目へ反映する。
受理されなかった生の応答は、承認されたclaimsへ移さない。

raw応答と審査用の反証はルートへ隔離し、items等の採用引用を再帰走査する処理と分離する。
審査用の証明断片自体を、新たな採用済みPDF引用として表示しない。
レポート表示時は説明・条件・引用指紋、要件・仮定・不足情報・source文脈指紋、
主張の被覆・状態と項目フラグの整合を検証する。
これは保存JSON内の整合確認であり、元資料の再読み込みではない。

## 9. 状態集約とレポート

### 9.1 充足判定の優先順

1. CLI・補足・意味審査のエラー、または項目status未確定があれば`undetermined`。
2. 項目間警告、資料間conflict、意味審査の適用条件未確定があれば`incomplete`。
3. 全項目supportedで、不足情報・仮定・テンプレート・必須保留がなければ`complete`。
4. それ以外は`incomplete`。

生成状態partialにはCLI・補足・意味審査のエラーと整合性警告を反映する。
適用条件未確定だけの場合は生成completeのままでも、充足とパイプラインに警告を反映する。
PDF partialもパイプライン警告になるが、単独で充足判定を書き換えない。

### 9.2 表示

4種のコマンドブロック、各行の構文ID・引用、項目状態、不足情報、仮定、制約、補足、
根拠説明、保留手順、推論候補、依存グラフ、履歴と出典一覧を表示する。
Markdown・HTMLとして解釈され得るテキストをエスケープする。
リファレンスを実行用CLIの根拠へ混ぜた出力は拒否する。

審査対象の説明は、支持・要件上適用可、支持・条件未確認、保留を区別する。
一つのtargetに不支持のclaimがあれば、target全体を保留として元説明を引用表示する。
未受理の審査は「正しい／誤り」と断定せず検証未完了とする。
意味審査メタデータのない旧JSONは従来表示し、審査済みとは表示しない。

## 10. API呼び出しと資源制御

共通通信は[select_manuals](../configbrain/select_manuals.py)の`request_json`が担当する。
接続設定は`CLOUD_GPT_API_URL`、`CLOUD_GPT_API_KEY`、任意の`LLM_MODEL`。
環境変数とローカルdotenvを読み、HTTPS・URLの妥当性を検査する。

温度0、JSON object形式、system/userメッセージ、api-keyヘッダーを使用する。
既定タイムアウト180秒、HTTPリダイレクト不許可。
非2xx、stop以外の終了理由、JSON不正、不正なモデル情報はエラーにする。
共有通信関数に自動バックオフや総API予算・トークン予算の制御はない。
再試行の範囲は選定・生成・補足・意味審査の各ループによって異なる。

項目数M、補足対象R、上限Nに対し、選定は最大3回、
CLI生成は最大M×N回、補足は最大R×N回、意味処理は最大M×(2N−1)回が概算上限となる。
CLI修正グループがまとまる場合や進展なし停止では減る。
N=3で全項目が補足対象なら概算上限は3+11M回であり、「3ラウンド＝API3回」ではない。
`generation_rounds_used`はCLI生成側のラウンド数で、補足・意味審査の総回数ではない。

検索用埋め込みには要件長の検査があるが、LLM処理全体を入力トークン量で自動分割する仕組みはない。
大きな根拠集合を一括送信する現状は、応答欠落・遅延・費用の改善対象である。

## 11. チェックポイント・失敗処理・互換性

段階状態はnot_started / running / completed / completed_with_warnings / failed。
成果物は一時ファイルから置換し、完了時にハッシュを記録する。
各成果物とチェックポイントを一括で不可分に更新する仕組みやrun全体の排他ロックはない。

再開時は次を確認する。

1. 要件、カタログハッシュ、資料・DBパス、検索件数、ラウンド数等が同じである。
2. 完了段階が先頭から連続し、各ファイル名・存在・ハッシュが一致する。
3. 現在の資料・索引による事前確認に合格する。
4. 保存済み文脈の方針版が一致し、現在資料からの再構成結果と等しい。

根拠台帳版やPDF照合版の不一致ではgenerationとreportを再実行する。
文脈方針版の変更は、新規runを要求する。
モデル設定・すべてのプロンプト・コード全体の版を、再開条件として一律には固定していない。
意味審査を後から追加しても、既存の完了generationを必ず再審査するとは限らない。

| 失敗・警告 | 現在の扱い |
| --- | --- |
| 資料欠落、索引世代不一致、入力不整合 | 停止。診断レポート |
| 選定・検索・文脈の停止エラー | 段階失敗。以前の完了成果物を保持 |
| CLI応答の一部不正 | 有用な検証済み部分を保持し、限定再試行。残件は生成未完了 |
| 補足・意味審査が未解決 | CLIを保持し警告付きレポート。意味審査対象は保留等を表示 |
| PDFの複数一致・未一致 | 引用未確定の警告。PDFハッシュ不一致とは区別 |
| 原本引用の破損、表示契約の不整合 | 生成またはレポート段階を停止 |

停止時の診断レポートには未検証CLIを埋め込まない。
警告付きgenerationが完了済みなら、通常のresumeで補足や審査を再試行しない。
単体reportの再表示は元資料・LLMを使わないが、pipelineのresumeは事前確認を伴う。

## 12. CLIインターフェース

一括生成・再開は[README](../README.md)、
資料準備と個別CLIの操作例は[実装ガイド](./implementation-guide.md)を参照する。

| 入口 | 主な必須引数 | 主な既定値・追加引数 |
| --- | --- | --- |
| `uv run python scripts/discover_manuals.py` | product、release、output | configuration-guide-url / command-reference-url（繰り返し可）、seed-url互換 |
| `uv run python -m configbrain.download_manuals` | catalog | destination=`data/source_manuals`、document-id（対象限定） |
| `uv run python -m configbrain.convert_manual` | catalog、document-id | source-dir=`data/source_manuals`、output=`data/okf`、reference-output=`data/command_references` |
| `uv run python -m configbrain.index_manuals` | なし | catalog・資料各root、database=`data/qdrant/db` |
| `uv run python -m configbrain.select_manuals` | requirementまたはrequirement-file | catalog、product、release、output |
| `uv run python -m configbrain.search_manuals` | selection | catalog、database、reference-root、limit=20、output |
| `uv run python -m configbrain.expand_context` | search-results | catalog、okf-root、source-pdf-root、reference-root、output |
| `uv run python -m configbrain.generate_config` | contexts | search-results、catalog、資料各root、generation-rounds=3、output |
| `uv run python -m configbrain.report` | draft、output | 保存JSONの表示のみ |
| `uv run python -m configbrain.search_commands` | catalog、document-id、command | reference-root、mode、parent |
| `uv run python -m configbrain.pipeline` | requirementまたはrequirement-file、output-dir | catalog、database、資料各root、limit=20、generation-rounds=3、resume |

表の引数名には実行時に`--`を付ける。
生成パイプラインの既定catalogは`data/catalog/c9300-iosxe26.json`。
資料各rootの既定値は第3章の配置と同じである。

## 13. 検証体系と残る限界

pytestを使用し、取り込み・変換・索引・選定・検索・文脈・生成・引用・表示・再開を試験する。
設定は[pyproject.toml](../pyproject.toml)、試験は[tests](../tests/)を参照する。
専用の型検査器・lintツールは現在の依存定義にない。

実行する試験の例は[実装ガイド](./implementation-guide.md#5-テストと品質評価)、
各時点の試験結果と実モデル評価は[進捗管理](./progress.md)に記録する。

[意味審査テスト](../tests/test_semantic_review.py)にはVLAN groupの誤転用、
VTP条件の欠落、no shutdownとLayer3化の混同に関する正例・負例6件がある。
`review_metrics`は有効なunit判定と人手ラベルを比較し、不当な支持・適用確定・正解数を集計する。
回帰ゲートは全ラベル一致かつ不当承認ゼロで、全件保留では合格しない。
評価は関数として提供しており、独立した実モデル用評価CLIや自動品質ゲートは未提供である。

固定応答試験で確認できるのは契約・制御・保存・表示であり、
モデルが正しく分解・推論・条件判定できるかは別途実測が必要である。
demo24はその差が実際に表れた基準例として扱う。
安全側に停止することだけをもって、有用な審査が実行できたとは評価しない。
