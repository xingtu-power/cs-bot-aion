#!/usr/bin/env bash
# =============================================================================
# cs-bot-aion · 云服务器(阿里云 ECS)一键部署脚本(方式 A)
#
#   在已 clone 代码的云服务器上,从零完成:
#     环境自检 → .env 配置(API Key) → Docker Hub 可达性预检
#     → docker compose 构建镜像 → 启动容器 → 健康检查 → 打印访问地址
#
#   特点:
#     * 走 docker compose 原生构建,**不需要 buildx**(那是本机出离线包才需要的)
#     * 幂等:重复执行只会重建并替换现有 csbot 容器,数据卷不丢
#     * 国内 ECS 默认: pip 走阿里云镜像;模型走 hf-mirror(见 deploy/.env)
#
# 用法:
#   export DEEPSEEK_API_KEY='sk-xxx'
#   ./deploy/onekey-deploy.sh                  # 默认国内 ECS 配置
#   ./deploy/onekey-deploy.sh --api-key sk-xxx
#   ./deploy/onekey-deploy.sh --overseas       # 海外 ECS:pypi 官方 + huggingface.co
#   ./deploy/onekey-deploy.sh --docker-mirror https://docker.m.daocloud.io   # Docker Hub 加速
# =============================================================================
set -euo pipefail

CONTAINER="csbot"
HOST_PORT="${HOST_PORT:-8000}"
API_KEY="${DEEPSEEK_API_KEY:-}"
OVERSEAS=0
DOCKER_MIRROR=""
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-900}"   # 秒;首次启动要下载 e5 模型,可调大
PULL_TIMEOUT="${PULL_TIMEOUT:-120}"
SKIP_PULL_TEST="${SKIP_PULL_TEST:-0}"

usage() {
  cat <<'EOF'
用法: ./deploy/onekey-deploy.sh [选项]

选项:
      --api-key KEY        DeepSeek API Key(也可 export DEEPSEEK_API_KEY,
                           或后续在 deploy/.env 中填写;缺省且非交互时退出)
  -p, --port PORT          公网端口(默认 8000,需与安全组放行端口一致)
      --overseas           海外 ECS:pip 官方源 + huggingface.co(默认国内镜像配置)
      --docker-mirror URL  把 Docker Hub 加速镜像写入 /etc/docker/daemon.json
                           并重启 Docker(例: https://docker.m.daocloud.io)
  -h, --help               显示帮助

环境变量: HOST_PORT / DEEPSEEK_API_KEY / HEALTH_TIMEOUT / SKIP_PULL_TEST=1
          PUBLIC_IP(手动指定公网 IP,跳过自动探测)
要求: Docker Engine ≥ 20.10 且装有 Compose v2(docker compose),无需 buildx。
EOF
}

need_value() {
  [ "$#" -ge 2 ] && [ -n "$2" ] || { echo "错误: $1 缺少参数" >&2; exit 2; }
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --api-key) need_value "$@"; API_KEY="$2"; shift 2 ;;
    -p|--port) need_value "$@"; HOST_PORT="$2"; shift 2 ;;
    --overseas) OVERSEAS=1; shift ;;
    --docker-mirror) need_value "$@"; DOCKER_MIRROR="$2"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "错误: 未知参数 $1" >&2; usage >&2; exit 2 ;;
  esac
done

case "$HOST_PORT" in ''|*[!0-9]*) echo "错误: 端口必须是数字" >&2; exit 2 ;; esac
[ "$HOST_PORT" -ge 1 ] && [ "$HOST_PORT" -le 65535 ] || { echo "错误: 端口范围应为 1-65535" >&2; exit 2; }

if [ "$(id -u)" -eq 0 ]; then SUDO=""; else SUDO="sudo"; fi

