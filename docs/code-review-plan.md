# Tushare Integration 代码修复计划

> 对应审查报告: [code-review-issues.md](./code-review-issues.md)
> 审查日期: 2026-05-25

---

## 修复原则

1. **安全优先**: SQL注入、SSRF、敏感信息泄露优先修复
2. **稳定优先**: 崩溃、连接泄漏、阻塞问题优先修复
3. **兼容优先**: 跨数据库兼容性修改需要兼容现有行为
4. **最小改动**: 每个修复独立commit，便于review和回滚
5. **验证每个修复**: 修复后运行对应spider或测试确认

---

## Phase 1: P0 紧急修复（预计 1-2 天）

**目标**: 修复影响系统稳定性和数据安全的高严重问题

### 1.1 修复数据库连接泄漏 (#2, #3)

**涉及文件**: `db_engine.py`, `pipelines.py`, `spiders/tushare.py`

**具体步骤**:
1. `DBEngine`基类添加`close()`抽象方法
2. `SQLAlchemyEngine`实现`close()`调用`self.conn.close()`
3. `ClickhouseEngine`实现`close()`（clickhouse-connect client通常无需显式关闭，但需确认）
4. `TushareIntegrationDataPipeline`添加`close_spider()`调用`self.db_engine.close()`
5. `RecordLogPipeline`添加`close_spider()`调用`self.db_engine.close()`
6. `TushareSpider`在爬虫结束时关闭`db_engine`

**验证方式**: 运行单个spider，通过数据库连接监控确认连接被释放

### 1.2 替换阻塞式sleep为异步延迟 (#4)

**涉及文件**: `middlewares.py`

**具体步骤**:
1. 移除`time.sleep(self.retry_delay)`
2. 导入`from twisted.internet import reactor`
3. 使用`deferLater(reactor, self.retry_delay, lambda: self._retry(request, reason, spider))`或设置`request.meta['download_delay'] = self.retry_delay`
4. 确保返回Deferred而不是Response

**验证方式**: 运行并发spider，确认事件循环未被阻塞

### 1.3 添加JSON解析异常保护 (#5, #6)

**涉及文件**: `middlewares.py`, `spiders/tushare.py`

**具体步骤**:
1. `middlewares.py:120`处添加try/except包裹`json.loads(response.text)`
2. 解析失败时按HTTP错误码路径重试
3. `spiders/tushare.py:59`处先检查`response.status`，再try/except解析JSON
4. 非JSON响应时记录日志并抛出可重试异常

**验证方式**: 模拟502/504响应，确认爬虫不crash且能重试

### 1.4 消除同步阻塞请求 (#7)

**涉及文件**: `spiders/tushare.py`, `spiders/stock/basic.py`, `spiders/index/quotes.py`, `spiders/index/sw.py`

**具体步骤**:
1. `request_with_requests()`方法添加`timeout=(10, 30)`参数
2. 优先方案: 将`StockNameChangeSpider`、`IndexWeightSpider`、`IndexMemberAllSpider`的分页逻辑改为使用Scrapy的meta+回调链
3. 备选方案: 使用`crawler.engine.download()`或在线程池中执行requests

**验证方式**: 运行上述三个spider，确认事件循环未被阻塞

### 1.5 修复int类型转换崩溃 (#10)

**涉及文件**: `pipelines.py`

**具体步骤**:
1. `TransformDTypePipeline`中`"int"`分支改为`astype('Int64')`（pandas nullable int）
2. 或在`astype(int)`前确保列中无NaN（FillNAPipeline后二次检查）

**验证方式**: 运行会返回含NaN int列的spider，确认不崩溃

### 1.6 修复日期静默数据损坏 (#11)

**涉及文件**: `pipelines.py`

**具体步骤**:
1. 移除将NaT硬编码替换为`1971-01-01`的逻辑
2. 改为: 对不可解析日期记录warning日志，保留NaT/None
3. 数据库INSERT时让数据库处理NULL（schema已定义DEFAULT）

**验证方式**: 检查数据库中日期列，确认无效日期为NULL而非`1971-01-01`

