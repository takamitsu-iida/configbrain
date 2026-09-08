# ConfigBrain 実装計画

## 1. 目的

Cisco公式マニュアルを根拠として、機種・OS・バージョンに適合したネットワーク設定コマンドを検索・生成するRAGシステムを構築する。

最初の対象はCisco Catalyst 9300 / IOS XE 26.xとし、VLAN、インターフェース、System Management設定に限定する。検索品質を先に検証し、根拠が不足する場合は推測で補完しない。

## 2. 基本方針

### 2.1 HTMLを一次入力とする

- Cisco公式HTMLマニュアルを検索用の一次資料とする。
- PDFは補助的な照合資料として保持する。
- HTML本文は取得元URL、節URL、取得日時、ハッシュと紐付ける。
- Cisco公式ドメイン、対象バージョン、許可したマニュアル配下だけを取得する。

### 2.2 RAG用中間JSONを中心にする

HTMLを直接Embeddingするのではなく、次の段階を経由する。

```text
公式HTML
  -> 原文保存・DOM正規化
  -> ルールベース抽出
  -> 手順・節単位の候補ブロック
  -> 難しいブロックだけ生成AIで構造化
  -> 機械検証済みRAG中間JSON
  -> 親子チャンク生成
  -> BM25/全文検索 + Embedding検索
  -> 軽量リランキング
  -> Qdrant
```

中間JSONは、原文の代替ではなく、原文と構造化情報を同時に保持する。生成AIは文書の編集者ではなく、構造化アノテーターとして利用する。

CLIコマンドやVLAN番号の完全一致にはBM25/全文検索を使い、自然言語の意味検索にはベクトル検索を使う。両方の結果を統合し、必要に応じて軽量なリランキングを行う。

### 2.3 生成AIの利用範囲

生成AIに許可する処理:

- 候補ブロックの種別分類
- 節タイトル、前提条件、操作、確認手順の構造化
- 説明文とコマンドの同一手順へのグルーピング
- 検索用キーワードの抽出
- 構造化処理の信頼度と検証状態の提案

生成AIに許可しない処理:

- 原文にないコマンドやパラメータの生成
- コマンドの修正・正規化による意味変更
- URL、節、製品、バージョン、ページの推測
- 原文を破棄した要約だけのEmbedding

LLM出力はJSON Schemaで検証し、原文内に存在しないコマンド・引用情報を不採用にする。

## 3. 対象範囲

### 対象

- [x] Cisco Catalyst 9300
- [x] Cisco IOS XE 26.x
- [x] VLAN
- [x] インターフェース
- [x] System Management
- [x] Cisco公式マニュアル3冊
- [x] 代表質問15件（`evaluation-questions.md`）
- [ ] RAG用中間JSON
- [ ] 親子チャンクとコマンド構造化
- [ ] BM25/全文検索とベクトル検索の併用
- [ ] 根拠付き設定コマンド生成
- [ ] HTML節URLによる引用表示

### 対象外

- 実機へのSSH接続・設定投入
- 複数ベンダー対応
- 完全な構成検証
- ユーザー認証・組織管理
- 未承認のWeb全体巡回

## 4. 公式資料

| 文書 | HTML入口 |
| --- | --- |
| VLAN Configuration Guide | <https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/vlan/b_26x_vlan_9300_cg.html> |
| Interface and Hardware Components Configuration Guide | <https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/int_hw/b_26x_int_and_hw_9300_cg.html> |
| System Management Configuration Guide | <https://www.cisco.com/c/en/us/td/docs/switches/lan/catalyst9300/software/release/26-x/configuration_guide/sys_mgmt/b_26x_sys_mgmt_9300_cg.html> |

取得時に次の情報を記録する。

- `document_id`
- 文書タイトル、製品、OS、バージョン
- HTML入口URLと節URL
- HTTP取得日時
- HTML本文のSHA-256
- 保存ファイル: `data/raw/html/{document_id}/{sha256}.html`
- 取得結果、失敗URL、再試行回数

## 5. RAG中間JSON仕様

### 5.1 文書ブロック

