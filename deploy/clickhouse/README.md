# ClickHouse factor UDF deployment

`dws_stock_factor_wide_matrix` depends on the executable UDF `dws_stock_factor_rows`.

Deploy these files on every ClickHouse server:

- Copy `deploy/clickhouse/user_scripts/dws_stock_factor_rows.py` to ClickHouse `user_scripts_path`.
- Copy `deploy/clickhouse/user_defined_functions/dws_stock_factor_rows.xml` to the path included by `user_defined_executable_functions_config`.
- Make sure the ClickHouse Python environment can import `tushare_integration` and has the project dependencies installed.
- Put `factor_mapping_readable.csv` in the working directory or next to the factor package, or set `TUSHARE_FACTOR_MAPPING_CSV` for the UDF process.

After deployment, reload ClickHouse executable functions before running:

```sql
SYSTEM RELOAD FUNCTIONS;
```

The UDF is configured with longer executable timeouts because Python startup, pandas import, and large factor blocks can exceed ClickHouse's default 10 second executable UDF timeout:

- `max_command_execution_time=1200`
- `command_read_timeout=1200000`
- `command_write_timeout=1200000`

## Docker image with Python UDF support

Build and start a ClickHouse container that keeps using the existing table data under `/data/public/clickhouse`:

```bash
deploy/clickhouse/start_clickhouse_udf.sh
```

The start script builds `tushare-clickhouse-udf:25.8.22.28` from `clickhouse:25.8.22.28` and runs it with:

```bash
-v /data/public/clickhouse:/var/lib/clickhouse
--net=host
--ulimit nofile=262144:262144
```

The mounted `/data/public/clickhouse` directory remains the ClickHouse data directory, so existing tables are loaded by the new container. On container startup, the wrapper only installs these UDF files:

- `/var/lib/clickhouse/user_scripts/dws_stock_factor_rows.py`
- `/etc/clickhouse-server/user_defined_functions/dws_stock_factor_rows.xml`

It does not recreate or recursively change ownership of the table data directory.

After changing the UDF XML in a running container, copy it into place and reload functions:

```bash
sudo docker cp deploy/clickhouse/user_defined_functions/dws_stock_factor_rows.xml \
  clickhouse-server:/etc/clickhouse-server/user_defined_functions/dws_stock_factor_rows.xml
sudo docker exec clickhouse-server clickhouse-client --query "SYSTEM RELOAD FUNCTIONS"
```

Check the loaded timeout values:

```bash
sudo docker exec clickhouse-server clickhouse-client --query "
SELECT
    name,
    load_status,
    max_command_execution_time,
    command_read_timeout,
    command_write_timeout,
    loading_error_message
FROM system.user_defined_functions
WHERE name = 'dws_stock_factor_rows'
FORMAT Vertical"
```

If the old `clickhouse-server` container already exists, replace it explicitly:

```bash
RECREATE=1 deploy/clickhouse/start_clickhouse_udf.sh
```

You can override defaults with environment variables:

```bash
IMAGE_NAME=tushare-clickhouse-udf:25.8.22.28 \
BASE_IMAGE=clickhouse:25.8.22.28 \
CONTAINER_NAME=clickhouse-server \
CLICKHOUSE_DATA_DIR=/data/public/clickhouse \
deploy/clickhouse/start_clickhouse_udf.sh
```

For China networks, the Dockerfile defaults to Tsinghua mirrors:

- `APT_MIRROR=auto`, which maps Ubuntu/Debian apt sources to Tsinghua mirrors.
- `PIP_SOURCE=https://pypi.tuna.tsinghua.edu.cn/simple`.

The image also defaults to `PYTHON_DEPS_MODE=udf`, which installs only the Python packages needed by the executable UDF:

```bash
numpy==2.1.3 pandas==2.2.3
```

Use the full project dependency set only when the ClickHouse container must run non-UDF application code:

```bash
PYTHON_DEPS_MODE=full RECREATE=1 deploy/clickhouse/start_clickhouse_udf.sh
```

You can switch to another mirror if needed:

```bash
PIP_SOURCE=https://mirrors.aliyun.com/pypi/simple/ \
APT_MIRROR=https://mirrors.aliyun.com/ubuntu \
RECREATE=1 deploy/clickhouse/start_clickhouse_udf.sh
```
