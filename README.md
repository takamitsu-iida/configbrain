# configbrain

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

手順見出しの索引形式が更新された場合、次の`--incremental`実行では、対象製品の
文書を再索引化します。処理に時間がかかり、引用IDも変わる場合があります。
既存のレポートを参照している場合は索引をバックアップし、共有環境では他の利用者と
調整してから実行してください。更新中は対象文書の検索結果が一時的に不完全になる
可能性があります。

#### 全PDF・OKFのローカル監査

全件を対象に品質を調べる場合は、まず次の監査を実行します。
外部API、LLM、OCRは呼び出さず、元PDF・Markdown・パッケージ・索引を変更しません。

```bash
uv run python scripts/audit_corpus.py \
  --data-root data/products/catalyst9300 \
  --output-dir data/evaluation/c9300-corpus-audit
```

`raw/`、`extracted/`、`packages/`の文書IDディレクトリの和集合を対象とし、
各IDのPDF 1件、抽出Markdown 1件、OKFの`manual.md`と`datapackage.json`を
照合します。入力の欠落や曖昧な対応はエラーとして記録し、他の文書は継続します。
同じ出典URLのパッケージも省略せず検査し、重複一覧を残します。

主な検査内容は次のとおりです。

- 既存のパッケージ検証（構造、リソースのハッシュ、画像参照、内部リンク）
- PDF全ページからのテキスト抽出と、抽出Markdown・OKFそれぞれとの対応付け
- 対応範囲の本文断片、プロンプト付きコマンド例、注意・制約文の欠落・変更候補
- 抽出MarkdownとOKFの本文断片の双方向比較、見出し・アンカー・画像参照の件数差
- Markdownのコードフェンス、表の列数、手順表の見出し・手順番号の異常候補
- PDF画像のあるページの視覚確認要求

対応付けには、空白・大文字小文字・記号を除いた48文字の断片を使います。
Markdown側は32文字間隔で採取し、PDF側を走査します。両文書で一意な断片が
3個以上あり、順序と広がりを確認できたページのみ比較します。
隣接ページの一致箇所から推定した比較範囲は重なりを含み、正確なページ境界では
ありません。対応付けできないページは、別の箇所の同じコマンドで代用しません。

本文比較も48文字の断片を32文字間隔で調べ、末尾を含めます。欠落候補は
一致率にかかわらず記録し、一致率90%未満は優先確認対象にします。
PDF座標で上下6%の余白にあり、3ページ以上で繰り返される行や単独のページ番号は
本文比較から除外し、除外した行もレポートに保存します。
コマンド例は記号も比較しますが、
PDFの列の混在や折り返し、表の読み順により誤検出があります。
本文比較では記号だけの変化や意味の変化を検出できず、重複した本文の欠落・追加も
見逃す可能性があります。図表の意味や画像内の文字は自動検証しません。

出力は次のとおりです。

- `summary.md`: 全体集計、文書別結果、優先確認ページ、出典の重複一覧
- `manifest.json`: 入力ルート、ツール・依存ライブラリ、検査条件、進捗、全体集計
- `documents/<document_id>.json`: 入力ハッシュ、全ページの状態、比較行範囲、
  一致率、異常候補、除外行。欠落断片の例は各比較につき最大8件です。

ページ状態は`checked`（テキスト検査で候補なし）、`review_required`（要確認）、
`unverifiable`（テキスト不足・対応不明）、`error`（読み取り等の失敗）です。
**`checked`も意味的な正しさや完全性の承認ではありません。**
文書・実行全体の完了と、内容の検証完了は別に扱ってください。

保存先は新規ディレクトリを指定します。中断後は同じ保存先へ`--resume`を付けます。
入力・ツール・依存ライブラリが同じで、パッケージ再検証結果も一致する文書のみ
既存結果を再利用し、エラー文書や変更のある文書は再検査します。
文書途中で中断した場合、その文書は最初から処理します。
同じ保存先への同時実行はロックで拒否します。強制終了で`.audit.lock`が残った場合は、
その保存先を利用する監査プロセスが停止していることを確認してから削除してください。

終了コード0は候補・検査不能・エラーなし、2は要確認または検査不能あり、
1は入力・検査エラーです。未解決項目を正常とみなしてパッケージや索引を
自動更新する処理はありません。個別候補の調査には以下の範囲指定検査を利用できますが、
監査の推定行範囲をそのまま修正へ渡さず、対応範囲を元PDFで確認してください。

