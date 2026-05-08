#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="${PROJECT_DIR:-/data/flc/code/quant/tushare-integration}"
JOBS_FILE="${JOBS_FILE:-$PROJECT_DIR/jobs.yaml}"
CONFIG_FILE="${CONFIG_FILE:-$PROJECT_DIR/config.yaml}"
LOG_DIR="${LOG_DIR:-$PROJECT_DIR/logs}"
LOCK_FILE="${LOCK_FILE:-/tmp/tushare-daily-stock-jobs.lock}"

IMAGE_BASIC="${IMAGE_BASIC:-tushare-integration:0.0.1}"
IMAGE_DEFAULT="${IMAGE_DEFAULT:-tushare-integration:0.0.4}"
DWD_SYNC_IMAGE="${DWD_SYNC_IMAGE:-$IMAGE_DEFAULT}"
DOCKER_BIN="${DOCKER_BIN:-docker}"
USE_SUDO="${USE_SUDO:-auto}"
CONTINUE_ON_ERROR="${CONTINUE_ON_ERROR:-0}"
NORMAL_JOBS_HAD_FAILURE=0
DWD_SYNC_HAD_FAILURE=0

mkdir -p "$LOG_DIR"
RUN_LOG="${RUN_LOG:-$LOG_DIR/daily-stock-jobs-$(date +%Y%m%d).log}"
exec > >(tee -a "$RUN_LOG") 2>&1

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[$(date '+%F %T')] Another daily stock job run is already active. Exiting."
  exit 1
fi

DOCKER_PREFIX=()
if [[ "$USE_SUDO" == "1" ]]; then
  DOCKER_PREFIX=(sudo)
elif [[ "$USE_SUDO" == "auto" && "$EUID" -ne 0 ]] && command -v sudo >/dev/null 2>&1; then
  DOCKER_PREFIX=(sudo)
fi

docker_cmd() {
  "${DOCKER_PREFIX[@]}" "$DOCKER_BIN" "$@"
}

ACTIVE_CONTAINER=""
ACTIVE_LOGS_PID=""

cleanup_active_container() {
  if [[ -n "${ACTIVE_LOGS_PID:-}" ]]; then
    kill "$ACTIVE_LOGS_PID" >/dev/null 2>&1 || true
    wait "$ACTIVE_LOGS_PID" >/dev/null 2>&1 || true
    ACTIVE_LOGS_PID=""
  fi

  if [[ -n "${ACTIVE_CONTAINER:-}" ]]; then
    echo "[$(date '+%F %T')] Stopping active container: $ACTIVE_CONTAINER"
    docker_cmd rm -f "$ACTIVE_CONTAINER" >/dev/null 2>&1 || true
    ACTIVE_CONTAINER=""
  fi
}

on_exit() {
  local exit_code="$?"
  if [[ "$exit_code" != "0" ]]; then
    cleanup_active_container
  fi
}

on_interrupt() {
  echo "[$(date '+%F %T')] Interrupted. Stopping current job."
  cleanup_active_container
  exit 130
}

on_terminate() {
  echo "[$(date '+%F %T')] Terminated. Stopping current job."
  cleanup_active_container
  exit 143
}

trap on_exit EXIT
trap on_interrupt INT
trap on_terminate TERM

require_file() {
  local path="$1"
  if [[ ! -f "$path" ]]; then
    echo "[$(date '+%F %T')] Missing required file: $path"
    exit 1
  fi
}

run_job() {
  local container="$1"
  local image="$2"
  local job="$3"
  local container_id
  local exit_code
  local logs_pid

  echo "[$(date '+%F %T')] Starting $job with $image as $container"
  docker_cmd rm -f "$container" >/dev/null 2>&1 || true

  container_id="$(
    docker_cmd run -d \
      --name "$container" \
      --net=host \
      -v "$JOBS_FILE:/code/app/jobs.yaml:ro" \
      -v "$CONFIG_FILE:/code/app/config.yaml:ro" \
      "$image" \
      python main.py run job "$job"
  )"
  echo "[$(date '+%F %T')] Container started: $container_id"
  ACTIVE_CONTAINER="$container"

  docker_cmd logs -f "$container" &
  logs_pid="$!"
  ACTIVE_LOGS_PID="$logs_pid"

  exit_code="$(docker_cmd wait "$container")"
  wait "$logs_pid" || true
  ACTIVE_LOGS_PID=""
  ACTIVE_CONTAINER=""

  if [[ "$exit_code" != "0" ]]; then
    echo "[$(date '+%F %T')] Job failed: $job exited with code $exit_code"
    NORMAL_JOBS_HAD_FAILURE=1
    if [[ "$CONTINUE_ON_ERROR" == "1" ]]; then
      return 0
    fi
    return "$exit_code"
  fi

  echo "[$(date '+%F %T')] Job completed: $job"
}

