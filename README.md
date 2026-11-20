# configbrain

Cisco公式マニュアルを根拠として、日本語の要件から評価用のコンフィグ案を生成するCLIです。
要件をすべて満たせない場合も、根拠のある部分設定、不足情報、確認事項を提示します。
**実機への接続・設定投入は行いません。生成案をそのまま本番へ適用しないでください。**

## 対象と必要なもの

- 対象: Cisco Catalyst 9300 / IOS XE 26.x。
- Python 3.12以上、[uv](https://docs.astral.sh/uv/)、資料取得用のwget。
- 同じ製品・リリースのコンフィグガイドとコマンドリファレンス。
- 検証済みのガイドOKF・原本PDF、リファレンスJSON・保存HTML。
- 上記の資料一式と同じ世代のローカルQdrant索引、埋め込みモデル。
- HTTPSのChat Completions互換LLM APIと認証情報。

Qdrantサーバーは不要です。ローカルの永続DBを使用します。
資料が未準備の場合は、[実装ガイドの資料準備手順](./docs/implementation-guide.md#3-資料の準備と更新)を先に実施してください。
生成コマンドは資料取得・索引登録を行いません。

## セットアップ

リポジトリのルートで依存パッケージを準備します。

```sh
uv sync
```

次の環境変数を設定してください。ローカルの`.env`も使用でき、環境変数が優先されます。

| 変数 | 内容 |
| --- | --- |
| `CLOUD_GPT_API_URL` | HTTPSのChat Completions互換APIの接続先 |
| `CLOUD_GPT_API_KEY` | `api-key`ヘッダーに設定する認証キー |
| `LLM_MODEL` | 必要な場合のモデル名。省略可 |

**要件・資料本文・生成案等は設定先APIへ送信されます。機密情報を含む要件を送信しないでください。**
認証情報、`.env`、原本資料、ローカルDB、実行結果はGitへ追加しないでください。
埋め込みモデルは事前に配置してください。未配置の場合はモデルのダウンロードが必要になることがあります。

## コンフィグ案を生成する

```sh
uv run python -m configbrain.pipeline \
  --requirement 'Catalyst 9300でVLAN120を生成し、GigabitEthernet1/0/1をアクセスポートとしてVLAN 120に割り当ててください。' \
  --output-dir data/runs/vlan120
```

UTF-8の要件ファイルを使う場合は、`--requirement`の代わりに
`--requirement-file requirements.txt`を指定します。
処理中は段階名を標準エラーへ表示します。CPUでの検索やLLM応答には時間がかかります。

### 結果の読み方

出力先の`report.md`を開いてください。主に次を確認できます。

- 要件項目ごとの設定案、確認コマンド、出典。
- 不足情報、前提条件、制約、保留した手順。
- 根拠付きの設定と分離された推論候補。
- 引用照合・説明審査の状態、各処理段階の成否。

**レポートが生成されたことと、要件が満たされたことは別です。**
`generation_status: complete`だけでなく、`fulfillment_status`と未確認事項を確認してください。
未指定の値を必要とする設定はテンプレートとして分離されます。
インターフェイスの有効化が要件にない場合、`no shutdown`は実行用設定に追加しません。

引用照合は正しさの全面保証ではありません。ガイドはPDFのテキスト、
リファレンスは保存HTMLと照合しますが、実機への適合性は人による確認が必要です。
検索結果のPDFページは章先頭であり、引用ごとの照合済み物理ページとは区別してください。
説明の審査が完了しない場合は、その説明を保留して設定案を残すことがあります。

出力先にはレポートのほか、`selection.json`、`search-results.json`、`contexts.json`、
`config-draft.json`、`pipeline-state.json`を保存します。
要件、引用本文、LLM応答、ローカル資料パスを含むため、公開可能なログとして扱わないでください。

### 主なオプション

| オプション | 用途・既定値 |
| --- | --- |
| `--catalog` | カタログ。`data/catalog/c9300-iosxe26.json` |
| `--database` | ローカルDB。`data/qdrant/db` |
| `--okf-root` | ガイドOKF。`data/okf` |
| `--source-pdf-root` | 原本PDF。`data/source_manuals` |
| `--reference-root` | リファレンスJSON・HTML。`data/command_references` |
| `--selection-metadata-root` | 章選定メタデータ。`data/selection_metadata` |
| `--limit` | 要件項目×選定章ごとの検索件数。20 |
| `--generation-rounds` | 生成・補足・意味審査それぞれの最大ラウンド数。3 |
| `--resume` | 同じ条件の実行を最初の未完了段階から再開 |

詳細は`uv run python -m configbrain.pipeline --help`で確認できます。
複数の生成・索引登録プロセスで同じローカルDBを同時に使用しないでください。

章の候補発見を補助するLLMメタデータは、検証済みの全ガイドに対して
`uv run python -m configbrain.generate_selection_metadata --all-guides`
で一括生成できます。鮮度検証済みの文書をスキップし、文書内の完了済み章もチェックポイントから再開します。
1冊だけの場合は`--document-id DOCUMENT_ID`を指定します。
未生成または古い文書はタイトル中心の選定へフォールバックし、
警告が`selection.json`と`pipeline-state.json`に記録されます。
生成時はマニュアル本文の抽出範囲を設定済みの外部LLM APIへ送信するため、
組織のデータ取り扱い方針を確認してください。各API応答後にクォータ関連ヘッダーを画面へ表示します。
メタデータは設定根拠ではなく、適用条件やCLIの確認には原文を使います。

## 失敗した場合と再開

途中で停止した場合も、完了済みのJSONと、失敗理由を示す診断レポートを残します。
同じ要件・オプション・出力先に`--resume`を追加すると、成果物を検証して再開します。
`--resume`なしでは既存出力を上書きしません。同じ出力先の同時実行は避けてください。

```sh
uv run python -m configbrain.pipeline \
  --requirement 'Catalyst 9300でVLAN120を生成し、GigabitEthernet1/0/1をアクセスポートとしてVLAN 120に割り当ててください。' \
  --output-dir data/runs/vlan120 \
  --resume
```

| 状況 | 対応 |
| --- | --- |
| ガイド・リファレンスや保存資料が不足 | 診断に従い、両資料と索引を準備する。片方だけでの続行はしません |
| `Search index generation differs from the current corpus` | 同じ資料・カタログで索引を再登録し、新しい出力先で実行する。`--resume`だけでは古い検索結果を更新しません |
| 選定時の不正な章ID、通信・認証エラー | 診断と接続設定を確認し、条件が同じなら`--resume`で再試行する |
| 警告付きでレポート生成済み | 保留・不足情報を確認する。完了済みの生成は`--resume`で再生成しません |
| 要件・資料・実行オプションを変更した | 既存結果を残し、新しい出力先で実行する |

資料の整合性確認は、起動・再開時にLLM呼び出しより前に行います。
認証情報が不要なのは次の単体表示処理であり、パイプライン再開では資料の事前確認も行います。

## 保存済み結果からレポートだけ作る

```sh
uv run python -m configbrain.report \
  --draft data/runs/vlan120/config-draft.json \
  --output data/runs/vlan120/report.md
```

LLM・Qdrantへの接続や元資料の読み直しは行いません。
これは保存済み結果の表示であり、設定や根拠を改めて検証する処理ではありません。

## 開発・保守向け文書

- [基本設計書](./docs/basic-design.md): 目的、全体構成、処理フロー、保証範囲。
- [詳細設計書](./docs/detailed-design.md): モジュール、データ契約、検証・状態・再開の仕様。
- [実装ガイド](./docs/implementation-guide.md): 開発原則、資料準備、個別CLI、テスト、変更時の手順。
- [進捗管理](./docs/progress.md): 現在地、課題、評価履歴、中断・再開時の着手点。