#### 監査候補のPDF照合

全件監査で得たコマンド例の相違候補と、OKFの表の列不整合をまとめて照合できます。
元の監査レポートと入力のハッシュが一致することを確認し、対象PDFページの文字座標、
コマンド列、折り返し、Markdownの対応見出し・行を根拠として保存します。
外部APIは呼ばず、本文・監査結果・索引を変更しません。

```bash
uv run python scripts/reconcile_corpus_audit.py \
  --audit-dir data/evaluation/c9300-corpus-audit \
  --output-dir data/evaluation/c9300-reconciliation
```

`--audit-dir`には実際の監査結果ディレクトリ、`--output-dir`には新規ディレクトリを
指定してください。`summary.md`、`manifest.json`、全候補の`review_queue.csv`、
`documents/*.json`を保存します。
各候補には元の監査JSONへのポインタ、PDFのページ・テキスト・座標、
OKFと抽出Markdownの比較範囲、判定理由を記録します。

判定は、監査の誤検出、コマンドのセル分断・プロンプト喪失、対応セクション内での
例文の欠落、表の列数の揺れ、判断保留に分けます。説明列の混入を除外し、
見出しやページ境界が曖昧な場合や、別の箇所に同じ例がある場合は保留します。
例文の欠落を確定するのは、PDFのコマンド列と対応見出しを特定でき、
監査の比較範囲にも一致を確認できない場合に限ります。

**列数の揺れだけで内容欠落とは断定しません。** 短い行の空セル補完、空行、
セル内へのコードフェンス混入、余剰セルを区別して記録します。
PDFページの候補は前後ページも含み、本文断片が一致したページと区別します。
判定は対象例文とシリアライズの照合であり、コマンドの意味や表の完全性の承認では
ありません。終了コード0はすべて誤検出、2は相違・列数の揺れ・保留あり、
1は入力等のエラーです。

#### 根拠付き補正の検証・適用

補正案の作成と本文への適用は分離します。`scripts/apply_corrections.py`は、
明示的に指定・承認された置換だけを行う独立CLIです。LLMや外部APIは使いません。
元PDF、抽出Markdown、OKF、監査結果、状態DB、索引は変更しません。
通常パイプラインへの自動接続はまだなく、将来の接続位置はOCR統合後・分割前です。

実例は`data/corrections/c9300-stateful-dhcp/corrections.json`と
同ディレクトリの`stateful-dhcp.md`です。物理PDFページ39〜40の
「Configuring Stateful DHCP Externally」全14手順を、元Markdownの
951〜988行と置き換える**未承認候補**です。表ではなく手順別の説明とコードブロックで
表現し、PDFの折り返しを結合しています。原文の値・モード・説明は外部知識で修正せず、
Step 10のIPv6という説明も原文どおり残します。Step 14の語間はPDFの例を根拠にしています。

```bash
# 未承認でも機械検証と根拠の保存が可能。未承認があれば終了コード2。
uv run python scripts/apply_corrections.py check \
  data/corrections/c9300-stateful-dhcp/corrections.json \
  --output-dir data/evaluation/c9300-correction-check

# この実例は未承認なので拒否される。本文は出力されない。
uv run python scripts/apply_corrections.py apply \
  data/corrections/c9300-stateful-dhcp/corrections.json \
  --output-dir data/evaluation/c9300-correction-apply
```

JSONの`schema_version`は1です。未知・重複フィールドは拒否し、各項目を必須とします。
パスはJSONの所在を基準とし、置換ファイルはそのディレクトリ内に置きます。

- `source.pdf`と`source.markdown`: `path`とファイル全体の`sha256`。
  `source.assets_dir`は元Markdownのローカル画像を解決するディレクトリです。
- `corrections[]`: 一意な`id`、`reason`、`pdf_pages: [開始, 終了]`、
  `target: {lines: [開始, 終了], sha256: ...}`、
  `replacement: {path: ..., sha256: ...}`、`review`を指定します。
  PDFは物理ページ番号、Markdownは元入力の行番号で、いずれも1始まり・両端を含みます。
  対象断片のSHA-256は、改行を変換せず元ファイルをバイト列として行分割・連結して計算します。
  PDF、元本文、対象断片、置換ファイルのいずれかが不一致なら停止し、曖昧検索はしません。
