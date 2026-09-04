#!/usr/bin/env bash
# 服务器一键校验、替换旧容器/镜像、导入并启动离线 E5 镜像。
set -euo pipefail

CONTAINER="${CONTAINER:-csbot}"
IMAGE_REF="${IMAGE_REF:-cs-bot-aion:al4-amd64-e5}"
HOST_PORT="${HOST_PORT:-8000}"
DATA_VOLUME="${DATA_VOLUME:-csbot_data}"
ENV_FILE="${ENV_FILE:-}"
HEALTH_RETRIES="${HEALTH_RETRIES:-60}"

usage() {
  cat <<'EOF'
用法:
  export DEEPSEEK_API_KEY='sk-xxx'
  ./load-and-run.sh <镜像.tar.gz> [选项]

选项:
      --env-file FILE      从文件传入环境变量（可代替 export API key）
      --image REF          包内镜像名:标签（默认: cs-bot-aion:al4-amd64-e5）
      --container NAME     容器名（默认: csbot）
  -p, --port PORT          宿主机端口（默认: 8000）
      --data-volume NAME   保留使用的数据卷（默认: csbot_data）
  -h, --help               显示帮助

要求镜像旁存在同名 .sha256 文件。脚本会停删指定容器并删除指定旧镜像，
但不会删除数据卷；也不会挂载模型缓存卷，以免遮住镜像中的 E5 模型。
EOF
}

need_value() {
  [ "$#" -ge 2 ] && [ -n "$2" ] || { echo "错误: $1 缺少参数" >&2; exit 2; }
}

[ "$#" -gt 0 ] || { usage >&2; exit 2; }
case "$1" in -h|--help) usage; exit 0 ;; esac
ARCHIVE="$1"
shift
while [ "$#" -gt 0 ]; do
  case "$1" in
    --env-file) need_value "$@"; ENV_FILE="$2"; shift 2 ;;
    --image) need_value "$@"; IMAGE_REF="$2"; shift 2 ;;
    --container) need_value "$@"; CONTAINER="$2"; shift 2 ;;
    -p|--port) need_value "$@"; HOST_PORT="$2"; shift 2 ;;
    --data-volume) need_value "$@"; DATA_VOLUME="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "错误: 未知参数 $1" >&2; usage >&2; exit 2 ;;
  esac
done

REF_IMAGE="${IMAGE_REF%:*}"
REF_TAG="${IMAGE_REF##*:}"
if [ "$REF_IMAGE" = "$IMAGE_REF" ] \
  || ! [[ "$REF_IMAGE" =~ ^[a-z0-9]+([._-][a-z0-9]+)*(/[a-z0-9]+([._-][a-z0-9]+)*)*$ ]] \
  || ! [[ "$REF_TAG" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]; then
  echo "错误: 镜像引用不合法: $IMAGE_REF" >&2
  exit 2
fi
if ! [[ "$CONTAINER" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]; then
  echo "错误: 容器名不合法: $CONTAINER" >&2
  exit 2
fi
if ! [[ "$DATA_VOLUME" =~ ^[A-Za-z0-9][A-Za-z0-9_.-]*$ ]]; then
  echo "错误: 数据卷名不合法: $DATA_VOLUME" >&2
  exit 2
fi
case "$HOST_PORT" in ''|*[!0-9]*) echo "错误: 端口必须是数字" >&2; exit 2 ;; esac
[ "$HOST_PORT" -ge 1 ] && [ "$HOST_PORT" -le 65535 ] || { echo "错误: 端口范围应为 1-65535" >&2; exit 2; }
case "$HEALTH_RETRIES" in ''|*[!0-9]*) echo "错误: HEALTH_RETRIES 必须是正整数" >&2; exit 2 ;; esac
[ "$HEALTH_RETRIES" -ge 1 ] || { echo "错误: HEALTH_RETRIES 必须大于 0" >&2; exit 2; }

command -v docker >/dev/null 2>&1 || { echo "错误: 未安装 Docker" >&2; exit 1; }
command -v sha256sum >/dev/null 2>&1 || { echo "错误: 未安装 sha256sum" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "错误: Docker 未运行或当前用户无权限" >&2; exit 1; }
[ -f "$ARCHIVE" ] || { echo "错误: 镜像归档不存在: $ARCHIVE" >&2; exit 1; }
CHECKSUM="${ARCHIVE}.sha256"
[ -f "$CHECKSUM" ] || { echo "错误: 校验文件不存在: $CHECKSUM" >&2; exit 1; }
RUN_ENV=(-e CSAPP_LLM_MODE=deepseek -e TZ=Asia/Shanghai)
if [ -n "$ENV_FILE" ]; then
  [ -f "$ENV_FILE" ] || { echo "错误: env 文件不存在: $ENV_FILE" >&2; exit 1; }
  RUN_ENV+=(--env-file "$ENV_FILE")
