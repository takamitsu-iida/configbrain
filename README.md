# ConfigBrain

ConfigBrain retrieves evidence from official network-device documentation and prepares grounded configuration guidance.

## Development setup

Requirements:

- Python 3.12 or newer
- `uv`

Create the environment and install dependencies:

```bash
uv sync
cp .env.example .env
```

Run the initial test suite:

```bash
uv run pytest
```

QdrantはDockerなしで、ローカルの `data/qdrant/` に永続化して使用します。
このディレクトリは自動作成され、バージョン管理対象外です。

PDF files and generated indexes are kept outside version control under `data/raw/` and `data/processed/`.

Download the three pinned Cisco manuals and record their checksums:

```bash
uv run python scripts/download_manuals.py
```

Use `--force` to replace existing PDFs. The files are saved under `data/raw/` and
download metadata is saved in `data/manuals.json`.

Create the Qdrant index after setting `OPENAI_API_KEY`:

```bash
./scripts/index_manuals.py --all
```

PDFを再解析して古いチャンクも削除し、インデックスを作り直す場合:

```bash
./scripts/index_manuals.py --all --recreate
```

既存のQdrantサーバーへ接続する場合だけ、次のように指定します。

```bash
./scripts/index_manuals.py --all --qdrant-url http://localhost:6333
```

Validate PDF loading and chunking without calling Qdrant or OpenAI:

```bash
./scripts/index_manuals.py --all --dry-run
```

To index one manual, use its path from `data/raw/`:

```bash
./scripts/index_manuals.py --pdf data/raw/c9300_iosxe26_vlan_cg.pdf
```

検索プロトタイプは、自然言語の質問に対する上位5件のチャンクをJSONで返します。
`OPENAI_API_KEY` とローカルQdrantインデックスが必要です。

```bash
./scripts/search_manuals.py "VLAN 100を作成する方法は？"
```

検索件数やメタデータフィルターを指定することもできます。

```bash
./scripts/search_manuals.py \
	"アクセスポートをVLAN 100に割り当てる方法は？" \
	--top-k 5 \
	--product catalyst9300 \
	--os-version 26.x
```

HTMLマニュアルのインデックスを作成する場合は、PDF用とは別のコレクションへ登録します。
まず取得とチャンク化だけを確認するには、次を実行します。

```bash
./scripts/index_html_manuals.py --all --dry-run
```

OpenAI Embeddingを使って登録する場合:

```bash
./scripts/index_html_manuals.py --all --recreate
```

HTMLコレクションを検索する場合:

```bash
./scripts/search_manuals.py \
	"VLAN 100を作成する方法は？" \
	--collection configbrain_html_documents
```