- `review`: `status`は`pending_review`、`approved`、`rejected`のいずれかです。
  PDF原本でコマンド・例・説明・条件と手順全体を確認した後に限り、
  `pdf_compared`と`complete_procedure_checked`を`true`にし、`notes`に確認内容を記録し、
  `status`を`approved`へ変更してください。機械検証は承認の代わりになりません。
  この記録はローカルの明示的な確認申告であり、署名・認証機構ではありません。
- `open_issues`: 文書に残る未解決事項の配列です。承認した範囲の記録は必要に応じて更新し、
  それ以外の未解決事項を消さないでください。

すべての範囲を同じ元入力に対して検証し、重複・範囲外・空白だけの対象を拒否してから
一括置換します。対象外のバイト列と、置換ファイルの末尾空行を保持します。
置換内の改行だけは対象範囲の形式へ統一し、対象末尾に改行がなければ元文書の形式を
使います。後続行がある場合は置換末尾の改行が必要です。
置換の未閉鎖フェンス・不整合表、範囲境界で生じる表の不整合、既存の
spanアンカー・画像・リンクの除去、新しい重複アンカー・内部リンク切れを拒否します。
既存の内部リンク切れ・重複アンカー・表の問題は検証結果に残し、正常扱いにしません。
元文書のフェンスが未閉鎖なら、安全に置換できる範囲を先に見直してください。

出力先は入力ディレクトリの外にある新規ディレクトリに限ります。
`manifest.json`、`validation.json`、`correction-set.json`、`diff.patch`、
補正ごとの`fragments/*.before.md`・`*.after.md`、PDFテキストと座標の
`evidence/*.json`を保存します。`check`は本文全文を出力しません。
`apply`は**全補正が承認済みの場合だけ**`manual.md`と参照ローカル画像を出力します。
相対画像パスは維持し、親ディレクトリ参照、資産外へのシンボリックリンク、
出力予約名との衝突は拒否します。外部画像・リンクは取得・確認しません。
ローカルの画像以外の相対ファイルリンクは、移動先で壊れるのを避けるため拒否します。
入力変更を出力準備の前後で再確認し、出力の`manifest.json`を最後に公開します。
I/O障害等で完了manifestがないディレクトリは採用せず、別の新規出力先で再実行してください。

終了コード0は承認済み範囲の検証・適用成功、2は未承認または却下による保留、
1は入力・検証・I/Oエラーです。**適用成功は文書全体の品質承認ではありません。**
`document_quality_approved`は常に`false`で、未解決事項と根拠を後続のレビューへ引き継ぎます。
残件の補正、再監査、下流の内容保持検証を経るまで本番パッケージ・索引へ昇格させないでください。

#### PDFとMarkdownの品質検査（試作）

対応するPDFページとMarkdownの行範囲を指定して、手順表やコマンド例の相違候補を
確認できます。ページ番号と行番号は1始まり、範囲の両端を含みます。
PDF全ページやMarkdown全文の無関係な箇所を混ぜず、対応範囲を確認してください。

```bash
uv run python scripts/check_pdf_markdown_quality.py \
  data/products/catalyst9300/raw/catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f/b_26x_sys_mgmt_9300_cg.pdf \
  data/products/catalyst9300/extracted/catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f/b_26x_sys_mgmt_9300_cg/b_26x_sys_mgmt_9300_cg.md \
  --pdf-pages 82:82 --markdown-lines 2524:2544 \
  --output data/evaluation/svi-raw-quality.json
```

検査はLLMを呼ばず、既存ファイルを上書きしません。報告にはファイルのSHA-256、
比較範囲、PDFページやMarkdown行、表・手順の異常およびコマンド例の欠落・追加・
重複候補を記録します。要確認項目や検査不能な箇所がある場合は終了コード2、
入力エラーの場合は1を返します。

PDFのテキストレイヤーにあるプロンプト付き例と特定形式の手順表を主に対象とする
限定的な検査です。座標による折り返しの推定、列数、手順順序、文字列の比較は
誤検出・未検出があり、画像内の情報、すべてのコマンド、表の意味や文書の完全性を
保証しません。結果が検出なしでも、対象範囲と元PDFを確認してください。

#### 指定範囲の検査・修正・再検査（試作）

`repair_pdf_markdown.py`は、指定範囲について次の流れを自動実行します。
既存の文書全体のパイプラインとは独立しており、修正結果を自動適用しません。

