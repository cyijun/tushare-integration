# Tushare Integration 代码审查问题清单

> 审查日期: 2026-05-25
> 审查方式: 4个并行agent + 人工深度阅读
> 覆盖范围: `tushare_integration/`全部Python源码、`schema/`模板、配置、部署、CI/CD

---

## 统计

| 严重级别 | 数量 |
|---------|------|
| 高严重 | 17 |
| 中严重 | 19 |
| 低严重 | 16 |
| **总计** | **52** |

---

## 高严重问题

### 1. SQL注入 - f-string拼接SQL（多处）

- **文件**: `spiders/stock/quotes.py`, `spiders/stock/special.py`, `spiders/index/quotes.py`, `manager.py` 等
- **描述**: 大量spider使用f-string直接拼接SQL，完全没有参数化查询。
- **具体位置**:
  - `spiders/stock/quotes.py:200-221` - StockMin的ts_code直接拼入SQL
  - `spiders/stock/special.py:26-39` - CyqChipsSpider的ts_code拼入SQL
  - `spiders/index/quotes.py:21` - IndexDailySpider查询index_basic
  - `manager.py:133` - 报告SQL拼接batch_id
- **修复方向**: 使用SQL参数化查询，或至少对标识符做严格正则校验。

### 2. SQL注入 - Jinja2 DDL模板

- **文件**: `tushare_integration/schema/template/*/table.jinja2`, `db_engine.py`
- **描述**: `db_name`、`table_name`、`column.name`、`column.comment`等通过`{{ }}`直接拼接到CREATE TABLE语句，无转义或校验。虽然insert/upsert使用了`:column`参数化，但DDL完全无防护。
- **修复方向**: 对标识符添加正则校验（`^[a-zA-Z_][a-zA-Z0-9_]*$`），对comment启用Jinja2 autoescape。

### 3. 数据库连接泄漏

- **文件**: `db_engine.py`
- **行号**: 53 (SQLAlchemyEngine), 109 (ClickhouseEngine)
- **描述**: `SQLAlchemyEngine`在`__init__`中`create_engine(...).connect()`创建连接后无`close()`或`__del__`。`ClickhouseEngine`同样无关闭。每个pipeline和spider独立创建引擎。
- **修复方向**: 实现`close()`方法，在pipeline的`close_spider`中调用；或使用连接池上下文管理。

### 4. 阻塞式sleep冻结Twisted事件循环

- **文件**: `middlewares.py`
- **行号**: 116, 123
- **描述**: `time.sleep(self.retry_delay)`在`process_response`中同步阻塞整个Twisted事件循环，所有并发请求被冻结。
- **修复方向**: 使用Twisted的`deferLater`返回Deferred，或设置`request.meta['download_delay']`让调度器重试。

### 5. JSON解析无异常处理

- **文件**: `middlewares.py`
- **行号**: 120-122
- **描述**: `json.loads(response.text)`在响应非JSON（HTML错误页、空体）时抛出异常，直接向上传播导致请求丢失且无重试。
- **修复方向**: 用try/except包裹JSON解析，解析失败时按HTTP错误码路径重试。

### 6. 非JSON响应导致崩溃

- **文件**: `spiders/tushare.py`
- **行号**: 59-63
- **描述**: `parse_response`直接`json.loads(response.text)`，Tushare返回502/504网关错误时抛`JSONDecodeError`。
- **修复方向**: 先检查`response.status`和Content-Type，再用try/except处理JSON解析。

### 7. 同步阻塞请求破坏异步架构

- **文件**: `spiders/tushare.py`
- **行号**: 101-116
- **描述**: `request_with_requests()`使用同步`requests.post()`，在Scrapy的Twisted异步环境中阻塞事件循环，且无timeout。
- **调用方**:
  - `spiders/stock/basic.py:36-52` - StockNameChangeSpider.parse()
  - `spiders/index/quotes.py:109-128` - IndexWeightSpider.parse()
  - `spiders/index/sw.py:42-58` - IndexMemberAllSpider.parse()
- **修复方向**: 使用`scrapy.Request`配合meta标记走下载器；或在线程池中执行requests。

### 8. 模块加载时硬读取配置文件

- **文件**: `settings.py`
- **行号**: 166-171
- **描述**: 模块导入时直接执行`yaml.safe_load(open('config.yaml'))`，文件不存在则整个模块导入失败（ImportError）。
- **修复方向**: 将配置加载延迟到首次使用时，或移除模块级副作用。

### 9. 配置验证错误 - port环境变量映射

- **文件**: `settings.py`
- **行号**: 47
- **描述**: `port`字段的env validator使用了`env_variable('DB_HOST')`而不是`'DB_PORT'`，环境变量`DB_PORT`无法正确覆盖端口配置。
- **修复方向**: 将`env_variable('DB_HOST')`改为`env_variable('DB_PORT')`。