```json
{
  "block_id": "c9300_iosxe26_vlan_cg:configuring_vlans:creating_ethernet_vlan:001",
  "document_id": "c9300_iosxe26_vlan_cg",
  "source_url": "https://www.cisco.com/.../b_26x_vlan_9300_cg.html",
  "section_url": "https://www.cisco.com/.../configuring_vlans.html#creating-ethernet-vlan",
  "section_title": "Creating or Modifying an Ethernet VLAN",
  "content_type": "configuration_example",
  "product": "catalyst9300",
  "os": "IOS XE",
  "os_version": "26.x",
  "source_text": "原HTMLから抽出した本文・表・コードの原文",
  "parent_block_id": "c9300_iosxe26_vlan_cg:configuring_vlans:001",
  "chunk_level": "child",
  "prerequisites": ["原文に存在する前提条件"],
  "commands": [
    {"text": "Device(config)# vlan 100", "role": "enter_vlan"},
    {"text": "Device(config-vlan)# name USERS", "role": "set_name"}
  ],
  "verification_commands": [],
  "keywords": ["VLAN", "normal-range VLAN", "vlan command"],
  "annotation_status": "verified",
  "annotation_confidence": 0.96,
  "validation_errors": [],
  "llm_annotation": {
    "model": "使用モデル",
    "prompt_version": "プロンプト版",
    "created_at": "取得日時"
  }
}
```

### 5.2 必須ルール

- `source_text`は必須で、LLMの要約だけを保存しない。
- `commands`の各要素は`source_text`の完全一致または許可した空白差分で検証する。
- `commands`はコマンド本文と役割を持つ構造化オブジェクトとして保存する。
- 親チャンクは節の説明・前提条件を保持し、子チャンクは個別の手順・コマンド・確認操作を保持する。
- `annotation_status`は`verified`、`needs_review`、`rejected`のいずれかとする。
- `annotation_confidence`が閾値未満、または`validation_errors`が空でないブロックは通常検索へ登録しない。
- `section_url`と`section_title`はHTMLから機械的に取得する。
- `content_type`は次のいずれかとする。
  - `concept`
  - `configuration_example`
  - `command_reference`
  - `warning`
  - `parameter_reference`
- 検証に失敗したLLM出力はQdrantへ登録しない。

## 6. Phase 0: 評価条件の固定

### 目的

HTMLから作成した中間JSONが、15問の検索評価に必要な情報を保持できることを定義する。

### 作業項目

- [x] Catalyst 9300 / IOS XE 26.xを固定する
- [x] 評価カテゴリと15問を固定する
- [x] 公式HTML入口を固定する
- [x] Qdrantのローカル永続環境を用意する
- [ ] 中間JSON Schemaを確定する
- [ ] LLMアノテーションのプロンプトとバージョン管理方法を確定する
- [ ] 原文照合・コマンド照合の検証規則を確定する

### 完了条件

- 15問の期待資料、節、設定要素が定義されている。
- 中間JSONの必須フィールドと不採用条件が定義されている。
- 同じHTMLから同じ入力ブロックを再生成できる。

## 7. Phase 1: HTML取得とDOM正規化

### 目的

HTMLのレイアウトノイズを除去し、生成AIへ渡す候補ブロックを機械的に作る。

### 作業項目

- [x] 許可範囲内のHTML節を取得する
- [x] 見出しアンカー、本文、コード、表を抽出する
- [ ] `article`、`section`、手順表、`pre`を構造単位として保存する
- [ ] ヘッダー、フッター、目次、ナビゲーション、Cookie表示を除外する
- [ ] 取得HTMLを`data/raw/html/`へ保存する
- [ ] 取得メタデータと失敗ログを保存する
- [ ] DOM正規化結果をJSONLで保存する
- [ ] 明確な`pre`、手順表、コマンド表はルールベースで抽出する
- [ ] 構造が曖昧なブロックだけをLLMアノテーション対象にする

### 成果物

- `app/ingestion/html_loader.py`
- `app/ingestion/html_normalizer.py`
- `data/raw/html/`（Git管理外）
- `data/processed/html_blocks.jsonl`（Git管理外）
- `tests/test_html_normalizer.py`