### 1.7 修复飞书报告器缩进bug (#12)

**涉及文件**: `reporters.py`

**具体步骤**:
1. 修正`if not self.webhook:`块内`return`的缩进
2. 确保webhook为空时直接return，不构造body

**验证方式**: 空webhook配置运行，确认不报错且无请求发出

### 1.8 修复记录日志schema误用 (#13)

**涉及文件**: `pipelines.py`

**具体步骤**:
1. `RecordLogPipeline`中保存日志表schema为实例变量`self.log_schema`
2. `close_spider`中使用`self.log_schema`而非`self.schema`

**验证方式**: 运行spider，确认日志表正确写入

### 1.9 修复飞书Webhook SSRF风险 (#15)

**涉及文件**: `reporters.py`

**具体步骤**:
1. `send_report`中添加URL校验: `urlparse(self.webhook).netloc.endswith('open.feishu.cn')`
2. `requests.post`添加`timeout=10`
3. 校验响应状态码，非200时记录error日志

**验证方式**: 配置错误域名，确认请求被拦截

### 1.10 修复配置验证错误 (#9)

**涉及文件**: `settings.py`

**具体步骤**:
1. `DatabaseConfig.port`字段的`env_variable('DB_HOST')`改为`env_variable('DB_PORT')`

**验证方式**: 设置`DB_PORT`环境变量，确认端口被正确覆盖

---

## Phase 2: P1 安全与正确性修复（预计 2-3 天）

**目标**: 修复SQL注入、配置安全、跨数据库兼容性问题

### 2.1 SQL注入防护 - f-string参数化 (#1)

**涉及文件**: `spiders/stock/quotes.py`, `spiders/stock/special.py`, `spiders/index/quotes.py`, `manager.py`

**具体步骤**:
1. `StockMin`中ts_code等参数改用SQL参数化查询
2. `CyqChipsSpider`中ts_code参数化
3. `IndexDailySpider`中查询index_basic使用参数化
4. `manager.py:133`中batch_id使用参数化查询
5. 对无法参数化的标识符（表名、列名）添加正则校验: `^[a-zA-Z_][a-zA-Z0-9_]*$`

**验证方式**: 运行相关spider，确认SQL执行正常且不受注入影响

### 2.2 SQL注入防护 - Jinja2模板转义 (#2)

**涉及文件**: `schema/template/*/table.jinja2`, `db_engine.py`

**具体步骤**:
1. `db_engine.py`加载Jinja2模板时启用`autoescape=True`
2. 对`db_name`、`table_name`、`column.name`等标识符添加正则校验函数
3. 非法字符抛出`ValueError`
4. 对`column.comment`等用户可能输入的内容确保转义

**验证方式**: 构造含特殊字符的schema，确认被拦截或正确转义

### 2.3 敏感信息配置安全 (#14)

**涉及文件**: `config.yaml`, `.gitignore`, `deploy/`

**具体步骤**:
1. `.gitignore`中添加`config.yaml`
2. 复制当前`config.yaml`为`config.example.yaml`，敏感字段留空
3. Helm chart中:
   - 创建`templates/secret.yaml`用于存储敏感配置
   - CronJob中通过`envFrom`或`env.valueFrom.secretKeyRef`注入敏感字段
   - ConfigMap仅保留非敏感配置
4. `values.yaml`中敏感字段添加注释警告

**验证方式**: `git status`确认config.yaml不被跟踪；Helm template渲染确认Secret正确生成

### 2.4 修复模块加载时硬读取配置 (#8)

**涉及文件**: `settings.py`

**具体步骤**:
1. 将模块底部的`for key, value in ...`循环移除
2. 改为惰性属性或显式初始化函数
3. 确保Scrapy settings兼容性不受影响

**验证方式**: 在config.yaml不存在的目录下`python -c "import tushare_integration.settings"`，确认不抛异常

### 2.5 ReporterLoader动态导入安全 (#16)

**涉及文件**: `reporters.py`

**具体步骤**:
1. 定义允许加载的类白名单`ALLOWED_REPORTERS`
2. `get_reporters()`中校验配置中的类名是否在白名单中
3. 不在白名单中时抛出`ValueError`