# ---- 定位项目根目录(脚本放在 deploy/ 或仓库根目录均可) --------------------
SELF_DIR="$(cd "$(dirname "$0")" && pwd)"
COMPOSE_FILE=""
for C in "$SELF_DIR/docker-compose.yml" "$SELF_DIR/../deploy/docker-compose.yml"; do
  if [ -f "$C" ]; then COMPOSE_FILE="$C"; break; fi
done
[ -n "$COMPOSE_FILE" ] || { echo "错误: 找不到 docker-compose.yml(请确认脚本位于仓库 deploy/ 下)" >&2; exit 1; }
ROOT_DIR="$(cd "$(dirname "$(dirname "$COMPOSE_FILE")")" && pwd)"
ENV_FILE="$ROOT_DIR/deploy/.env"
ENV_EXAMPLE="$ROOT_DIR/deploy/.env.example"
for F in "$ROOT_DIR/requirements.txt" "$ROOT_DIR/deploy/Dockerfile"; do
  [ -f "$F" ] || { echo "错误: 缺少项目文件 $F,请在 cs-bot-aion 仓库内运行" >&2; exit 1; }
done

echo "==> 项目根目录: $ROOT_DIR"
echo "==> Compose 文件: $COMPOSE_FILE"

# ---- [1] Docker 环境自检 -----------------------------------------------------
command -v docker >/dev/null 2>&1 || {
  echo "错误: 未安装 Docker。参考 deploy/README.md「方式 A」安装:" >&2
  echo "  sudo dnf install -y dnf-utils" >&2
  echo "  sudo dnf config-manager --add-repo https://mirrors.aliyun.com/docker-ce/linux/centos/docker-ce.repo" >&2
  echo "  sudo sed -i 's/\$releasever/9/g' /etc/yum.repos.d/docker-ce.repo" >&2
  echo "  sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin" >&2
  echo "  sudo systemctl enable --now docker" >&2
  exit 1
}
docker info >/dev/null 2>&1 || {
  echo "错误: Docker 未运行或当前用户无权限。请用 root / sudo,或先 systemctl start docker" >&2
  exit 1
}
if ! docker compose version >/dev/null 2>&1; then
  echo "错误: 未安装 Compose v2(docker compose)。安装:" >&2
  echo "  sudo dnf install -y docker-compose-plugin   # docker-ce 仓库已配置时" >&2
  echo "  # Ubuntu/Debian: curl -fsSL https://get.docker.com | sh" >&2
  exit 1
fi
SERVER_VERSION="$(docker version --format '{{.Server.Version}}' 2>/dev/null || true)"
SERVER_MAJOR="${SERVER_VERSION%%.*}"
if [ -z "$SERVER_VERSION" ] || [ "${SERVER_MAJOR:-0}" -lt 20 ]; then
  echo "错误: Docker Engine 版本过旧(${SERVER_VERSION:-未知},需 ≥ 20.10 以支持 BuildKit/Compose 构建)。" >&2
  echo "      请按 deploy/README.md「方式 A」升级到 docker-ce 后重试。" >&2
  exit 1
fi
echo "==> Docker 就绪: Server ${SERVER_VERSION} / Compose v2 / 架构 $(uname -m)"

# ---- 可选:Docker Hub 加速(国内 ECS 拉基础镜像慢/失败时用) --------------------
if [ -n "$DOCKER_MIRROR" ]; then
  echo "==> 配置 Docker Hub 加速镜像: $DOCKER_MIRROR"
  case "$DOCKER_MIRROR" in
    http://*|https://*) ;;
    *) echo "错误: --docker-mirror 需要完整 URL(https://...)" >&2; exit 2 ;;
  esac
  DAEMON_JSON="/etc/docker/daemon.json"
  if [ -s "$DAEMON_JSON" ]; then
    if command -v python3 >/dev/null 2>&1; then
      $SUDO python3 - "$DAEMON_JSON" "$DOCKER_MIRROR" <<'PY'
import json, sys
path, mirror = sys.argv[1], sys.argv[2]
with open(path) as f:
    cfg = json.load(f)
mirrors = cfg.get("registry-mirrors") or []
if mirror not in mirrors:
    mirrors.append(mirror)
