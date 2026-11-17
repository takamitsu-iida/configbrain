# configbrain

Cisco公式マニュアルを根拠として、要求に沿った有用なコンフィグ案を生成するプロジェクトです。
全要求を満たす根拠が揃わない場合も、根拠のある部分設定、未充足項目、確認できた制約を提示します。
推論候補は根拠付きコンフィグと分離し、適用可能な設定として扱いません。

## 開発方針

新しい開発指示と合格条件は[instruction.md](./instruction.md)を参照してください。
初期段階ではCisco Catalyst 9300 IOS XE 26.xの公式マニュアル群を対象に、
更新確認からPDF取得、OKF変換、検証、Qdrant登録までの取り込みパイプラインを構築します。
その後、要件から参照すべきマニュアルと章をLLMで選定する方式を評価し、根拠確認を経て
部分コンフィグを提示する処理へ進みます。

## マニュアルカタログとPDF取得

Cisco公式のCatalyst 9300 / IOS XE 26.x掲載ページから、対象リリースの文書カタログを作成します。

```sh
uv run python scripts/discover_manuals.py \
  --product catalyst9300 \
  --release 26.x \
  --seed-url https://www.cisco.com/c/en/us/support/switches/catalyst-9300-series-switches/products-installation-and-configuration-guides-list.html \
  --output data/catalog/c9300-iosxe26.json
```

カタログには文書名、分類、PDF URL、対応するHTML目次URL、目次順の章タイトル・URL、
安定した文書IDが記録され、再実行時は掲載情報の新規・変更・削除を報告します。
HTML目次が見つからない場合や章リンクがない場合も状態を記録します。
PDF本文はHTTP `ETag`または`Last-Modified`で更新を確認し、同じ版でローカルの
SHA-256も一致する場合は再取得を省略します。更新時は`wget`で一時ファイルへ取得し、PDFとして検証して
から保存済み原本を置き換えます。サーバーから更新判定用ヘッダーが得られない場合は、安全のため再取得します。

```sh
uv run python -m configbrain.download_manuals \
  --catalog data/catalog/c9300-iosxe26.json
```

取得先は既定で`data/source_manuals/<document_id>/`です。別の場所を使う場合は`--destination`で指定できます。
原本PDFとローカル認証情報はGitへ追加しません。

## 取り込みパイプラインの実施順序

1. 公式掲載ページから文書カタログを作成し、前回との差分を確認します。
2. PDFの更新を確認し、必要な原本を取得・検証します。
3. 1冊をパイロット変換し、HTML本文、表、CLI、画像、PDFページ対応を検証します。
4. パイロット合格後、残りのPDFを個別にOKFへ変換します。各パッケージのFrictionless検証、
   章数・表・整形済みブロック・画像・ページ対応を確認し、問題のある文書は登録対象から外します。
5. 検証済みのOKFだけをQdrantへ登録し、文書・章・出典の情報を検索結果から追跡できるようにします。
6. 要件例を使い、ベクトル検索で候補を事前に絞らず、対象リリースの全マニュアルカタログから
   LLMが参照文書・章を選べるか評価します。複合要件は個別項目ごとに扱い、複数文書を選択できるようにします。
7. 選定した章の本文・表・手順を確認し、原本の出典位置と照合します。その後、根拠付きコンフィグ生成を評価します。

Qdrantへの登録は全対象文書の変換と検証が済んでから行い、失敗した変換を検索インデックスへ混在させません。

## 次段階: LLMによる参照マニュアル選定の評価

日本語要件をそのままベクトル検索する方式で検索漏れと誤った文脈の候補が見られたため、
次はマニュアル選定をLLMに委ねる方式を試します。ベクトル検索の上位結果で候補を絞らず、
対象製品・リリースの文書カタログ全体をLLMへ渡します。初期対象はカタログの全15文書・303章です。

- VLAN作成、アクセスポート、SVI、DHCPリレーなどの要件項目ごとに選定し、複合要件では必要な複数文書・章を返せるようにします。
- 出力はカタログに存在する文書ID・章URLに限定し、選定理由と判断の不確実さも記録します。
- 以前の13件の要件例、VLAN 120のパラメータシート例、Stateful DHCPv6の14手順を評価ケースとして再利用します。
- 正解文書・章のRecall@k、複合要件の項目網羅率、無関係な候補の割合を記録します。
- この段階では選定結果だけを評価し、コンフィグ生成や設定適用は行いません。Qdrantの結果は比較用の基準として残します。

## OKF変換パイロット

まずIP Addressing Services Configuration Guide 1冊で、カタログに記録された章HTMLを
Markdown化します。表は行・セル構造を保つためHTMLのまま埋め込み、CLI等の整形済みブロックは
表内のものも含めHTMLのまま保持し、表外のものはコードブロックとして保持します。
PDFページは、PDF本文の`CHAPTER`見出し直後に章名が一意に
一致した場合だけ割り当て、それ以外は未対応として記録します。

```sh
uv run python -m configbrain.convert_manual \
  --catalog data/catalog/c9300-iosxe26.json \
  --document-id catalyst9300-26-x-b-26x-ip-9300-cg-a541d83b236d
```

出力先は`data/okf/<document_id>/`です。`manual.md`、PDFと各HTML章の出典・ハッシュ・
ページ対応を記録した`provenance.json`、HTML画像リソース、およびFrictionlessで検証した
`datapackage.json`を生成します。これは品質評価用の1冊の試験変換です。パイロットの検証後に
残りの文書へ適用します。生成データは原本同様Gitに追加しません。
取得できない画像は元URLを残し、未取得状態を`provenance.json`と実行結果に明示します。

## Qdrantへの登録

検証済みの全OKFパッケージを、Qdrantの永続ローカルモードで登録します。
Qdrantサーバーは不要で、DBは`data/qdrant/db/`、検索用の別名は
`c9300_iosxe26_manuals`です。日本語の要件から英語のマニュアルを検索できるよう、
埋め込みはCPU上の`intfloat/multilingual-e5-small`で生成します。
登録するテキストには`passage:`、検索クエリには`query:`の接頭辞を付けます。

```sh
uv run python -m configbrain.index_manuals \
  --catalog data/catalog/c9300-iosxe26.json
```

登録前に各OKFのFrictionless検証、原本PDFと画像のハッシュ、章とPDFページの対応を確認します。
表は列見出しとセルの対応を含む行単位、CLIブロックと本文は出典メタデータ付きチャンクとして
登録します。新しいコレクションの点数とIDを検証してから別名を切り替えるため、登録失敗時は
既存の検索対象を維持し、同じ入力で再実行しても重複しません。QdrantのDBはローカルデータのため
Gitに追加しません。