**验证方式**: 配置非法reporter类名，确认被拦截

### 2.6 修复manager.py SQL拼接 (#17)

**涉及文件**: `manager.py`

**具体步骤**:
1. `get_report_content`中使用参数化查询传递batch_id
2. 需要扩展DBEngine接口支持带参数的query_df

**验证方式**: 运行job，确认报告正确生成

### 2.7 跨数据库日期函数兼容 (#19)

**涉及文件**: `spiders/tushare.py`, `db_engine.py`

**具体步骤**:
1. `DBEngine.functions`中已定义`to_date`，扩展添加`today`函数映射
2. `ClickhouseEngine`: `today` -> `today()`
3. `MySQLEngine`/`ApacheDorisEngine`: `today` -> `CURDATE()`
4. `DailySpider`中使用`self.get_db_engine().functions.get('today', 'today()')`代替硬编码

**验证方式**: 在MySQL环境下运行DailySpider，确认SQL正常执行

### 2.8 修复FutHoldingSpider SQL (#25)

**涉及文件**: `spiders/future/quotes.py`

**具体步骤**:
1. `FROM trade_cal`改为`FROM {db_name}.trade_cal`

**验证方式**: 非default数据库运行，确认正常

---

## Phase 3: P2 稳定性与质量改进（预计 2-3 天）

**目标**: 修复异常处理、资源管理、代码质量等问题

### 3.1 异常处理与事务回滚 (#22, #30)

**涉及文件**: `pipelines.py`, `commands.py`

**具体步骤**:
1. `TushareIntegrationDataPipeline.process_item`添加try/except
2. SQLAlchemy连接显式管理事务
3. `commands.py`三个命令添加try/except，友好错误输出

### 3.2 重试策略优化 (#20, #35)

**涉及文件**: `middlewares.py`

**具体步骤**:
1. 402限流错误使用独立的重试计数器
2. 实现指数退避: `delay = min(base_delay * 2^attempt, max_delay)`
3. 显式传递`max_retry_times`参数

### 3.3 信号处理修复 (#31)

**涉及文件**: `manager.py`

**具体步骤**:
1. 移除全局`signal.signal`注册
2. 使用Scrapy内置的`engine_stopped`信号

### 3.4 文件句柄关闭 (#26)

**涉及文件**: `db_engine.py`, `pipelines.py`, `spiders/tushare.py`, `tests/create_tables.py`

**具体步骤**:
1. 统一所有`open()`调用改为`with open(...) as f:`

### 3.5 消除schema修改副作用 (#23)

**涉及文件**: `pipelines.py`

**具体步骤**:
1. `get_default_by_data_type`返回值赋给局部变量，不修改原dict

### 3.6 StockMin条数检查优化 (#24)

**涉及文件**: `spiders/stock/quotes.py`

**具体步骤**:
1. 改为检查条数是否在合理范围内（如200-250），而非严格等于241
2. 或按交易日类型（正常/节假日/停牌）分别判断

### 3.7 meta合并保护 (#33)

**涉及文件**: `spiders/tushare.py`

**具体步骤**:
1. 调换meta合并顺序: 先调用方meta，再内部meta，确保内部字段不被覆盖

### 3.8 清理未使用import (#34)

**涉及文件**: `spiders/index/sw.py`

**具体步骤**:
1. 删除`from urllib import request`和`from venv import logger`

---

## Phase 4: 工程化改进（预计 2-3 天）

**目标**: 代码重构、测试、部署优化

### 4.1 代码重复消除 (#36)

**涉及文件**: `spiders/stock/moneyflow.py`, `spiders/stock/margin.py`等

**具体步骤**:
1. 分析简单spider的共同模式
2. 考虑配置化生成: 在schema YAML或单独配置中定义spider参数
3. 或创建工厂函数动态生成spider类

### 4.2 添加测试覆盖 (#44)

**涉及文件**: `tests/`

