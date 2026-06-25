# Google Meridian MCP Server [v0.1.0]

FastMCP server exposing a focused set of Google Meridian model-analysis tools for agents.

This project wraps Google Meridian models behind a small, read-only MCP surface so agents can
discover available models, inspect model setup, and request structured analysis outputs without
needing to understand Meridian's internal APIs directly.

It is designed for both local development and containerized deployment. The current tool surface
covers model discovery, model overview metadata, training data extraction, channel summaries,
contribution outputs, adstock decay outputs, and response curves.

## Local Setup

### 1. Create a Python environment

Meridian currently targets Python 3.12+.

```bash
python3.12   -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
```

### 2. Install the project

```bash
pip install -e ".[dev]"
```

### 3. Configure `.env`

Create `.env` in the repository root.

```bash
cp .env.example .env
```

For local filesystem-backed development, a minimal `.env` looks like this:

```dotenv
MCP_TRANSPORT=streamable-http
MCP_HOST=127.0.0.1
MCP_PORT=8000
PERSISTENCE_BACKEND=local
LOCAL_MODELS_ROOT=./models
MODEL_CACHE_ROOT=/tmp/mmm-models
DISCOVERY_TTL_SECONDS=7200
RESULT_CACHE_ENABLED=true
```

`.env` belongs at the project root because the runtime loads it from there explicitly.

### 4. Add a model

Both flat and nested layouts are supported. Nested directories are usually clearer.

```text
models/
├── geo-revenue/
│   └── model.binpb
└── experiment-a/
    └── model.pkl
```

The catalog will expose those examples as model IDs like `geo-revenue` and `experiment-a`.

### 5. Run the server

```bash
python -m google_meridian_mcp_server.server
```

For interactive Inspector testing, the repository includes `fastmcp.json`, so from the project root you can run:

```bash
fastmcp dev inspector / npx @modelcontextprotocol/inspector
```

The external config value remains `streamable-http`. Internally, the current FastMCP runtime is started with its HTTP transport and binds to `MCP_HOST` and `PORT` or `MCP_PORT`.

`fastmcp dev inspector` does not use your activated `.venv` or Conda environment directly. FastMCP launches Inspector servers through a `uv run` subprocess, so inspector-specific dependencies must be declared in `fastmcp.json` or passed with CLI flags such as `--project`, `--with-editable`, and `--with`.

For this repository, the most reliable way to test with your already-working local environment is to start the server yourself:

```bash
source .venv/bin/activate
python -m google_meridian_mcp_server.server
```

Then open the MCP Inspector separately and connect it to `http://localhost:8000/mcp` over HTTP instead of using `fastmcp dev inspector`.

## Tool Surface

The current MCP surface includes:

- `list_models`
- `get_model_overview`
- `get_training_data`
- `get_channel_summary`
- `get_contribution`
- `get_adstock_decay`
- `get_response_curves`

Every tool is annotated as read-only and uses typed parameters with documented validation metadata
so the generated schema is stricter and easier for agents to call correctly.

Tool responses are canonical JSON payloads. The row-oriented analysis tools return a compact
**columnar** envelope: `model_id`, a selector field (`output_type` for analysis tools, or
`datasets`/`dataset` for training data), `columns` (the ordered column names), `rows` (a list of
positional value lists, one per row), and `row_count`. There is no `data` key and no
`result_metadata` block. Measure floats are rounded to 6 significant figures.

`get_model_overview` returns the model's time range, geo scope, channel/input groups, flattened data schema, and the supported dataset/output-type values for the other analysis tools.

`get_training_data` accepts one or more dataset keys and returns a single merged result set for the requested selections.

Grouped analysis tools return **posterior-only** rows. Prior rows are removed from tool results,
and the transport payloads do not include a `distribution` field.

`get_channel_summary` exposes:

- `baseline_summary_metrics`
- `paid_summary_metrics`
- `roi`
- `cpik`
- `marginal_roi`
- `marginal_cpik`

`get_adstock_decay` exposes:

- `adstock_decay`
- `alpha_summary`

`get_response_curves` exposes:

- `response_curves`, which returns numeric curve rows including spend, spend multiplier, metric,
  and incremental outcome
- `response_curve_summary`, which returns numeric summarized rows keyed by channel, spend, and
  spend multiplier with `mean`, `ci_lo`, and `ci_hi`

## Quality Checks

Run tests:

```bash
pytest
```

Run Ruff:

```bash
ruff check src tests
ruff format src tests
```

## GCS Notes

When using the GCS backend, authenticate with Application Default Credentials locally:

```bash
gcloud auth application-default login
```

Then set these variables in `.env`:

```dotenv
PERSISTENCE_BACKEND=gcs
GCS_BUCKET=my-project.appspot.com
GCS_MODELS_PREFIX=models
```

## Docker

Build locally:

```bash
docker build -t google-meridian-mcp-server .
```

Run locally in Docker:

```bash
docker run --rm -p 8080:8080 --env-file .env -e MCP_HOST=0.0.0.0 google-meridian-mcp-server
```

The container listens on `0.0.0.0` and respects the injected `PORT` environment variable.

## Cloud Run Deployment

Cloud Run is usually the best fit when this server is deployed with the **GCS backend**. That
keeps model files outside the container image, avoids rebuilds when models change, and works well
with Cloud Run's ephemeral filesystem.

Before deploying:

1. Create or choose a GCS bucket and prefix that hold your Meridian models.
2. Grant the Cloud Run service account access to read those objects
   (for example, `roles/storage.objectViewer`).
3. Create an Artifact Registry repository for the image if you do not already have one.

Build and publish the container:

```bash
gcloud builds submit \
  --tag REGION-docker.pkg.dev/PROJECT_ID/REPOSITORY/google-meridian-mcp-server
```

Deploy to Cloud Run:

```bash
gcloud run deploy google-meridian-mcp-server \
  --image REGION-docker.pkg.dev/PROJECT_ID/REPOSITORY/google-meridian-mcp-server \
  --region REGION \
  --platform managed \
  --service-account CLOUD_RUN_SERVICE_ACCOUNT \
  --set-env-vars=MCP_TRANSPORT=streamable-http,MCP_HOST=0.0.0.0,PERSISTENCE_BACKEND=gcs,GCS_BUCKET=MY_BUCKET,GCS_MODELS_PREFIX=models,MODEL_CACHE_ROOT=/tmp/mmm-models,DISCOVERY_TTL_SECONDS=7200,RESULT_CACHE_ENABLED=true
```

Cloud Run injects the `PORT` environment variable automatically, and the server already uses that
value when it starts its HTTP transport.

If you need a public endpoint, add `--allow-unauthenticated` to the deploy command. If the service
should stay private, keep IAM restricted and put it behind your existing gateway or identity layer.

Using the `local` backend on Cloud Run is only practical when models are baked into the image at
build time. For most deployments, `PERSISTENCE_BACKEND=gcs` is the safer and simpler default.