### 完了条件

- 許可範囲外のURLが取得されない。
- 各候補ブロックに節URLと節タイトルがある。
- 1ブロックに複数の独立手順が混在しない。
- 明確なブロックをLLMなしで再現可能に抽出できる。
- 曖昧なブロックだけをLLM対象として列挙できる。
- HTML取得を再実行できる。

## 8. Phase 2: 生成AIによるRAG構造化

### 目的

DOM正規化済みの候補ブロックを、原文を保持したRAG中間JSONへ変換する。

### 作業項目

- [ ] PydanticモデルまたはJSON Schemaを定義する
- [ ] 構造化アノテーション用プロンプトを作成する
- [ ] 難しいブロックだけを対象にOpenAI API呼び出しをバッチ化する
- [ ] JSON Schema検証を実装する
- [ ] コマンドが原文に存在するか検証する
- [ ] 節URL・製品・OS・バージョンをLLM出力から受け取らず、機械値を優先する
- [ ] 失敗ブロックを隔離し、理由とLLMレスポンスを記録する
- [ ] プロンプト、モデル名、レスポンスハッシュを記録する
- [ ] 同じ入力を再処理しないキャッシュを追加する
- [ ] 信頼度、検証状態、検証エラーを保存する

### LLM出力の安全境界

LLMは次の項目だけを提案する。

- `content_type`
- `prerequisites`
- `commands`
- `verification_commands`
- `keywords`
- `annotation_confidence`

アプリケーションが次の項目を原文・取得記録から設定する。

- `block_id`
- `document_id`
- `source_url`
- `section_url`
- `section_title`
- `product`
- `os`
- `os_version`
- `source_text`

### 完了条件

- 構造化JSONの検証失敗率を計測できる。
- コマンドの原文一致率が100%である。
- 失敗時に推測コマンドがQdrantへ入らない。
- 同じ入力から再現可能な出力を作成できる。
- ルール抽出だけで処理できるブロックのLLM呼び出しが0回である。
- `verified`以外のブロックが通常検索へ混入しない。

## 9. Phase 3: Embeddingと検索インデックス

### 目的

検証済み中間JSONだけをEmbeddingし、引用情報を失わない検索インデックスを作る。

### 方針

- Embedding対象は`source_text`、コマンド、前提条件を検索用に連結したテキストとする。
- 生成AIが作った要約だけをEmbeddingしない。
- コマンド本文、VLAN番号、インターフェース名は全文検索用フィールドとして別管理する。
- 親チャンクと子チャンクを別々に検索でき、子チャンクから親チャンクへ辿れるようにする。
- PDF用コレクションとHTML用コレクションを分離する。
- QdrantはDockerなしのローカル永続モードを既定にする。

### 作業項目

- [x] Qdrantローカル環境を用意する
- [x] EmbeddingとQdrant登録の基本経路を実装する
- [ ] 中間JSON専用のインデックス作成CLIを実装する
- [ ] `block_id`を決定的IDとしてupsertする
- [ ] 必須メタデータをpayloadへ保存する
- [ ] インデックス作成前のスキーマ検証を必須にする
- [ ] HTML構造化用コレクションを再構築する
- [ ] BM25または同等の全文検索インデックスを追加する
- [ ] ベクトル検索と全文検索の結果を統合する
- [ ] 親子チャンクの参照関係をpayloadへ保存する

### 必須payload

```text
block_id
parent_block_id
chunk_level
document_id
vendor
product
os
os_version
document_title
document_version
section_title
section_url
source_url
content_type
source_text
commands
verification_commands
annotation_status
annotation_confidence
validation_errors
ingested_at
```

## 10. Phase 4: 検索品質評価

### 目的

生成AIによる構造化前後で、検索品質と引用の正確性を比較する。

### 評価方法