### 10. int类型转换崩溃

- **文件**: `pipelines.py`
- **行号**: 88
- **描述**: `data[column["name"]].astype(int)`在列中存在NaN时抛`ValueError: Cannot convert NA to integer`。FillNAPipeline先执行但可能遗漏或后续产生新NaN。
- **修复方向**: 使用`astype('Int64')`（nullable int），或在转换前显式填充缺失值。

### 11. 日期静默数据损坏

- **文件**: `pipelines.py`
- **行号**: 92-93
- **描述**: `pd.to_datetime(..., errors='coerce')`将不可解析日期转为NaT，然后硬编码替换为`1971-01-01`。这会把原始数据中的真正无效日期变成虚假有效日期，导致数据分析错误。
- **修复方向**: 对不可解析日期记录警告并保留NULL，或明确丢弃该行，而非硬编码替换。

### 12. 飞书报告器缩进bug

- **文件**: `reporters.py`
- **行号**: 18-23
- **描述**: `if not self.webhook:`后的`return`缩进有误，webhook为空时body仍被构造，只是跳过了发送。
- **修复方向**: 修正缩进，确保webhook为空时直接return。

### 13. 记录日志schema误用

- **文件**: `pipelines.py`
- **行号**: 203
- **描述**: `RecordLogPipeline.close_spider`中`self.db_engine.insert(self.table_name, self.schema, statistics_data)`传入的是当前spider的数据表schema（如daily），而非日志表的schema。
- **修复方向**: 传入日志表自身的schema（`create_log_table`中定义的dict）。

### 14. 敏感信息明文配置

- **文件**: `config.yaml`, `deploy/tushare-integration/templates/configmap.yaml`
- **描述**: Tushare token、数据库密码、飞书webhook以空字符串默认值存在于config.yaml中。Helm chart将其挂载到Kubernetes ConfigMap（明文存储）。用户填入真实值后提交到Git将导致密钥泄露。
- **修复方向**:
  - `.gitignore`中忽略`config.yaml`，提供`config.example.yaml`模板
  - Helm chart中通过Kubernetes Secret传递敏感字段
  - 优先使用环境变量注入（settings.py已支持env override）

### 15. 飞书Webhook SSRF/信息泄露风险

- **文件**: `reporters.py`
- **行号**: 41
- **描述**: `requests.post(self.webhook, json=body)`未校验webhook URL域名。配置被篡改指向内网地址可导致SSRF；发送到第三方地址导致业务数据泄露。且无超时。
- **修复方向**: 校验URL限制为`https://open.feishu.cn`域名；添加`timeout=10`；校验响应状态码。

### 16. ReporterLoader动态导入安全风险

- **文件**: `reporters.py`
- **行号**: 55-63
- **描述**: `get_reporters`使用`importlib.import_module`和`getattr`动态加载配置中的任意类，无白名单限制。配置被篡改可加载任意模块中的任意类。
- **修复方向**: 维护允许加载的类的白名单，或限制只能从`tushare_integration.reporters`包中加载。

### 17. manager.py SQL字符串拼接

- **文件**: `manager.py`
- **行号**: 132-135
- **描述**: `get_report_content`中`batch_id`直接拼接到SQL字符串。虽然batch_id是内部生成的UUID，但如果未来被外部输入污染将存在注入风险。
- **修复方向**: 使用参数化查询传递`batch_id`。

---

## 中严重问题

### 18. 重复的数据库引擎实例

- **文件**: `pipelines.py`
- **行号**: 107, 136
- **描述**: DataPipeline和RecordLogPipeline各自独立调用`DatabaseEngineFactory.create()`，加上spider也创建一个，一个spider运行期间至少3个独立连接实例。
- **修复方向**: 通过`from_crawler`传递共享引擎实例。

### 19. DailySpider数据库函数不兼容

- **文件**: `spiders/tushare.py`
- **行号**: 151
- **描述**: SQL中硬编码`today()`，ClickHouse特有函数，MySQL/Doris使用`CURDATE()`。
- **修复方向**: 通过`db_engine.functions`提供跨数据库日期函数。

### 20. 中间件重试未限制次数/无退避

- **文件**: `middlewares.py`
- **行号**: 118, 125
- **描述**: `_retry`未显式传递`max_retry_times`，402限流场景固定delay无指数退避。
- **修复方向**: 对402错误使用独立的重试计数和指数退避策略。

### 21. 无并发安全保护

- **文件**: `pipelines.py`, `spiders/tushare.py`
- **描述**: DailySpider的缺失日期计算基于查询时刻状态，多实例竞争导致重复。RecordLogPipeline的`self.count`累加存在race condition。
- **修复方向**: 增加分布式锁或运行时检测；`self.count`改用线程安全计数器。