```text
品質検査 → 異常候補がある範囲をLLMで修正 → 同じPDFを使って再検査
                                              ↓
                       通過／未解決／回数上限で停止、候補と履歴を保存
```

次の例は、異常候補がある場合に指定したPDFページのテキスト・座標とMarkdown範囲を
設定済みのLLM APIへ送信します。送信範囲を事前に確認してください。
画像は送信しません。テキストレイヤーが不足しているページは修正せず停止します。

```bash
uv run python scripts/repair_pdf_markdown.py \
  data/products/catalyst9300/raw/catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f/b_26x_sys_mgmt_9300_cg.pdf \
  data/products/catalyst9300/extracted/catalyst9300-26-x-b-26x-sys-mgmt-9300-cg-c8b91f2a412f/b_26x_sys_mgmt_9300_cg/b_26x_sys_mgmt_9300_cg.md \
  --pdf-pages 82:82 --markdown-lines 2524:2544 \
  --max-attempts 2 --output-dir data/evaluation/svi-repair-run1
```

- 保存先は新規ディレクトリに限定します。再実行時は別の保存先を指定してください。
- 元PDFとMarkdownは変更せず、指定範囲外を保持した全文を`candidate-0001.md`などに
  保存します。`fragment-0001.md`には修正範囲だけを保存します。
- `check-0000.json`は修正前、`check-0001.json`以降は各候補の再検査結果です。
  `manifest.json`には出典のハッシュ、入力範囲、モデル指定値、試行履歴、停止理由を
  記録します。モデル未指定の場合、実際にAPI側で選ばれたモデルは記録できません。
- 修正回数の既定上限は2回、指定可能範囲は1〜5回です。候補が変わらない、
  LLMが未解決事項を返す、検査の制約だけが残る場合も停止します。
- 初回に異常候補がなければLLMを呼びません。不正な応答、リンク・画像・アンカーや
  見出しの変更は拒否し、履歴へエラーを保存します。

終了コード0は`no_changes_needed`または`checks_passed_review_required`、
2は`unresolved`、1は入力・API・応答のエラーです。**0も自動承認ではありません。**
チェックだけを満たす修正や説明・条件の欠落を完全には排除できません。
すべての候補を元PDFと比較してから採否を判断してください。未解決の候補を
パッケージ化・索引化へ進めず、文書全体の自動処理への統合は別途評価します。

#### 新方式: ルール候補の抽出（試作）

新方式の進捗は[ルール候補に基づく設定生成の進捗](RULE_SYSTEM_PROGRESS.md)を
参照してください。現在は、OKF Data Packageの**1セクションから未検証の候補を
抽出する段階**です。候補のQdrant索引化や、候補を使った設定生成は未実装です。
既存の索引・設定生成には変更を加えません。

まず外部APIを呼ばずに対象セクションのIDを確認します。

```bash
uv run python scripts/extract_rule_candidates.py \
  data/products/catalyst9300/packages/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc \
  --list-sections --heading-contains "Creating or Modifying an Ethernet VLAN"
```

取得したIDを指定して候補を抽出します。次の例はLLM APIに選択したマニュアル本文を
送信するため、外部送信してよい情報であることを事前に確認してください。

```bash
uv run python scripts/extract_rule_candidates.py \
  data/products/catalyst9300/packages/catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc \
  --section catalyst9300-26-x-b-26x-vlan-9300-cg-b64b1bc78dbc:section-0075 \
  --output data/evaluation/rule-candidates-vlan.json
```

保存先は新規ファイルに限定し、既存ファイルを上書きしません。引用の不一致や
不明なモードなどがある場合も候補と`issues`を保存し、終了コード2で通知します。
引用の文字列一致は、意味、条件、手順の完全性やPDFからの変換品質を保証しません。
未検証の候補は実機への設定生成に使わないでください。

#### HTMLとPDFのルール候補の比較（試作）

同じ製品・リリース・手順に対応するHTMLを手元に保存し、PDF側の候補JSONと比較できます。
HTML取得や対象範囲の対応付けは自動化していません。HTMLには表・手順・前提条件を含む
適切な要素を選んでください。`--list-headings`で見出しを確認できます。

