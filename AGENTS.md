# Tushare-Integration

> 面向 AI 编程助手的项目说明文件。阅读本文前，请默认你对本项目一无所知。

## 项目概述

Tushare-Integration 是一个将 [Tushare Pro](https://tushare.pro/) 金融数据接口的数据同步到本地数据库的 Python 采集工具。项目基于 [Scrapy](https://scrapy.org/) 框架构建，支持全量同步与增量更新，覆盖 A 股、指数、期货等多个资产类别的行情、财务、市场参考等数据。

当前版本：`0.1.7`

## 技术栈

- **Python 3.11**
- **Scrapy 2.12.0** —— 数据采集与调度框架
- **Pandas / NumPy** —— 数据清洗与处理
- **Pydantic / Pydantic Settings** —— 配置校验与环境变量注入
- **Typer** —— 命令行接口
- **Rich** —— 命令行输出美化
- **Jinja2** —— 数据库 SQL 模板渲染
- **SQLAlchemy 2.0** —— 数据库 ORM（MySQL / Doris）
- **clickhouse-connect** —— ClickHouse 原生驱动
- **PyYAML** —— 配置文件解析
- **mkdocs-material** —— 文档站点生成

## 项目结构

```
.
├── main.py                          # CLI 入口
├── config.yaml                      # 主配置文件（Scrapy + 数据库 + Tushare）
├── jobs.yaml                        # Job 定义：将多个 spider 分组
├── pyproject.toml                   # Python 包元数据与工具配置（black/isort/pytest）
├── scrapy.cfg                       # Scrapy 项目配置
├── Dockerfile                       # 构建镜像（python:3.11.9-slim-bullseye）
├── schema_upgrade.py                # 旧版 schema 迁移脚本（一般无需使用）
│
├── tushare_integration/
│   ├── commands.py                  # Typer CLI 命令定义（run job / run spider / list）
│   ├── manager.py                   # CrawlManager：加载配置、解析依赖、顺序执行 spider、发送报告
│   ├── settings.py                  # Pydantic Settings 模型，支持环境变量覆盖
│   ├── db_engine.py                 # 数据库引擎抽象（ClickHouse / MySQL / Doris）
│   ├── items.py                     # Scrapy Item：仅包含一个 `data` 字段（DataFrame）
│   ├── pipelines.py                 # 4 条 Item Pipeline：填充缺失值 → 类型转换 → 写入数据库 → 记录日志
│   ├── middlewares.py               # 下载器中间件：自定义重试与限流处理
│   ├── reporters.py                 # 报告模块：目前仅实现飞书 Webhook 通知
│   ├── spiders/
│   │   ├── tushare.py               # 基类：TushareSpider / DailySpider / TSCodeSpider / FinancialReportSpider
│   │   ├── stock/                   # A 股相关 spider（基础、行情、财务、市场、特色等）
│   │   ├── index/                   # 指数相关 spider
│   │   └── future/                  # 期货相关 spider
│   └── schema/                      # 每个 API 对应的 YAML 表结构定义
│       ├── stock/                   # 按资产类别和模块组织
│       ├── index/
│       ├── future/
│       └── template/                # Jinja2 SQL 模板
│           ├── clickhouse/
│           ├── doris/
│           ├── mysql/
│           └── starrocks/
│
├── deploy/
│   └── tushare-integration/         # Helm Chart，用于 K8s CronJob 部署
├── scripts/
│   ├── generate_jobs.py             # 根据 spider 列表生成 jobs.yaml
│   └── batch_run.sh                 # kubectl 批量手动触发 CronJob
├── tests/
│   └── create_tables.py             # 一键创建所有数据表（非单元测试）
└── docs/                            # MkDocs 文档源文件
```

## 核心架构说明

### 1. 配置文件体系

- **`config.yaml`** 是运行时唯一主配置，包含 Tushare Token、数据库连接、Scrapy 设置、Reporter 配置等。
- **环境变量优先级高于配置文件**。支持的环境变量包括：
  - `TUSHARE_TOKEN`、`TUSHARE_POINT`
  - `DB_TYPE`、`DB_HOST`、`DB_PORT`、`DB_USER`、`DB_PASSWORD`、`DB_NAME`
  - `FEISHU_WEBHOOK`、`BATCH_ID`、`CONCURRENT_REQUESTS`、`CONCURRENT_ITEMS`
- `settings.py` 使用 Pydantic `BaseSettings` 并自定义了 `settings_customise_sources`，使 env 优先于文件配置。

### 2. Spider 继承体系

所有 spider 均继承自 `tushare_integration.spiders.tushare` 中的基类：

| 基类 | 用途 | 关键行为 |
|------|------|----------|
| `TushareSpider` | 所有 spider 的基类 | 构造 Tushare API 请求（POST JSON）、解析响应为 DataFrame、自动建表、加载 schema |
| `DailySpider` | 按交易日采集 | 读取本地 `trade_cal` 表，找出目标表中缺失的交易日，逐日发起请求 |
| `TSCodeSpider` | 按股票代码采集 | 读取 `stock_basic`（或 `BASIC_TABLE`）中的 `ts_code` 列表，逐个请求 |
| `FinancialReportSpider` | 财务报表采集 | 积分 >= 5000 时使用 VIP 接口（按 `period` 全量拉取），否则按 `ts_code` 逐个拉取 |

### 3. Schema 定义

每个 spider 在 `tushare_integration/schema/` 下有一个同名的 `.yaml` 文件，格式示例：

```yaml
id: 5
api_name: daily
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

- `dependencies`：声明前置依赖 spider，`CrawlManager` 会自动按依赖顺序串行执行。
- `primary_key`：存在时，数据写入使用 upsert（去重后更新/插入）；不存在时直接 insert。
- `data_type` 支持：`str`、`int`、`float`、`number`、`date`、`datetime`、`json`。

### 4. Pipeline 处理流程

 Scrapy 的 `ITEM_PIPELINES` 按优先级从小到大执行：

1. **`TushareIntegrationFillNAPipeline` (298)** —— 根据 schema 的 `default` 填充 DataFrame 中的 `NaN` / `NaT`。
2. **`TransformDTypePipeline` (299)** —— 按 schema 的 `data_type` 将列转换为对应 Python / Pandas 类型。
3. **`TushareIntegrationDataPipeline` (300)** —— 去重（按 `primary_key`）后，调用 `DBEngine.upsert()` 或 `insert()` 写入数据库。
4. **`RecordLogPipeline` (301)** —— 统计写入条数，在 spider 关闭时将批次信息写入 `tushare_integration_log` 表。

### 5. 数据库引擎

`db_engine.py` 中定义了 `DBEngine` 抽象类，当前实现：

- **`ClickhouseEngine`** —— 使用 `clickhouse-connect` 原生驱动，`insert_df` / `query_df`。
- **`MySQLEngine`** / **`ApacheDorisEngine`** —— 基于 SQLAlchemy，使用 Jinja2 模板生成 SQL。

新增数据库支持的方法：在 `tushare_integration/schema/template/{db_type}/` 下提供三个 Jinja2 模板：
- `table.jinja2` —— `CREATE TABLE IF NOT EXISTS`
- `insert.jinja2` —— 插入语句
- `upsert.jinja2` —— 更新或插入语句（如 `REPLACE INTO`）

然后在 `DatabaseEngineFactory.create()` 中注册新引擎即可。

### 6. 重试与限流

`TushareRetryDownloaderMiddleware` 继承自 Scrapy 的 `RetryMiddleware`：
- 遇到 HTTP 错误码或 Tushare 返回的 `402xx` 限流码时，**固定等待 `retry_delay` 秒（默认 60s）**后重试。
- Tushare 的频次限制以分钟为周期，因此重试间隔大于 60 秒理论上可规避限流。

## 常用命令

项目使用 `python main.py` 作为统一入口，基于 Typer：

```bash
# 列出所有 spider
python main.py query list

# 运行单个 spider（支持正则匹配）
python main.py run spider "stock/basic/.*"
python main.py run spider "stock/quotes/daily"

# 运行一个 job（按 jobs.yaml 中的定义）
python main.py run job stock/basic
```

### 数据库表初始化

```bash
python tests/create_tables.py
```

该脚本会遍历所有 spider 的 schema 并调用 `CREATE TABLE IF NOT EXISTS`。

## 构建与部署

### Docker 镜像

```bash
docker build -t tushare-integration .
```

镜像默认运行 `python main.py`。生产使用时需将 `config.yaml` 和 `jobs.yaml` 挂载进容器：

```bash
docker run -v /path/to/config.yaml:/code/app/config.yaml \
           -v /path/to/jobs.yaml:/code/app/jobs.yaml \
           zhangbc/tushare-integration:0.1.7 \
           python main.py run job stock/basic
```

### Helm / Kubernetes

`deploy/tushare-integration/` 提供 Helm Chart：
- `values.yaml` 中定义 `cronjob` 列表（名称、Cron 表达式、spider 列表）以及 `configOverrides`。
- Chart 渲染为 Kubernetes `CronJob` 资源。
- `parallel_mode: true` 时，每个 spider 会单独生成一个 `CronJob`；否则一个 job 对应一个 `CronJob`，内部串行执行多个 spider。
- 注意：采集服务本身**不是并发安全的**，同一时间应避免多个实例对同一张表写入。

### CI/CD

- **构建镜像**：`.github/workflows/github-actions-build-image.yml` —— 在推送 tag 时使用 Kaniko 构建并推送到 Docker Hub（`zhangbc/tushare-integration`）。
- **构建文档**：`.github/workflows/github-actions-build-docs.yml` —— 在 `main` 分支推送时，使用 `mkdocs gh-deploy` 部署到 GitHub Pages。

## 代码风格

- **Black**：行宽 `120`，`skip-string-normalization = true`（保留引号风格）。
- **isort**：`profile = "black"`，`multi_line_output = 3`，行宽 `120`。
- 项目内大量使用中文注释与中文 docstring，文档也以中文为主。
- Python 3.11 特性：使用 `str | None` 等联合类型语法。

## 测试说明

- 当前项目中**没有传统的 pytest 单元测试**。
- `tests/create_tables.py` 是一个辅助脚本，用于一次性初始化所有数据库表，可在部署新环境时运行验证。
- `pyproject.toml` 中已配置 `tool.pytest.ini_options`，预留了日志格式和 Pydantic 弃用警告过滤。

## 安全与敏感信息

- **Tushare Token**、**数据库密码**、**飞书 Webhook URL** 均为敏感信息，应通过**环境变量**注入，避免直接写入 `config.yaml`。
- `config.yaml` 中的 `tushare_token` 和 `feishu_webhook` 默认为空字符串，生产环境务必通过 `TUSHARE_TOKEN`、`FEISHU_WEBHOOK` 等环境变量提供。
- 数据库 URI 在 `DatabaseConfig.get_uri()` 中拼接，密码明文传输，建议在可信内网使用。

## 给 AI 助手的关键提示

1. **修改 spider 时**：通常需要同时检查/修改对应的 `tushare_integration/schema/{spider_name}.yaml`，确保字段、主键、依赖关系与实际 API 返回一致。
2. **修改表结构时**：数据库引擎在 spider 启动时执行 `CREATE TABLE IF NOT EXISTS`，**不会自动 ALTER 表**。若列发生变更，需要手动在数据库端修改表结构，或删除旧表让 spider 重新创建。
3. **新增数据库支持时**：只需要在 `schema/template/` 下新建目录并提供 `table.jinja2`、`insert.jinja2`、`upsert.jinja2`，然后在 `db_engine.py` 的工厂中注册。
4. **新增 reporter 时**：继承 `reporters.Reporter`，实现 `send_report()` 和 `from_settings()`，然后在 `config.yaml` 的 `reporters` 列表中配置完整类路径。
5. **避免并发写入**：同一个 spider 或 job 不要同时启动多个实例，否则可能导致数据重复或冲突（除非目标数据库 upsert 语义能完全覆盖）。
6. **依赖解析**：非并行模式下，`CrawlManager` 会自动根据 schema 的 `dependencies` 字段递归解析并串行执行。若开启 `parallel_mode`，依赖解析关闭，需人工保证执行顺序。