cfg["registry-mirrors"] = mirrors
with open(path, "w") as f:
    json.dump(cfg, f, indent=2, ensure_ascii=False)
PY
    else
      echo "错误: $DAEMON_JSON 已存在且无 python3,无法合并。请手动在其中追加:" >&2
      echo "  \"registry-mirrors\": [\"$DOCKER_MIRROR\"]" >&2
      exit 1
    fi
  else
    $SUDO sh -c "echo '{\"registry-mirrors\": [\"$DOCKER_MIRROR\"]}' > '$DAEMON_JSON'"
  fi
  echo "==> 重启 Docker 使镜像加速生效 ..."
  $SUDO systemctl restart docker
  for i in $(seq 1 15); do docker info >/dev/null 2>&1 && break; sleep 1; done
  docker info >/dev/null 2>&1 || { echo "错误: Docker 重启后仍不可用" >&2; exit 1; }
fi

# ---- 资源提示(不阻断) ----------------------------------------------------------
FREE_KB="$(df -Pk / | awk 'NR==2 {print $4}')"
FREE_GB=$((FREE_KB / 1024 / 1024))
[ "$FREE_GB" -ge 4 ] || { echo "错误: 磁盘剩余不足 4GB(${FREE_GB}GB),无法完成构建" >&2; exit 1; }
[ "$FREE_GB" -ge 15 ] || echo "警告: 磁盘仅剩 ${FREE_GB}GB,建议 ≥ 15GB(镜像+模型缓存+日志)"

MEM_MB="$(free -m | awk '/^Mem:/ {print $2}')"
SWAP_MB="$(free -m | awk '/^Swap:/ {print $2}')"
if [ "$MEM_MB" -lt 4000 ] && [ "$SWAP_MB" -lt 512 ]; then
  echo "警告: 内存 ${MEM_MB}MB 且无 swap,e5 推理可能内存不足。建议:" 
  echo "  fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile"
  echo "  echo '/swapfile none swap sw 0 0' >> /etc/fstab"
fi

# ---- [2] 初始化 deploy/.env ------------------------------------------------------
if [ -f "$ENV_FILE" ]; then
  echo "==> 使用已有 $ENV_FILE"
  # 若已有 .env 但没配任何镜像源,补上区域默认(不覆盖用户已有配置)
  if ! grep -q '^PIP_INDEX_URL=' "$ENV_FILE" && ! grep -q '^HF_ENDPOINT=' "$ENV_FILE"; then
    if [ "$OVERSEAS" -eq 1 ]; then
      printf '\n# --- onekey-deploy: 海外 ECS ---\nHF_ENDPOINT=https://huggingface.co\n' >>"$ENV_FILE"
    else
      printf '\n# --- onekey-deploy: 国内 ECS(阿里云 pip 加速)---\nPIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple\n' >>"$ENV_FILE"
    fi
    echo "    已补写区域默认镜像源到 $ENV_FILE"
  fi
else
  echo "==> 从 .env.example 生成 $ENV_FILE"
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  if [ "$OVERSEAS" -eq 1 ]; then
    printf '\n# --- onekey-deploy: 海外 ECS ---\nHF_ENDPOINT=https://huggingface.co\n' >>"$ENV_FILE"
  else
    printf '\n# --- onekey-deploy: 国内 ECS(阿里云 pip 加速)---\nPIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple\n' >>"$ENV_FILE"
  fi
  chmod 600 "$ENV_FILE"
fi

# API Key: 参数 > 环境变量 > .env 已有 > 交互输入
ENV_KEY="$(sed -n 's/^DEEPSEEK_API_KEY=//p' "$ENV_FILE" 2>/dev/null | head -n1 | tr -d '[:space:]' || true)"
if [ -z "$API_KEY" ] && [ -n "$ENV_KEY" ] && [ "$ENV_KEY" != "sk-xxx" ] && [ "$ENV_KEY" != "sk-你的实际Key" ]; then
  API_KEY="$ENV_KEY"
