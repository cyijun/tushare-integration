# 安装

## Kubernetes部署(Helm)

    cd deploy/tushare-integration
    helm install tushare-integration ./ -f values.yaml

### 基于CronJob的定时任务

values.yaml中包含cronjob字段用于配置定时任务，可根据自己需求进行修改，参考[配置文档](settings.md)

## 使用Docker

    docker pull zhangbc/tushare-integration:latest

## 直接使用源码

### 从仓库获取源码

    git clone git@github.com:zhangbc97/tushare-integration.git
    cd tushare-integration

#### 使用 pip 安装依赖

    pip install -r requirements.txt

#### 使用 poetry 安装依赖

    pip install poetry
    poetry install

#### 使用 uv 安装依赖（推荐）

项目支持 [uv](https://docs.astral.sh/uv/) 作为包管理和运行工具，速度更快且无需单独创建虚拟环境。

**安装 uv：**

    curl -LsSf https://astral.sh/uv/install.sh | sh

**创建虚拟环境并安装依赖：**

    uv venv
    uv pip install -r requirements.txt

**或直接通过 pyproject.toml 安装：**

    uv pip install -e .

**使用 uv 运行：**

    uv run python main.py query list
    uv run python main.py run spider "stock/basic/.*"

> **提示**：使用 `uv run` 时会自动识别项目下的 `.venv` 虚拟环境，无需手动激活。

# 升级

直接替换镜像即可