```bash
uv run python scripts/compare_html_rules.py /path/to/manual.html --list-headings

# セレクターと入力範囲を確認。LLM APIは呼びません。
uv run python scripts/compare_html_rules.py /path/to/manual.html \
  --selector 'section#procedure' \
  --pdf-result /path/to/pdf-candidates.json \
  --source-url https://www.cisco.com/path/to/manual.html --preview

# 選択内容を確認した後に実行。選択したHTML本文をLLM APIへ送信します。
uv run python scripts/compare_html_rules.py /path/to/manual.html \
  --selector 'section#procedure' \
  --pdf-result /path/to/pdf-candidates.json \
  --source-url https://www.cisco.com/path/to/manual.html \
  --output data/evaluation/html-comparison.json
```

セレクターは実際のHTMLに合わせて変更し、1要素だけに一致させてください。
同様にVLAN、アクセスポート、SVI、DHCPリレーの各範囲と、それに対応する
PDF側の候補JSONを指定します。既存の出力は上書きしません。HTML側に未解決項目や
画像の未抽出などがある場合は結果を保存して終了コード2を返します。

比較JSONにはHTMLのハッシュ・セレクター・変換後テキスト、PDF側の候補と両者の集計を
保存します。表の行・セルやコードブロックは区別しますが、引用の文字列照合は
**変換後テキスト**に対するもので、HTMLの意味的な照合ではありません。画像は
抽出せず、入れ子の表はエラーとします。元HTML、対象範囲、製品・リリースの一致と
候補の正しさは別途確認してください。件数や文字列一致は精度を示しません。
HTML側とPDF側で対象範囲やLLM設定が異なる場合は単純比較できません。

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

#### CSVパラメータシートのデモ

[デモ用CSV](data/evaluation/parameter-sheet-demo.csv)の1行を入力として、
Catalyst 9300 / IOS XE 26.x向けのVLAN、アクセスポート、SVI、DHCPリレーの
設定案を生成できます。CSVはUTF-8、ヘッダー付き、データ1行の固定形式です。
実際のパラメータシートを汎用的に読み取るものではありません。

| 項目 | 内容 |
|---|---|
| `product`, `release` | `catalyst9300`, `26.x`のみ |
| `vlan_id` | 新規VLANのID（2〜1001、1006〜4094） |
| `access_port` | `GigabitEthernet1/0/20`形式のポート |
| `svi_ipv4_cidr` | SVIのIPv4アドレスとプレフィックス |
| `svi_vrf`, `dhcp_server_vrf` | SVIのVRFとサーバーへの経路があるVRF。それぞれ`default`または既存VRF名を指定し、両方必須で同一値のみ対応 |
| `dhcp_server` | DHCPサーバーのIPv4アドレス |
| `ip_routing`, `service_dhcp` | 既に有効なら`already_enabled`、新規設定するなら`configure` |

まず外部APIを呼ばずにCSVを検証し、生成要求を確認します。

```bash
uv run python scripts/parameter_sheet_demo.py \
  data/evaluation/parameter-sheet-demo.csv --validate-only
```

実際に生成するには、索引とLLM APIの準備を済ませてから実行します。
生成時はシートの値が外部LLM APIへ送られるため、機密情報の扱いに注意してください。

```bash
uv run python scripts/parameter_sheet_demo.py \
  data/evaluation/parameter-sheet-demo.csv \
  --markdown-output data/evaluation/parameter-sheet-report-split.md \
  > data/evaluation/parameter-sheet-result-split.json
```

結果は標準出力のJSONと、`--markdown-output`で指定したMarkdownレポートです。
VLAN、アクセスポート、SVI、DHCPリレーの4要件について、それぞれ検索計画・根拠検索・
設定生成を行います。各要件に必要な値とVRF・既存設定の条件を渡し、設定モードごとに
重複をまとめて結合します。各要件でモードを終了する`exit`も根拠を確認します。
LLM APIの呼び出し回数と実行時間は一括生成より増えます。