fi
if [ -z "$API_KEY" ] && [ -t 0 ]; then
  printf "请输入 DeepSeek API Key(sk-...): " >&2
  read -rs API_KEY
  printf '\n' >&2
fi
if [ -z "$API_KEY" ]; then
  echo "错误: 缺少 DEEPSEEK_API_KEY。可 export DEEPSEEK_API_KEY='sk-xxx'、加 --api-key 参数,或在 $ENV_FILE 中填写" >&2
  exit 1
fi
# 写入 .env(替换占位/空值),并导出供 compose 插值
KEY_SED="${API_KEY//&/\\&}"
if grep -q '^DEEPSEEK_API_KEY=' "$ENV_FILE"; then
  sed -i.bak "s|^DEEPSEEK_API_KEY=.*|DEEPSEEK_API_KEY=${KEY_SED}|" "$ENV_FILE" && rm -f "$ENV_FILE.bak"
else
  printf 'DEEPSEEK_API_KEY=%s\n' "$API_KEY" >>"$ENV_FILE"
fi
export DEEPSEEK_API_KEY="$API_KEY"
export HOST_PORT="$HOST_PORT"   # 让 -p/默认端口 覆盖 deploy/.env 中的值,保证映射与健康检查一致
chmod 600 "$ENV_FILE"
echo "==> DEEPSEEK_API_KEY 已配置(长度 ${#API_KEY}),写入 $ENV_FILE"

# ---- [3] Docker Hub 可达性预检(仅首次,拉取基础镜像) ------------------------------
if [ "$SKIP_PULL_TEST" = "1" ]; then
  echo "==> 已设置 SKIP_PULL_TEST=1,跳过基础镜像预检"
elif docker image inspect python:3.11-slim >/dev/null 2>&1; then
  echo "==> 基础镜像 python:3.11-slim 已在本地,跳过预检"
else
  echo "==> 预拉基础镜像 python:3.11-slim(验证 Docker Hub 可达性,最多 ${PULL_TIMEOUT}s)..."
  PULL_OK=0
  if command -v timeout >/dev/null 2>&1; then
    timeout "$PULL_TIMEOUT" docker pull python:3.11-slim && PULL_OK=1 || true
  else
    docker pull python:3.11-slim && PULL_OK=1 || true
  fi
  if [ "$PULL_OK" -ne 1 ]; then
    echo "错误: 拉取基础镜像失败/超时。国内 ECS 可用 Docker Hub 加速重跑:" >&2
    echo "  ./deploy/onekey-deploy.sh --docker-mirror https://docker.m.daocloud.io" >&2
    echo "  (其他可用镜像地址可自行替换;镜像已在本地时可用 SKIP_PULL_TEST=1 跳过)" >&2
    exit 1
  fi
fi

# ---- [4] 构建并启动 --------------------------------------------------------------
echo "==> docker compose 构建并启动(首次构建需下载 pip 依赖,约几分钟;日志见屏幕输出)"
(
  cd "$ROOT_DIR"
  docker compose -f deploy/docker-compose.yml up -d --build
)
docker container inspect "$CONTAINER" >/dev/null 2>&1 || {
  echo "错误: 容器 $CONTAINER 未能启动,最近日志:" >&2
  docker compose -f "$COMPOSE_FILE" logs --tail 80 2>/dev/null || true
  exit 1
}

# ---- [5] 等待健康检查 --------------------------------------------------------------
check_health() {
  if command -v curl >/dev/null 2>&1; then
    curl -fsS --max-time 5 "http://127.0.0.1:${HOST_PORT}/health" >/dev/null 2>&1 && return 0
  fi
  docker exec "$CONTAINER" python -c \
    "import urllib.request; assert urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=5).status==200" \
    >/dev/null 2>&1 && return 0
  return 1
}

