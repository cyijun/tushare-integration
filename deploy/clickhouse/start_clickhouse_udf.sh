#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"

image_name="${IMAGE_NAME:-tushare-clickhouse-udf:25.8.22.28}"
base_image="${BASE_IMAGE:-clickhouse:25.8.22.28}"
container_name="${CONTAINER_NAME:-clickhouse-server}"
clickhouse_data_dir="${CLICKHOUSE_DATA_DIR:-/data/public/clickhouse}"
apt_mirror="${APT_MIRROR:-auto}"
pip_source="${PIP_SOURCE:-https://pypi.tuna.tsinghua.edu.cn/simple}"
python_deps_mode="${PYTHON_DEPS_MODE:-udf}"
udf_python_packages="${UDF_PYTHON_PACKAGES:-numpy==2.1.3 pandas==2.2.3}"
recreate="${RECREATE:-0}"

if [ -n "${DOCKER_CMD:-}" ]; then
    read -r -a docker_cmd <<< "${DOCKER_CMD}"
else
    docker_cmd=(docker)
fi

if ! "${docker_cmd[@]}" info >/dev/null 2>&1 && command -v sudo >/dev/null 2>&1; then
    docker_cmd=(sudo docker)
fi

"${docker_cmd[@]}" build \
    --build-arg "CLICKHOUSE_IMAGE=${base_image}" \
    --build-arg "APT_MIRROR=${apt_mirror}" \
    --build-arg "PIP_SOURCE=${pip_source}" \
    --build-arg "PYTHON_DEPS_MODE=${python_deps_mode}" \
    --build-arg "UDF_PYTHON_PACKAGES=${udf_python_packages}" \
    -f "${script_dir}/Dockerfile" \
    -t "${image_name}" \
    "${repo_root}"

mkdir -p "${clickhouse_data_dir}"

if "${docker_cmd[@]}" ps -a --format '{{.Names}}' | grep -Fxq "${container_name}"; then
    if [ "${recreate}" = "1" ]; then
        "${docker_cmd[@]}" rm -f "${container_name}"
    else
        echo "Container ${container_name} already exists. Set RECREATE=1 to replace it."
        exit 1
    fi
fi

"${docker_cmd[@]}" run -d \
    --name "${container_name}" \
    -v "${clickhouse_data_dir}:/var/lib/clickhouse" \
    --net=host \
    --ulimit nofile=262144:262144 \
    "${image_name}"