**具体步骤**:
1. 添加`tests/test_settings.py` - 验证配置加载和覆盖
2. 添加`tests/test_pipelines.py` - 验证pipeline数据处理逻辑
3. 添加`tests/test_db_engine.py` - 验证SQL生成和引擎创建
4. 添加`tests/test_spiders.py` - 验证spider基类方法

### 4.3 Schema验证机制 (#45)

**涉及文件**: `schema/`, `settings.py`或新模块

**具体步骤**:
1. 定义Pydantic模型`SchemaDefinition`
2. 加载schema时进行校验
3. 提前发现配置错误（如缺失data_type、非法列名等）

### 4.4 Dockerfile优化 (#46)

**涉及文件**: `Dockerfile`

**具体步骤**:
1. `ADD . .`改为精确的`COPY`指令
2. 创建`.dockerignore`排除`.git/`、`config.yaml`、缓存等

### 4.5 GitHub Actions版本锁定 (#40)

**涉及文件**: `.github/workflows/`

**具体步骤**:
1. `actions/checkout@master` -> `actions/checkout@v4`
2. 评估使用官方`docker/build-push-action`替代第三方kaniko action
3. 或固定第三方action到commit SHA

### 4.6 Helm Chart改进 (#28, #29)

**涉及文件**: `deploy/tushare-integration/`

**具体步骤**:
1. CronJob名称加入job name前缀避免冲突
2. 添加名称长度截断逻辑（限制63字符）
3. 文档化subPath热更新限制

### 4.7 统一日志使用 (#37, #38)

**涉及文件**: 多处

**具体步骤**:
1. `manager.py:78`改为`logging.info`
2. 统一spider内使用`spider.logger`，非spider代码使用`logging`

### 4.8 其他低严重修复

- #39 `jobs.yaml` cron表达式同步
- #41 settings.py除零保护
- #42 RecordLogPipeline start_time冗余
- #43 FutWeeklyDetail有序去重
- #47 ClickHouse upsert文档化
- #48 StarRocks模板统一占位符
- #49 config.yaml配置合并
- #50 main.py基础结构

---

## 执行时间表

| Phase | 内容 | 预计时间 | 涉及文件数 |
|-------|------|----------|-----------|
| Phase 1 | P0紧急修复 | 1-2天 | ~8 |
| Phase 2 | P1安全与正确性 | 2-3天 | ~12 |
| Phase 3 | P2稳定性改进 | 2-3天 | ~6 |
| Phase 4 | 工程化改进 | 2-3天 | ~15 |
| **总计** | | **7-11天** | **~40** |

---

## 分支策略

```
main
  └── fix/code-review-2026-05-25  (当前分支)
        ├── phase-1-p0-fixes      (Phase 1修复)
        ├── phase-2-p1-fixes      (Phase 2修复)
        ├── phase-3-p2-fixes      (Phase 3修复)
        └── phase-4-engineering   (Phase 4改进)
```

建议每个Phase独立为一个PR，便于review和回滚。

---

## 验证清单

每个Phase完成后执行:

- [ ] `black .` 和 `isort .` 通过
- [ ] `pytest` 通过（新增测试全部通过）
- [ ] 运行一个代表性spider（如`stock/basic/stock_basic`）确认正常
- [ ] 运行一个DailySpider（如`stock/quotes/daily`）确认增量采集正常
- [ ] Helm chart渲染通过: `helm template deploy/tushare-integration/`
- [ ] 数据库连接监控确认无泄漏

---

## 风险与注意事项

1. **Phase 1.2（替换time.sleep）**: Twisted deferLater与Scrapy RetryMiddleware的集成需要仔细测试，确保重试计数和delay逻辑正确
2. **Phase 1.4（消除requests）**: 将同步分页改为异步回调链可能改变spider的执行顺序和内存使用模式，需监控
3. **Phase 2.3（敏感信息配置）**: Helm chart的Secret变更需要配合运维更新部署流程，有外部依赖
4. **Phase 2.4（模块级副作用）**: settings.py的改动影响整个项目的配置加载方式，需全面测试
5. **Phase 2.7（跨数据库兼容）**: today()函数的替换需在所有数据库环境（ClickHouse/MySQL/Doris）验证
