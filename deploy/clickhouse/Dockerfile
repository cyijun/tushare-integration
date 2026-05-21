ARG CLICKHOUSE_IMAGE=clickhouse:25.8.22.28
FROM ${CLICKHOUSE_IMAGE}

ARG APT_MIRROR=auto
ARG PIP_SOURCE=https://pypi.tuna.tsinghua.edu.cn/simple
ARG PYTHON_DEPS_MODE=udf
ARG UDF_PYTHON_PACKAGES="numpy==2.1.3 pandas==2.2.3"

USER root

ENV VIRTUAL_ENV=/opt/tushare-integration/.venv
ENV PATH="/opt/tushare-integration/.venv/bin:${PATH}"
ENV PYTHONPATH=/opt/tushare-integration
ENV TUSHARE_FACTOR_MAPPING_CSV=/opt/tushare-integration/docs/prd/factor_mapping_readable.csv

RUN set -eux; \
    if [ -n "${APT_MIRROR}" ]; then \
        . /etc/os-release; \
        mirror="${APT_MIRROR}"; \
        if [ "${mirror}" = "auto" ]; then \
            case "${ID}" in \
                ubuntu) mirror="https://mirrors.tuna.tsinghua.edu.cn/ubuntu" ;; \
                debian) mirror="https://mirrors.tuna.tsinghua.edu.cn/debian" ;; \
                *) mirror="" ;; \
            esac; \
        fi; \
        if [ -n "${mirror}" ]; then \
            if [ "${ID}" = "ubuntu" ]; then \
                sed -i -E "s#https?://(archive|security).ubuntu.com/ubuntu/?#${mirror}#g" \
                    /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources 2>/dev/null || true; \
            elif [ "${ID}" = "debian" ]; then \
                sed -i -E "s#https?://deb.debian.org/debian/?#${mirror}#g; s#https?://security.debian.org/debian-security/?#${mirror}-security#g" \
                    /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources 2>/dev/null || true; \
            fi; \
        fi; \
    fi; \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        python3 \
        python3-pip \
        python3-venv

WORKDIR /opt/tushare-integration

COPY pyproject.toml requirements.txt README.md VERSION ./
COPY tushare_integration ./tushare_integration
COPY docs/prd ./docs/prd

RUN python3 -m venv "${VIRTUAL_ENV}" \
    && pip install --upgrade pip setuptools wheel --index-url "${PIP_SOURCE}" \
    && if [ "${PYTHON_DEPS_MODE}" = "full" ]; then \
        pip install -r requirements.txt --index-url "${PIP_SOURCE}"; \
    else \
        pip install ${UDF_PYTHON_PACKAGES} --index-url "${PIP_SOURCE}"; \
    fi \
    && pip install --no-deps -e . \
    && python -c "from pathlib import Path; from tushare_integration.factors.clickhouse_udf import main; assert Path('/opt/tushare-integration/docs/prd/factor_mapping_readable.csv').exists()"

COPY deploy/clickhouse/user_scripts/dws_stock_factor_rows.py /opt/clickhouse-udf/user_scripts/dws_stock_factor_rows.py
COPY deploy/clickhouse/user_defined_functions/dws_stock_factor_rows.xml /opt/clickhouse-udf/user_defined_functions/dws_stock_factor_rows.xml
COPY deploy/clickhouse/config.d/executable-udf.xml /etc/clickhouse-server/config.d/executable-udf.xml
COPY deploy/clickhouse/docker-entrypoint-udf.sh /usr/local/bin/docker-entrypoint-udf.sh

RUN mkdir -p /etc/clickhouse-server/user_defined_functions /var/lib/clickhouse/user_scripts \
    && cp /opt/clickhouse-udf/user_scripts/dws_stock_factor_rows.py /var/lib/clickhouse/user_scripts/dws_stock_factor_rows.py \
    && cp /opt/clickhouse-udf/user_defined_functions/dws_stock_factor_rows.xml /etc/clickhouse-server/user_defined_functions/dws_stock_factor_rows.xml \
    && chmod 755 /opt/clickhouse-udf/user_scripts/dws_stock_factor_rows.py \
        /var/lib/clickhouse/user_scripts/dws_stock_factor_rows.py \
        /usr/local/bin/docker-entrypoint-udf.sh \
    && chmod 644 /opt/clickhouse-udf/user_defined_functions/dws_stock_factor_rows.xml \
        /etc/clickhouse-server/user_defined_functions/dws_stock_factor_rows.xml

ENTRYPOINT ["/usr/local/bin/docker-entrypoint-udf.sh"]
