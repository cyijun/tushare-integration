#!/bin/bash
# Tushare Integration 首次全量同步脚本
# 用法: nohup ./first_run.sh &
# 然后可以关闭终端，它会继续在后台跑

set -o pipefail

cd "$(dirname "$0")"
PROJECT_DIR="$(pwd)"
LOG_DIR="$PROJECT_DIR/logs"
mkdir -p "$LOG_DIR"

LOG_FILE="$LOG_DIR/first_run_$(date +%Y%m%d_%H%M%S).log"
STATE_FILE="$LOG_DIR/first_run_state.txt"

# 如果存在状态文件，读取已完成的 job
COMPLETED_JOBS=""
if [[ -f "$STATE_FILE" ]]; then
    COMPLETED_JOBS=$(cat "$STATE_FILE")
    echo "检测到上次运行状态，已完成的 job 将跳过:" | tee -a "$LOG_FILE"
    echo "$COMPLETED_JOBS" | tee -a "$LOG_FILE"
    echo "" | tee -a "$LOG_FILE"
fi

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

log "===== Tushare Integration 首次全量同步 ====="
log "项目目录: $PROJECT_DIR"
log "日志文件: $LOG_FILE"
log "开始时间: $(date)"
log ""

# 按依赖顺序定义所有 job
# 注意: stock/basic 会重新跑（包含之前中断的 stk_managers 等）
# DailySpider/TSCodeSpider 有去重逻辑，重复数据不会写入
jobs=(
    "stock/basic"
    "stock/quotes"
    "stock/moneyflow"
    "stock/financial"
    "stock/market"
    "stock/margin"
    "stock/special"
    "stock/limit"
    "index/basic"
    "index/quotes"
    "index/sw"
    "index/ths"
    "index/zx"
    "future/basic"
    "future/quotes"
)

TOTAL=${#jobs[@]}
CURRENT=0

for job in "${jobs[@]}"; do
    CURRENT=$((CURRENT + 1))

    # 检查是否已跳过
    if echo "$COMPLETED_JOBS" | grep -qx "$job"; then
        log "[$CURRENT/$TOTAL] 跳过已完成: $job"
        continue
    fi

    log ""
    log "===== [$CURRENT/$TOTAL] 启动 job: $job ====="

    START_TIME=$(date +%s)
    uv run python main.py run job "$job" 2>&1 | tee -a "$LOG_FILE"
    EXIT_CODE=${PIPESTATUS[0]}
    END_TIME=$(date +%s)
    ELAPSED=$((END_TIME - START_TIME))

    if [[ $EXIT_CODE -eq 0 ]]; then
        log "===== [$CURRENT/$TOTAL] 完成 job: $job (耗时 ${ELAPSED}s) ====="
        echo "$job" >> "$STATE_FILE"
    else
        log "===== [$CURRENT/$TOTAL] job: $job 失败 (exit=$EXIT_CODE, 耗时 ${ELAPSED}s) ====="
        log "由于错误，暂停执行。修复后可重新运行本脚本，已完成的 job 会自动跳过。"
        exit 1
    fi
done

log ""
log "===== 全部 job 执行完毕 ====="
log "结束时间: $(date)"
log "日志文件: $LOG_FILE"
log "可以删除状态文件 $STATE_FILE 来重新全量跑"

# 发送完成通知（如果配置了飞书 webhook）
if command -v curl &> /dev/null; then
    curl -s -X POST \
        -H "Content-Type: application/json" \
        -d '{"msg_type":"text","content":{"text":"Tushare 首次全量同步完成"}}' \
        "https://open.feishu.cn/open-apis/bot/v2/hook/2a5a19b0-e5e0-4a9e-908b-1ce8928187f0" \
        > /dev/null 2>&1 || true
fi