echo "==> 等待服务就绪(最长 ${HEALTH_TIMEOUT}s;首次启动会先下载 e5 模型 ~2.3GB) ..."
RC_BASE="$(docker inspect -f '{{.RestartCount}}' "$CONTAINER" 2>/dev/null || echo 0)"
NOW_S="$(date +%s)"
DEADLINE=$((NOW_S + HEALTH_TIMEOUT))
LAST_MSG=0
HEALTHY=0
while [ "$(date +%s)" -lt "$DEADLINE" ]; do
  if check_health; then HEALTHY=1; break; fi
  STATUS="$(docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null || echo unknown)"
  RC_NOW="$(docker inspect -f '{{.RestartCount}}' "$CONTAINER" 2>/dev/null || echo 0)"
  if [ "$STATUS" != "running" ] || [ "$RC_NOW" -gt "$RC_BASE" ]; then
    echo "错误: 容器异常($STATUS,重启次数 ${RC_NOW}),最近日志:" >&2
    docker logs --tail 100 "$CONTAINER" >&2 || true
    echo "常见原因: 无法连接模型源(hf-mirror/huggingface)或环境变量错误。" >&2
    echo "处理: 修改 $ENV_FILE 中 HF_ENDPOINT(国内 hf-mirror / 海外 huggingface.co),再执行:" >&2
    echo "  docker compose -f '$COMPOSE_FILE' up -d" >&2
    exit 1
  fi
  ELAPSED=$(( $(date +%s) - NOW_S ))
  if [ $((ELAPSED - LAST_MSG)) -ge 30 ]; then
    echo "   · 已等待 ${ELAPSED}s,容器运行中 ... 可另开终端观察: docker logs -f $CONTAINER"
    LAST_MSG=$ELAPSED
  fi
  sleep 15
done

# ---- [6] 汇总 -----------------------------------------------------------------------
get_public_ip() {
  [ -n "${PUBLIC_IP:-}" ] && { echo "$PUBLIC_IP"; return 0; }
  for U in https://api.ipify.org https://ifconfig.me; do
    IP="$(curl -4 -fsS --max-time 5 "$U" 2>/dev/null || true)"
    [ -n "$IP" ] && { echo "$IP"; return 0; }
  done
  hostname -I 2>/dev/null | awk '{print $1}'
}

if [ "$HEALTHY" -eq 1 ]; then
  echo
  echo "✅ 部署成功!服务健康检查通过。"
  echo "   容器: $CONTAINER(restart=unless-stopped,数据卷保留)"
  echo "   访问: http://$(get_public_ip):${HOST_PORT}/"
  echo "   健康: curl http://127.0.0.1:${HOST_PORT}/health   →  {\"ok\": true}"
  echo "   日志: docker logs -f $CONTAINER"
  echo "   若公网打不开,请检查 ECS 安全组/轻量服务器防火墙是否放行 TCP ${HOST_PORT}"
  exit 0
fi

echo
echo "⚠️  容器已启动但 ${HEALTH_TIMEOUT}s 内未就绪(通常仍在下载 e5 模型 ~2.3GB,首次可能需数分钟~数十分钟)。"
STATUS="$(docker inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null || echo unknown)"
if [ "$STATUS" = "running" ]; then
  echo "   容器运行中,restart 策略会自动重试,无需干预。"
  echo "   观察进度: docker logs -f $CONTAINER   (看到 '[preload] e5 模型已就绪' 即完成)"
  echo "   就绪后访问: http://$(get_public_ip):${HOST_PORT}/"
  echo "   或延长等待后重跑本脚本(幂等,会重建容器但不丢数据卷): HEALTH_TIMEOUT=1800 ./deploy/onekey-deploy.sh"
  exit 0
else
  echo "   容器状态: $STATUS,最近日志:" >&2
  docker logs --tail 80 "$CONTAINER" >&2 || true
  echo "   处理: 见 $ENV_FILE 中 HF_ENDPOINT 等配置,改完重跑本脚本。" >&2
  exit 1
fi
