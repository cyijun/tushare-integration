# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common Commands

```bash
# List all spiders
python main.py query list

# Run a single spider (supports regex/wildcards)
python main.py run spider "stock/basic/stock_basic"
python main.py run spider "stock/quotes/.*"

# Run a job (group of spiders defined in jobs.yaml)
python main.py run job stock/basic

# Create all database tables from schemas
python tests/create_tables.py

# Format code
black .
isort .

# Run tests (limited traditional test suite; mostly integration)
pytest

# Build Docker image
docker build -t tushare-integration .

# Serve documentation locally
mkdocs serve
```

## High-Level Architecture

### Scrapy + Typer Hybrid

The project is a **Scrapy**-based data pipeline wrapped in a **Typer** CLI. `main.py` bootstraps `CrawlManager` (in `manager.py`), which loads `config.yaml` and orchestrates spider execution via `scrapy.crawler.CrawlerProcess`.

### Configuration System

- **`config.yaml`** is the single runtime config (Tushare token, DB credentials, Scrapy settings, reporters).
- **`jobs.yaml`** groups spiders into named jobs for batch execution.
- **Environment variables override file config**. Priority is defined in `settings.py` via Pydantic `settings_customise_sources`.
- Key env vars: `TUSHARE_TOKEN`, `TUSHARE_POINT`, `DB_TYPE`, `DB_HOST`, `DB_PORT`, `DB_USER`, `DB_PASSWORD`, `DB_NAME`, `FEISHU_WEBHOOK`, `CONCURRENT_REQUESTS`.

### Spider Inheritance Hierarchy

All spiders live under `tushare_integration/spiders/` and inherit from bases in `tushare_integration/spiders/tushare.py`:

- **`TushareSpider`** — Base class. Constructs POST JSON requests to Tushare API, parses responses into `TushareIntegrationItem` (a DataFrame), auto-creates DB tables, and loads schema YAML.
- **`DailySpider`** — Iterates over missing trading days from local `trade_cal` table and issues one request per day. Use `custom_settings.MIN_CAL_DATE` to bound the range.
- **`TSCodeSpider`** — Reads `ts_code` list from a configured basic table (default `stock_basic`) and issues one request per code.
- **`FinancialReportSpider`** — Chooses strategy based on Tushare points: >= 5000 uses VIP period-based bulk API; below uses per-`ts_code` requests.

### Schema-Driven Data Model

Every spider has a corresponding YAML schema at `tushare_integration/schema/{spider_name}.yaml`:

```yaml
name: daily
comment: 日线行情
dependencies:
  - stock/basic/stock_basic
  - stock/basic/trade_cal
primary_key:
  - ts_code
  - trade_date
columns:
  - name: ts_code
    data_type: str
    length: 16
    default: ""
    comment: 股票代码
```

- `dependencies`: CrawlManager auto-resolves and runs dependent spiders first (unless `parallel_mode: true`).
- `primary_key`: When present, pipeline uses `upsert` (deduplicate + update/insert); otherwise plain `insert`.
- `data_type` values: `str`, `int`, `float`, `number`, `date`, `datetime`, `json`.

### Pipeline Chain

`ITEM_PIPELINES` in `settings.py` executes in priority order (lower = earlier):

1. **`TushareIntegrationFillNAPipeline` (298)** — Fills NaN/NaT with schema defaults.
2. **`TransformDTypePipeline` (299)** — Converts DataFrame columns to schema-declared types.
3. **`TushareIntegrationDataPipeline` (300)** — Writes to DB via `DBEngine` (upsert if `primary_key` exists).
4. **`RecordLogPipeline` (301)** — Counts rows and writes a batch summary to `tushare_integration_log`.

### Database Engine Abstraction

`db_engine.py` defines `DBEngine` with implementations for **ClickHouse**, **MySQL**, and **Apache Doris** (StarRocks templates exist but reuse Doris engine). SQL is generated via Jinja2 templates in `tushare_integration/schema/template/{db_type}/`:

- `table.jinja2` — `CREATE TABLE IF NOT EXISTS`
- `insert.jinja2` — bulk insert
- `upsert.jinja2` — upsert/replace logic

To add a new database: create a template directory + register the engine class in `DatabaseEngineFactory`.

### Retry & Rate Limiting

`TushareRetryDownloaderMiddleware` extends Scrapy `RetryMiddleware`. On HTTP errors or Tushare `402xx` rate-limit responses, it waits `RETRY_DELAY` seconds (default 60s) before retrying. Tushare limits are per-minute, so a 60s+ delay generally avoids sustained throttling.

### Deployment Artifacts

- `Dockerfile` — Python 3.12 slim image; mounts `config.yaml` and `jobs.yaml` at runtime.
- `deploy/tushare-integration/` — Helm chart for Kubernetes CronJobs. When `parallel_mode: true`, each spider becomes its own CronJob; otherwise a job groups spiders into one CronJob and runs them sequentially.
- `.github/workflows/` — Kaniko-based image build on tag push; MkDocs deploy to GitHub Pages on `main` push.

### Code Style

- Black with line length 120 and `skip-string-normalization = true`.
- isort with `profile = "black"`.
- Project uses Chinese comments and docstrings extensively.

### Important Constraints

- **Not concurrency-safe**: Do not run multiple instances of the same spider/job simultaneously against the same table.
- **Schema changes do not auto-migrate**: Spiders run `CREATE TABLE IF NOT EXISTS` on startup. If a column changes, manually alter the table or drop and recreate it.
- **No traditional unit tests**: `tests/create_tables.py` is a deployment helper, not a test suite.
