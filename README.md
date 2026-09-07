# ConfigBrain

ConfigBrain retrieves evidence from official network-device documentation and prepares grounded configuration guidance.

## Development setup

Requirements:

- Python 3.12 or newer
- `uv`
- Docker, for the local Qdrant service

Create the environment and install dependencies:

```bash
uv sync
cp .env.example .env
```

Run the initial test suite:

```bash
uv run pytest
```

Start the local Qdrant service:

```bash
docker compose up -d
curl http://localhost:6333/healthz
```

Stop the service while keeping its data:

```bash
docker compose down
```

Qdrant data is stored in the named `qdrant_storage` volume. The service exposes
the HTTP API on port `6333` and the gRPC API on port `6334`.

PDF files and generated indexes are kept outside version control under `data/raw/` and `data/processed/`.

Download the three pinned Cisco manuals and record their checksums:

```bash
python scripts/download_manuals.py
```

Use `--force` to replace existing PDFs. The files are saved under `data/raw/` and
download metadata is saved in `data/manuals.json`.

Create the Qdrant index after starting Qdrant and setting `OPENAI_API_KEY`:

```bash
./scripts/index_manuals.py --all
```

Validate PDF loading and chunking without calling Qdrant or OpenAI:

```bash
./scripts/index_manuals.py --all --dry-run
```

To index one manual, use its path from `data/raw/`:

```bash
./scripts/index_manuals.py --pdf data/raw/c9300_iosxe26_vlan_cg.pdf
```
