#!/usr/bin/env bash
# =====================================================================
# cs-bot-aion 一键发布:本机交叉构建 linux/amd64 → 上传阿里云 ACR
#
# 用法:
#   deploy/build-push.sh --namespace <命名空间>                  # 默认推 registry.cn-hangzhou.aliyuncs.com/<ns>/cs-bot-aion:latest
#   deploy/build-push.sh -n myns -t v1 --with-model             # 打 v1 tag 并预置 2.3GB 模型
#   deploy/build-push.sh -r registry.ap-southeast-1.aliyuncs.com -n myns   # 海外地域仓库
#   deploy/build-push.sh --push-only -n myns -t v1              # 复用本地已构建镜像只做推送
#
# 参数说明:
#   -r|--registry    ACR 仓库域名(默认 registry.cn-hangzhou.aliyuncs.com)
#   -n|--namespace   ACR 命名空间(必填;或 export ACR_NAMESPACE=xxx)
#   -i|--image       镜像名(默认 cs-bot-aion)
#   -t|--tag         版本 tag(默认 latest)
#   --with-model     构建期预置 e5 模型(镜像 +2.3GB,云上首次零下载)
#   --push-only      跳过构建,只推送本地已有镜像
#   --skip-login     跳过 docker login(已登录过时用)
#   -h|--help        帮助
#
# 环境变量可替代参数: ACR_REGISTRY / ACR_NAMESPACE / IMAGE / TAG / PIP_INDEX_URL
# =====================================================================
set -euo pipefail

REGISTRY="${ACR_REGISTRY:-registry.cn-hangzhou.aliyuncs.com}"
NAMESPACE="${ACR_NAMESPACE:-}"
IMAGE="${IMAGE:-cs-bot-aion}"
TAG="${TAG:-latest}"
BAKE_MODEL=0
ONLY_PUSH=0
SKIP_LOGIN=0
PIP_INDEX_URL="${PIP_INDEX_URL:-}"

usage() {
    sed -n '2,25p' "$0" | sed 's/^# \{0,1\}//'
    exit "${1:-0}"
}

while [ $# -gt 0 ]; do
    case "$1" in
        -r|--registry)   REGISTRY="$2"; shift 2 ;;
        -n|--namespace)  NAMESPACE="$2"; shift 2 ;;
        -i|--image)      IMAGE="$2"; shift 2 ;;
        -t|--tag)        TAG="$2"; shift 2 ;;
        --with-model)    BAKE_MODEL=1; shift ;;
        --push-only)     ONLY_PUSH=1; shift ;;
        --skip-login)    SKIP_LOGIN=1; shift ;;
        -h|--help)       usage 0 ;;
        *) echo "未知参数: $1"; usage 1 ;;
    esac
done

[ -n "$NAMESPACE" ] || {
    echo "错误:缺少 ACR 命名空间。用 --namespace xxx,或 export ACR_NAMESPACE=xxx"; usage 1
}
command -v docker >/dev/null 2>&1 || { echo "错误:本机未安装 Docker"; exit 1; }
docker info >/dev/null 2>&1 || { echo "错误:Docker 未在运行(Docker Desktop 启动了吗?)"; exit 1; }

REF="$REGISTRY/$NAMESPACE/$IMAGE:$TAG"
cd "$(dirname "$0")/.."     # 切到项目根目录

echo "==> 目标镜像: $REF"

# 1) 登录(仅推送时需要;ACR 用户名=阿里云账号,密码=镜像仓库「独立登录密码」)
if [ "$SKIP_LOGIN" = 0 ]; then
    echo "==> [登录] docker login $REGISTRY"
    docker login "$REGISTRY"
fi

# 2) 构建 / 复用本地镜像
if [ "$ONLY_PUSH" = 0 ]; then
    echo "==> [构建] 交叉构建 linux/amd64${BAKE_MODEL:+(含 2.3GB 模型,耗时较长)} ..."
    args=(--platform linux/amd64 --push -f deploy/Dockerfile -t "$REF" .)
    [ "$BAKE_MODEL" = 1 ] && args+=(--build-arg BAKE_MODEL=1)
    [ -n "$PIP_INDEX_URL" ] && args+=(--build-arg "PIP_INDEX_URL=$PIP_INDEX_URL")
    docker buildx build "${args[@]}"
else
    echo "==> [推送] --push-only:复用本地镜像 $IMAGE:$TAG"
    docker tag "$IMAGE:$TAG" "$REF"
    docker push "$REF"
fi

echo
echo "================================================================"
echo " 完成 ✔  已推送: $REF"
echo "================================================================"
echo "ECS(Alibaba Cloud Linux 4)上一键拉取运行:"
echo
echo "  docker pull $REF"
echo "  docker run -d --name csbot --restart unless-stopped \\"
echo "    -p 8000:8000 \\"
echo "    -e DEEPSEEK_API_KEY='sk-你的key' \\"
echo "    -v csbot_data:/app/data \\"
echo "    -v csbot_emb:/app/tools/emb_cache \\"
echo "    -v csbot_hf:/app/tools/hf_cache \\"
echo "    $REF"
echo
echo "首次启动会预下载 e5 模型(仅当未用 --with-model);查看: docker logs -f csbot"
echo "安全组记得放行 TCP 8000。"