どちらも設定、警告、不足値、引用、検索根拠、CLIごとの引用確認、要件ごとの結果を含みます。
JSONの`component_results`には要件ごとの検索語、個別候補と確認結果も記録します。
個別候補は全体が失敗した場合を含めて診断用であり、実機に投入しないでください。
Markdownは同じ生成結果から
作成するため、追加のLLM API呼び出しはありません。空欄や不正値は生成前に
エラーとし、生成設定に必要な行が欠けるか、対象外の設定が含まれる場合は
`configuration`を空にして理由を表示します。1要件でも根拠不足や引用未確認なら
設定全体を棄権し、どの要件で失敗したかを表示します。
各CLIについて、説明中の`CLI: <コマンド> [引用ID]`と引用先の構文・文脈を
照合します。引用がない場合や無関係な文脈を検出した場合は設定全体を棄権します。
この照合は定型の文字列・文脈による保守的な確認であり、引用の意味的な妥当性や
設定の正しさを保証しません。妥当な根拠でも照合できず棄権する場合があります。
`already_enabled`は利用者が確認済みであることを意味し、機器の状態や
DHCPサーバーへの到達性は確認しません。VRF名は大文字と小文字を区別し、英数字で始まる
英数字、`_`、`.`、`-`の32文字以内に限ります。名前付きVRFはIPv4用の設定を含めて
既に存在する前提で、SVIへのVRF割り当てをIPアドレス設定より前に行います。
異なるVRF間のリレーは対象外です。出力はレビュー用であり、実機へ自動投入しません。
例えば既存VRF `Blue`を使用する場合は、両方のVRF欄に`Blue`を指定します。

#### 異なるCSV形式からの対応付け（試作）

列名やレイアウトの異なるCSVを、上記デモの固定形式へ変換する試作です。
対象の設定機能は上記デモと同じで、任意の設定項目を生成できるものではありません。
[行形式の例](data/evaluation/parameter-sheet-rows.csv)と
[列形式の例](data/evaluation/parameter-sheet-columns.csv)を用意しています。

1. LLMにセルの対応案を作成させます。CSV全体が設定済みの外部LLM APIに
   送られるため、機密情報の扱いを確認してください。

   ```bash
   uv run python scripts/normalize_parameter_sheet.py propose \
     data/evaluation/parameter-sheet-rows.csv \
     > data/evaluation/mapping-review.json
   ```

2. 元CSVと対応案の各項目の`row`、`column`、`value`、`row_cells`、
   `first_row_cell`を照合し、各項目の`confirmed`を`true`にします。
   行番号はCSVの論理行（ヘッダーを含む1始まり）、列番号も1始まりです。
   `unresolved`は未解決項目、`unmapped_cells`は選ばれていない非空セルです。
   各未対応セルの`decision`を、設定に不要なら`irrelevant`、必要な設定なら
   `required`にし、`irrelevant`の場合は`reason`に理由を記入します。
   **未判断のセルや`required`がある場合、出力は拒否されます。**
   複数機器の行がある場合は、対応案が対象機器を正しく選んだかも確認してください。
   対応する行・列を変更した場合は`refresh`で値と未対応セルを再計算し、
   全確認状態が初期化された対応案を改めて確認します。

   ```bash
   uv run python scripts/normalize_parameter_sheet.py refresh \
     data/evaluation/parameter-sheet-rows.csv \
     data/evaluation/mapping-review.json \
     > data/evaluation/mapping-review-refreshed.json
   ```

   `refresh`は元CSVと対応案のSHA-256が一致するときだけ利用できます。
3. 確認済みなら`--confirm`を付けて固定形式CSVを出力します。
   入力CSVのSHA-256、参照セルと値、各項目・未対応セルの確認、
   必須値、VRFなどを検証します。従来の一括確認だけの対応案は出力できません。
   入力CSVを変更した場合は対応案を作り直してください。

   ```bash
   uv run python scripts/normalize_parameter_sheet.py export \
     data/evaluation/parameter-sheet-rows.csv \
     data/evaluation/mapping-review.json --confirm \
     > data/evaluation/normalized-sheet.csv
   ```

   `refresh`を使った場合は、確認済みの`mapping-review-refreshed.json`を指定します。

4. `normalized-sheet.csv`を上記の`parameter_sheet_demo.py`へ渡します。
   出力したCSVには対応付けた項目しか含まれず、元シートのすべての要件を
   網羅したことは自動的に保証されません。

対応案は値を推測・変換せず元セルを参照します。例えば`ip_routing`の値が
「有効」のように`already_enabled`と異なる場合は自動変換せず、検証で拒否します。
空欄は`default`などで補完せず未解決とし、対応する値のある元CSVを用意してください。
LLMの対応は誤る可能性があるため、`--confirm`は人によるレビューの代わりにはなりません。

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
  user@fast-machine:/path/configbrain/data/products/catalyst9300/

別マシンでは uv sync の実行と .env の設定が必要。

-->