else
  : "${DEEPSEEK_API_KEY:?请先 export DEEPSEEK_API_KEY=sk-xxx，或使用 --env-file FILE}"
  RUN_ENV+=(-e "DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY")
fi

echo "==> [1/6] 校验镜像归档"
read -r EXPECTED_SUM RECORDED_FILE <"$CHECKSUM" || true
RECORDED_FILE="${RECORDED_FILE#\*}"
case "$EXPECTED_SUM" in
  *[!0-9a-fA-F]*|'') echo "错误: SHA-256 校验文件格式不正确" >&2; exit 1 ;;
esac
[ "${#EXPECTED_SUM}" -eq 64 ] || { echo "错误: SHA-256 校验值长度不正确" >&2; exit 1; }
[ "$RECORDED_FILE" = "$(basename "$ARCHIVE")" ] || {
  echo "错误: 校验文件记录的目标不是 $(basename "$ARCHIVE")" >&2
  exit 1
}
ACTUAL_SUM="$(sha256sum -- "$ARCHIVE" | awk '{ print $1 }')"
[ "${ACTUAL_SUM,,}" = "${EXPECTED_SUM,,}" ] || {
  echo "错误: 镜像归档 SHA-256 不匹配，禁止部署" >&2
  exit 1
}
echo "$(basename "$ARCHIVE"): OK"

# load 会把标签指向新镜像，但旧 image ID 仍保留，可用于失败回滚。
OLD_IMAGE_ID="$(docker image inspect "$IMAGE_REF" --format '{{.Id}}' 2>/dev/null || true)"
restore_old_tag() {
  if [ -n "$OLD_IMAGE_ID" ]; then
    docker image tag "$OLD_IMAGE_ID" "$IMAGE_REF"
  fi
}

echo "==> [2/6] 导入新镜像（此时旧容器仍在运行）"
gunzip -dc -- "$ARCHIVE" | docker load
docker image inspect "$IMAGE_REF" >/dev/null 2>&1 || {
  restore_old_tag
  echo "错误: 归档中没有预期镜像 $IMAGE_REF；请用 --image 指定实际名称" >&2
  exit 1
}
PLATFORM="$(docker image inspect "$IMAGE_REF" --format '{{.Os}}/{{.Architecture}}')"
[ "$PLATFORM" = "linux/amd64" ] || {
  restore_old_tag
  echo "错误: 镜像平台为 $PLATFORM，预期 linux/amd64" >&2
  exit 1
}
NEW_IMAGE_ID="$(docker image inspect "$IMAGE_REF" --format '{{.Id}}')"

echo "==> [3/6] 停止并删除旧容器（仅 $CONTAINER）"
if docker container inspect "$CONTAINER" >/dev/null 2>&1; then
  docker container rm -f "$CONTAINER"
fi

start_container() {
  docker run -d \
    --name "$CONTAINER" \
    --restart unless-stopped \
    -p "${HOST_PORT}:8000" \
    "${RUN_ENV[@]}" \
    -v "${DATA_VOLUME}:/app/data" \
    "$1" >/dev/null
}

echo "==> [4/6] 启动新容器（数据卷 $DATA_VOLUME 保留）"
start_container "$IMAGE_REF"

echo "==> [5/6] 等待健康检查"
check_health() {
  docker exec "$CONTAINER" python -c \
    "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3).status == 200" \
    >/dev/null 2>&1
}

i=1
while [ "$i" -le "$HEALTH_RETRIES" ]; do
  if check_health; then
    echo "==> [6/6] 清理旧镜像"
    if [ -n "$OLD_IMAGE_ID" ] && [ "$OLD_IMAGE_ID" != "$NEW_IMAGE_ID" ]; then
      docker image rm "$OLD_IMAGE_ID" >/dev/null 2>&1 || \
        echo "提示: 旧镜像仍被其他标签或容器引用，已保留: $OLD_IMAGE_ID"
    fi
    echo "完成: $CONTAINER 已就绪，访问 http://<服务器公网IP>:${HOST_PORT}/"
    echo "日志: docker logs -f $CONTAINER"
    exit 0
  fi
  if [ "$(docker inspect "$CONTAINER" --format '{{.State.Status}}')" = "exited" ]; then
    break
  fi
  sleep 3
  i=$((i + 1))
done

echo "错误: 容器未通过健康检查，最近日志如下:" >&2
docker logs --tail 100 "$CONTAINER" >&2 || true
if [ -n "$OLD_IMAGE_ID" ] && [ "$OLD_IMAGE_ID" != "$NEW_IMAGE_ID" ]; then
  echo "==> 新版本失败，恢复旧镜像 $OLD_IMAGE_ID" >&2
  docker container rm -f "$CONTAINER" >/dev/null 2>&1 || true
  docker image tag "$OLD_IMAGE_ID" "$IMAGE_REF"
  start_container "$IMAGE_REF"
  echo "旧版本已重新启动，请检查: docker logs -f $CONTAINER" >&2
else
  echo "没有可回滚的旧镜像。" >&2
fi
exit 1