run_dwd_sync() {
  local container="$1"
  local image="$2"
  local table="$3"
  local container_id
  local exit_code
  local logs_pid

  echo "[$(date '+%F %T')] Starting DWD sync $table with $image as $container"
  docker_cmd rm -f "$container" >/dev/null 2>&1 || true

  container_id="$(
    docker_cmd run -d \
      --name "$container" \
      --net=host \
      -v "$CONFIG_FILE:/code/app/config.yaml:ro" \
      "$image" \
      python main.py dwd sync "$table"
  )"
  echo "[$(date '+%F %T')] Container started: $container_id"
  ACTIVE_CONTAINER="$container"

  docker_cmd logs -f "$container" &
  logs_pid="$!"
  ACTIVE_LOGS_PID="$logs_pid"

  exit_code="$(docker_cmd wait "$container")"
  wait "$logs_pid" || true
  ACTIVE_LOGS_PID=""
  ACTIVE_CONTAINER=""

  if [[ "$exit_code" != "0" ]]; then
    echo "[$(date '+%F %T')] DWD sync failed: $table exited with code $exit_code"
    DWD_SYNC_HAD_FAILURE=1
    if [[ "$CONTINUE_ON_ERROR" == "1" ]]; then
      return 0
    fi
    return "$exit_code"
  fi

  echo "[$(date '+%F %T')] DWD sync completed: $table"
}

main() {
  require_file "$JOBS_FILE"
  require_file "$CONFIG_FILE"

  local jobs=(
    "tushare-job-basic|$IMAGE_BASIC|stock/basic"
    "tushare-job-financial|$IMAGE_DEFAULT|stock/financial"
    "tushare-job-margin|$IMAGE_DEFAULT|stock/margin"
    "tushare-job-market|$IMAGE_DEFAULT|stock/market"
    "tushare-job-quotes|$IMAGE_DEFAULT|stock/quotes"
    "tushare-job-special|$IMAGE_DEFAULT|stock/special"
  )

  # Ordered by DWD dependencies; factor bars read the first two tables.
  local dwd_sync_tasks=(
    "tushare-dwd-sync-stock-eod-price|$DWD_SYNC_IMAGE|dwd_stock_eod_price"
    "tushare-dwd-sync-stock-daily-basic|$DWD_SYNC_IMAGE|dwd_stock_daily_basic"
    "tushare-dwd-sync-stock-factor-bar|$DWD_SYNC_IMAGE|dwd_stock_factor_bar"
  )

  echo "[$(date '+%F %T')] Daily stock jobs started. Log: $RUN_LOG"

  local entry
  local container
  local image
  local job
  for entry in "${jobs[@]}"; do
    IFS="|" read -r container image job <<< "$entry"
    run_job "$container" "$image" "$job"
  done

  if [[ "$NORMAL_JOBS_HAD_FAILURE" != "0" ]]; then
    echo "[$(date '+%F %T')] Skipping DWD sync tasks because one or more normal jobs failed."
    echo "[$(date '+%F %T')] Daily stock jobs completed with failures."
    return 0
  fi

  echo "[$(date '+%F %T')] DWD sync tasks started."

  local table
  for entry in "${dwd_sync_tasks[@]}"; do
    IFS="|" read -r container image table <<< "$entry"
    run_dwd_sync "$container" "$image" "$table"
  done

  if [[ "$DWD_SYNC_HAD_FAILURE" != "0" ]]; then
    echo "[$(date '+%F %T')] Daily stock jobs completed; DWD sync tasks completed with failures."
    return 0
  fi

  echo "[$(date '+%F %T')] Daily stock jobs and DWD sync tasks completed."
}

main "$@"