### 22. 异常时未清理资源

- **文件**: `pipelines.py`
- **行号**: 117-129
- **描述**: `process_item`中upsert/insert异常时无`try/finally`或事务回滚，SQLAlchemy可能留下未提交事务。
- **修复方向**: 包裹`try/except`，显式管理事务（`begin()`/`commit()`/`rollback()`）。

### 23. 修改schema对象副作用

- **文件**: `pipelines.py`
- **行号**: 70-71
- **描述**: `column["default"] = ...`直接修改schema dict中的列定义。
- **修复方向**: 使用局部变量，不修改原dict。

### 24. StockMin数据条数假设不稳健

- **文件**: `spiders/stock/quotes.py`
- **行号**: 274
- **描述**: 判断分钟线数据条数必须等于241，节假日、停牌日条数不同，导致数据丢弃。
- **修复方向**: 放宽条数检查逻辑，或按交易日类型分别判断。

### 25. FutHoldingSpider SQL缺少db_name前缀

- **文件**: `spiders/future/quotes.py`
- **行号**: 40
- **描述**: `FROM trade_cal`未加`{db_name}`前缀，非default数据库时失败。
- **修复方向**: 添加`{db_name}`前缀。

### 26. 文件句柄未关闭

- **文件**: `db_engine.py:13-27`, `pipelines.py:34`, `spiders/tushare.py:32`, `tests/create_tables.py:16,20`
- **描述**: `open(...).read()`无`with`语句。
- **修复方向**: 统一使用`with open(...) as f:`。

### 27. 重复读取配置文件

- **文件**: `pipelines.py`
- **行号**: 31-36
- **描述**: 每个pipeline实例独立读取config.yaml，未与Scrapy settings系统集成。
- **修复方向**: 从`crawler.settings`获取已解析配置，或缓存yaml读取结果。

### 28. Helm Chart CronJob命名冲突

- **文件**: `deploy/tushare-integration/templates/cronjob.yaml`
- **行号**: 8, 41
- **描述**: CronJob名称通过`splitList "/" $spider.name | last`生成，同名spider在不同目录下产生冲突；Kubernetes资源名称有63字符限制。
- **修复方向**: 名称中加入job name前缀或哈希值；做长度校验和截断。

### 29. Helm Chart subPath热更新问题

- **文件**: `deploy/tushare-integration/templates/cronjob.yaml`
- **行号**: 49-52, 105-111
- **描述**: `subPath`挂载不会随ConfigMap更新而自动更新，需要重启Pod才能生效。
- **修复方向**: 文档化说明需重启，或避免使用subPath。

### 30. commands.py异常处理缺失

- **文件**: `commands.py`
- **行号**: 20-22, 26-28, 32-39
- **描述**: 三个CLI命令都无try-except，失败时输出Python traceback。
- **修复方向**: 添加异常捕获，使用`typer.echo`输出友好错误信息，设置`raise typer.Exit(code=1)`。

### 31. manager.py信号处理竞态条件

- **文件**: `manager.py`
- **行号**: 26-27, 153-155
- **描述**: 全局`signal.signal`覆盖Python进程中其他组件的信号处理，在Twisted reactor环境下可能不生效或行为异常。
- **修复方向**: 使用Scrapy内置信号机制（`scrapy.signals.engine_stopped`）。

### 32. create_tables.py异常只打印不处理

- **文件**: `tests/create_tables.py`
- **行号**: 22-26
- **描述**: 建表异常只print，无退出码，CI场景无法检测失败。
- **修复方向**: `sys.exit(1)`或非零退出码。

### 33. meta合并可能覆盖关键字段

- **文件**: `spiders/tushare.py`
- **行号**: 93-97
- **描述**: `meta`参数通过`|`合并，调用方传入的`api_name`或`params`会覆盖内部值。
- **修复方向**: 调换合并顺序或显式禁止覆盖内部字段。

### 34. 未使用的错误import

- **文件**: `spiders/index/sw.py`
- **行号**: 2-3
- **描述**: `from urllib import request`、`from venv import logger`未使用且后者不存在。
- **修复方向**: 删除未使用的import。

### 35. 重试次数未显式限制

- **文件**: `middlewares.py`
- **行号**: 118, 125
- **描述**: `_retry`调用未传递`max_retry_times`，完全依赖Scrapy默认设置。
- **修复方向**: 显式传递`max_retry_times`。

---

## 低严重问题

### 36. 代码重复

- **文件**: 大量spider文件（如`moneyflow.py`、`margin.py`等）
- **描述**: 多数spider仅是简单继承基类+设置custom_settings，可通过配置化或工厂模式生成。
- **修复方向**: 配置化生成简单spider。

### 37. 日志级别错误

