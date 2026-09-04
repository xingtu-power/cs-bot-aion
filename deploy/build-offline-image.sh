#!/usr/bin/env bash
# 本机一键构建可离线加载 E5 的 linux/amd64 镜像，并导出 tar.gz + SHA-256。
set -euo pipefail

IMAGE="${IMAGE:-cs-bot-aion}"
TAG="${TAG:-al4-amd64-e5}"
OUTPUT_DIR="${OUTPUT_DIR:-csbot-dist}"
PIP_INDEX_URL="${PIP_INDEX_URL:-}"
PRUNE_BUILD_CACHE=0

usage() {
  cat <<'EOF'
用法: ./deploy/build-offline-image.sh [选项]

选项:
  -i, --image NAME       镜像名（默认: cs-bot-aion）
  -t, --tag TAG          镜像标签（默认: al4-amd64-e5）
  -o, --output-dir DIR   输出目录（默认: csbot-dist）
      --prune-cache      构建成功后清理未使用的 buildx 缓存
  -h, --help             显示帮助

也可使用环境变量 IMAGE、TAG、OUTPUT_DIR、PIP_INDEX_URL。
脚本只删除同名旧镜像和同名旧导出文件，不会删除其他镜像或容器。
EOF
}

need_value() {
  [ "$#" -ge 2 ] && [ -n "$2" ] || { echo "错误: $1 缺少参数" >&2; exit 2; }
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    -i|--image) need_value "$@"; IMAGE="$2"; shift 2 ;;
    -t|--tag) need_value "$@"; TAG="$2"; shift 2 ;;
    -o|--output-dir) need_value "$@"; OUTPUT_DIR="$2"; shift 2 ;;
    --prune-cache) PRUNE_BUILD_CACHE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "错误: 未知参数 $1" >&2; usage >&2; exit 2 ;;
  esac
done

if ! [[ "$IMAGE" =~ ^[a-z0-9]+([._-][a-z0-9]+)*(/[a-z0-9]+([._-][a-z0-9]+)*)*$ ]]; then
  echo "错误: 镜像名不合法（仅允许小写字母、数字及分隔符 . _ - /）" >&2
  exit 2
fi
case "$TAG" in
  ''|*[!A-Za-z0-9_.-]*|[.-]*|*/*)
    echo "错误: 镜像 tag 不合法" >&2
    exit 2
    ;;
esac
[ "${#TAG}" -le 128 ] || { echo "错误: 镜像 tag 最长 128 个字符" >&2; exit 2; }
case "$OUTPUT_DIR" in
  ''|/*|..|../*|*/../*|*/..)
    echo "错误: 输出目录必须是项目内的相对路径，且不能包含 .." >&2
    exit 2
    ;;
esac

command -v docker >/dev/null 2>&1 || { echo "错误: 未安装 Docker" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "错误: Docker 未运行" >&2; exit 1; }
docker buildx version >/dev/null 2>&1 || { echo "错误: Docker buildx 不可用" >&2; exit 1; }

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
OUTPUT_DIR="$ROOT_DIR/$OUTPUT_DIR"
mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd -P)"
case "$OUTPUT_DIR/" in
  "$ROOT_DIR"/*/) ;;
  *) echo "错误: 输出目录解析后不在项目目录内" >&2; exit 2 ;;
esac
IMAGE_REF="${IMAGE}:${TAG}"
ARCHIVE="$OUTPUT_DIR/${IMAGE//\//-}-${TAG}.tar.gz"
CHECKSUM="${ARCHIVE}.sha256"

echo "==> [1/5] 清理同名旧镜像和旧导出文件"
if docker image inspect "$IMAGE_REF" >/dev/null 2>&1; then
  docker image rm -f "$IMAGE_REF"
fi
rm -f -- "$ARCHIVE" "$CHECKSUM"

echo "==> [2/5] 构建 linux/amd64 镜像，并将 E5 模型写入镜像"
BUILD_ARGS=(--platform linux/amd64 --load --file "$ROOT_DIR/deploy/Dockerfile" --tag "$IMAGE_REF" --build-arg BAKE_MODEL=1)
if [ -n "$PIP_INDEX_URL" ]; then
  BUILD_ARGS+=(--build-arg "PIP_INDEX_URL=$PIP_INDEX_URL")
fi
docker buildx build "${BUILD_ARGS[@]}" "$ROOT_DIR"

echo "==> [3/5] 断网验证内置 E5 模型"
docker run --rm --platform linux/amd64 --network none \
  --entrypoint python "$IMAGE_REF" tools/preload_model.py 1

echo "==> [4/5] 导出并压缩镜像"
docker save "$IMAGE_REF" | gzip -1 >"$ARCHIVE"

echo "==> [5/5] 生成 SHA-256 校验文件"
(
  cd "$OUTPUT_DIR"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$(basename "$ARCHIVE")" >"$(basename "$CHECKSUM")"
  else
    shasum -a 256 "$(basename "$ARCHIVE")" >"$(basename "$CHECKSUM")"
  fi
)

if [ "$PRUNE_BUILD_CACHE" -eq 1 ]; then
  echo "==> 清理未使用的 buildx 缓存"
  docker builder prune -f
fi

echo
echo "构建完成"
echo "镜像: $IMAGE_REF"
echo "归档: $ARCHIVE"
echo "校验: $CHECKSUM"
echo "请把归档、校验文件和 deploy/load-and-run.sh 一起上传到服务器。"