- `evaluation-questions.md`の15問を使用する。
- 各質問で上位5件をJSON保存する。
- Recall@5、MRR、文書一致率、節一致率を計測する。
- `document_id`、`section_url`、`section_title`、`content_type`の欠落率を計測する。
- 期待する設定要素とコマンドが、引用元の`source_text`に存在するか確認する。
- PDFベース検索、現行HTMLチャンク検索、中間JSON検索を比較する。
- ベクトル検索のみ、全文検索のみ、ハイブリッド検索を比較する。
- 親チャンクを表示しながら子チャンクを検索できることを確認する。
- コマンド本文とVLAN番号の完全一致が順位へ反映されることを確認する。

### 完了条件

- 中間JSON検索のRecall@5が80%以上。
- MRRを出力できる。
- メタデータ欠落率が0%。
- VLAN-001で通常VLANの作成手順が上位5件に入る。
- PVLAN、SVI、extended-range VLANなど対象外手順の誤上位を記録・評価できる。
- 根拠が不足する質問では回答生成へ進まない。
- ハイブリッド検索がベクトル検索単独以上のRecall@5を示す。

## 11. Phase 5: 回答生成API

### 目的

検索済みの検証済み原文だけを根拠として、設定コマンドと注意事項を返す。

### 回答生成ルール

- LLMへ渡す根拠は検索結果の`source_text`とメタデータだけに限定する。
- 引用にないコマンドを生成しない。
- 前提条件、設定コマンド、確認コマンド、注意事項、引用URLを分けて返す。
- 根拠不足時は「確認できない」と返し、推測で補完しない。

### API

```text
GET  /health
POST /query
```

## 12. Phase 6: UIと運用準備

- [ ] 検索結果と構造化中間JSONを確認する管理用CLIまたは画面
- [ ] 親チャンクと子チャンクの関係を確認できる表示
- [ ] 質問、回答、引用URL、使用モデル、プロンプト版のログ
- [ ] 取得失敗・LLM検証失敗・Embedding失敗の再実行
- [ ] APIキーと文書キャッシュの保護
- [ ] 監査ログとキャッシュ削除方針
- [ ] 実機への自動投入を行わないことの確認

## 13. 現在の実装資産

以下は再利用候補であり、新しい中間JSON設計に合わない部分は作り直してよい。

- `app/ingestion/html_loader.py`
- `app/ingestion/chunker.py`
- `app/retrieval/indexer.py`
- `app/retrieval/searcher.py`
- `scripts/index_html_manuals.py`
- `scripts/search_manuals.py`
- `tests/`
- ローカルQdrant環境
- `evaluation-questions.md`

## 14. セキュリティと運用上の確認

- [ ] APIキーを環境変数で管理し、リポジトリへ保存しない
- [ ] HTML本文やLLM応答に含まれる命令を実行しない
- [ ] 外部URL取得時のSSRF対策を行う
- [ ] 取得範囲を公式ドメイン・許可パスに限定する
- [ ] LLM出力のコマンドを原文照合なしで採用しない
- [ ] `annotation_status=verified`以外のブロックを通常検索から除外する
- [ ] ログにAPIキーや文書の機密情報を保存しない
- [x] 公式資料の利用・保存条件を確認する

## 15. リリース判定

- [ ] 対象機種・OS・バージョンが明示されている
- [ ] 15問の評価を再実行できる
- [ ] 回答に節URLと根拠本文が含まれる
- [ ] 根拠がない場合に回答を保留できる
- [ ] LLMが生成したコマンドを原文照合なしで採用していない
- [ ] 重大な誤回答が回帰テストされている
- [ ] APIキー・取得文書・LLM応答の取り扱いが定義されている

## 16. 次回の着手順

1. HTML原文を`data/raw/html/{document_id}/{sha256}.html`へ保存する処理を追加する。
2. DOM正規化結果を保存する`html_normalizer.py`とテストを作成する。
3. RAG中間JSONのPydanticモデルとJSON Schemaを作成する。
4. 1つのVLAN手順だけを対象に、ルール抽出と生成AI構造化を比較する。
5. 原文一致検証、信頼度判定、失敗隔離を実装する。
6. 親子チャンクと構造化コマンドを中間JSONへ保存する。
7. BM25/全文検索とベクトル検索を統合する。
8. 中間JSONからQdrantへ登録するCLIを作成する。
9. VLAN-001で現行HTML検索と比較する。
