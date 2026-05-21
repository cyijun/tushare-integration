#!/usr/bin/env bash
set -euo pipefail

udf_source_dir="${CLICKHOUSE_UDF_SOURCE_DIR:-/opt/clickhouse-udf}"
user_scripts_path="${CLICKHOUSE_USER_SCRIPTS_PATH:-/var/lib/clickhouse/user_scripts}"
udf_config_dir="${CLICKHOUSE_UDF_CONFIG_DIR:-/etc/clickhouse-server/user_defined_functions}"

install -d -m 0755 "${user_scripts_path}" "${udf_config_dir}"
install -m 0755 "${udf_source_dir}/user_scripts/dws_stock_factor_rows.py" \
    "${user_scripts_path}/dws_stock_factor_rows.py"
install -m 0644 "${udf_source_dir}/user_defined_functions/dws_stock_factor_rows.xml" \
    "${udf_config_dir}/dws_stock_factor_rows.xml"

if id clickhouse >/dev/null 2>&1; then
    chown clickhouse:clickhouse \
        "${user_scripts_path}" \
        "${user_scripts_path}/dws_stock_factor_rows.py" \
        "${udf_config_dir}" \
        "${udf_config_dir}/dws_stock_factor_rows.xml"
fi

if [ -x /entrypoint.sh ]; then
    exec /entrypoint.sh "$@"
fi

exec clickhouse-server "$@"