- **文件**: `manager.py`
- **行号**: 78
- **描述**: `logging.error(spiders)`应为info级别。
- **修复方向**: 改为`logging.info`。

### 38. 日志使用不统一

- **文件**: 多处
- **描述**: 混用`logging.*`和`spider.logger.*`。
- **修复方向**: 统一使用`spider.logger`。

### 39. jobs.yaml占位符

- **文件**: `jobs.yaml`
- **描述**: 所有`cron_expr: UnSupported`为占位符，未实际使用。
- **修复方向**: 同步`values.yaml`中的真实cron表达式，或移除该字段。

### 40. GitHub Actions版本不安全

- **文件**: `.github/workflows/github-actions-build-image.yml`
- **描述**: `actions/checkout@master`未锁定具体版本；使用旧版本第三方action。
- **修复方向**: 更新到`actions/checkout@v4`，固定到commit SHA。

### 41. 除零风险

- **文件**: `settings.py`
- **行号**: 148
- **描述**: `DOWNLOAD_DELAY = 60 / frequency`，frequency为0时除零。
- **修复方向**: 添加除零保护，frequency<=0时使用默认值。

### 42. RecordLogPipeline start_time冗余

- **文件**: `pipelines.py`
- **行号**: 141, 185
- **描述**: `__init__`和`open_spider`中重复设置`start_time`。
- **修复方向**: 移除`__init__`中的初始化。

### 43. FutWeeklyDetail顺序混乱

- **文件**: `spiders/future/quotes.py`
- **行号**: 87
- **描述**: 用`set`去重周编号导致顺序丢失。
- **修复方向**: 保留有序去重。

### 44. 缺少测试覆盖

- **文件**: `tests/`
- **描述**: 仅有`create_tables.py`部署辅助脚本，无单元/集成测试。
- **修复方向**: 添加核心模块的单元测试。

### 45. schema无验证机制

- **文件**: `schema/*.yaml`
- **描述**: YAML schema格式无校验，错误配置在运行时才暴露。
- **修复方向**: 添加Pydantic模型或JSON Schema验证。

### 46. Dockerfile ADD . .过于宽泛

- **文件**: `Dockerfile`
- **行号**: 13
- **描述**: `ADD . .`复制整个构建上下文，可能包含`.git`和敏感文件。
- **修复方向**: 使用精确的`COPY`指令，确保`.dockerignore`排除敏感文件。

### 47. ClickHouse upsert实为insert

- **文件**: `schema/template/clickhouse/upsert.jinja2`
- **描述**: 与insert模板内容相同，依赖ReplacingMergeTree后台合并去重，非即时upsert语义。
- **修复方向**: 文档化说明ReplacingMergeTree语义。

### 48. StarRocks模板与其他引擎不一致

- **文件**: `schema/template/starrocks/*.jinja2`
- **描述**: 使用`%s`占位符，其他引擎用`:column`。
- **修复方向**: 统一为命名参数`:column`。

### 49. config.yaml重复配置

- **文件**: `config.yaml`
- **描述**: `reporters`列表和`feishu_webhook`分散配置，维护困难。
- **修复方向**: 将webhook URL内联到reporters配置块中。

### 50. main.py缺少模块文档

- **文件**: `main.py`
- **描述**: 无`__version__`、错误处理、日志配置、`--version`选项。
- **修复方向**: 添加基础模块结构。

---

## 附录：按文件归类

| 文件 | 高严重 | 中严重 | 低严重 |
|------|--------|--------|--------|
| `db_engine.py` | #2, #3 | #18, #26 | - |
| `settings.py` | #8, #9 | #11 | #41 |
| `manager.py` | #17 | #31, #32 | #37 |
| `pipelines.py` | #10, #11, #13 | #18, #22, #23, #27, #42 | - |
| `middlewares.py` | #4, #5 | #20, #35 | - |
| `spiders/tushare.py` | #6, #7, #8 | #19, #33 | - |
| `spiders/stock/quotes.py` | #1, #24 | - | - |
| `spiders/stock/special.py` | #1 | - | - |
| `spiders/index/quotes.py` | #1, #7 | - | - |
| `spiders/index/sw.py` | - | #34 | - |
| `spiders/future/quotes.py` | - | #25, #43 | - |
| `reporters.py` | #12, #15, #16 | - | - |
| `commands.py` | - | #30 | - |
| `main.py` | - | - | #50 |
| `config.yaml` | #14 | - | #39, #49 |
| `jobs.yaml` | - | - | #39 |
| `Dockerfile` | - | - | #46 |
| `.github/workflows/` | - | - | #40 |
| `deploy/tushare-integration/` | - | #28, #29 | - |
| `tests/` | - | #32 | #44 |
| `schema/template/` | #2 | - | #47, #48 |
| 其他spider文件 | - | - | #36 |
