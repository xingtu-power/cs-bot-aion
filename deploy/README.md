# 部署到阿里云(ECS / 轻量应用服务器)

本目录是 `cs-bot-aion`(AION 海外智能客服)的部署包,提供三种方式:

- **方式 A**:云上 clone + Docker Compose 构建(常规;GitHub 访问慢可改用方式 B)
- **方式 B**:**本机构建镜像 → 上传(ACR / save-load)→ 云上直接 `docker run`,无需在云上 clone/构建**
- **方式 C**:systemd + venv(不用 Docker)

全程 **IP 直连(HTTP)**,暂不涉及域名/HTTPS。

> 面向泰国/澳洲用户时建议选购**海外地域**(如新加坡、悉尼),免备案且链路近;
> 纯演示/国内访问可选中国内地地域(绑域名需 ICP 备案)。
> 说明:国内访问 GitHub 慢时,可把仓库推到阿里云 Codeup 或 Gitee 后在服务器上 clone。

---

## 0. 服务器要求

| 项 | 建议 |
|---|---|
| 规格 | **2 vCPU / 4GB 起**(e5 模型推理 + onnxruntime 吃内存;4GB 建议配 2GB swap) |
| 磁盘 | **≥ 30GB**(代码 + pip 依赖 + 模型缓存 ~2.3GB + 日志) |
| 系统 | **Alibaba Cloud Linux 4 LTS(本机 ECS 所用)**/ 3 / Ubuntu 22.04 / Debian 12 均可 |
| 网络 | 安全组**放行 TCP ${HOST_PORT:-8000}**;出网需能访问 huggingface(或镜像)与 api.deepseek.com |
| 软件 | Docker Engine + Compose v2(方式 A/B)或 Python 3.9+(方式 C) |

检查: `nproc && free -h && df -h /` ;无 swap 可 `fallocate -l 2G /swapfile && chmod 600 /swapfile && mkswap /swapfile && swapon /swapfile`(写进 /etc/fstab 持久化)。

---

## 方式 A:Docker Compose(云上 clone + 构建)

### A1. 安装 Docker(如已装可跳过)

**Alibaba Cloud Linux 3 / 4 LTS(RHEL 兼容,dnf):**

```bash
sudo dnf install -y dnf-utils
sudo dnf config-manager --add-repo https://download.docker.com/linux/centos/docker-ce.repo
# 国内网络慢可改走阿里云镜像:
#   sudo sed -i 's+download.docker.com+mirrors.aliyun.com/docker-ce+' /etc/yum.repos.d/docker-ce.repo
# 若安装时因 $releasever 解析到不存在的目录而报 404,执行(按系统对应 EL 大版本 8/9):
#   sudo sed -i 's/$releasever/9/g' /etc/yum.repos.d/docker-ce.repo
sudo dnf install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo systemctl enable --now docker
docker compose version   # 需 v2
```

**Ubuntu/Debian:**

```bash
curl -fsSL https://get.docker.com | sh
sudo systemctl enable --now docker
docker compose version   # 需 v2
```

> 若命令与你的 ECS 版本有出入,以阿里云官方文档为准:
> [ECS 安装并使用 Docker(阿里云帮助中心)](https://www.alibabacloud.com/help/en/ecs/user-guide/install-and-use-docker) /
> [轻量应用服务器 手动部署 Docker](https://www.alibabacloud.com/help/zh/simple-application-server/use-cases/manually-deploy-docker)。

### A2. 拉代码

```bash
mkdir -p /opt && cd /opt
git clone https://github.com/xingtu-power/cs-bot-aion.git
cd cs-bot-aion
```

### A3. 配置环境变量

```bash
cd deploy
cp .env.example .env
vim .env        # 至少填 DEEPSEEK_API_KEY=sk-xxx
```

> **内部效果分析台 /admin**(只读):同进程独立页面,与访客聊天窗隔离(无入口)。
> 仅本机/内网使用可不设口令(默认放行 127.0.0.1/::1);若公网/多机访问,
> 在 `.env` 设置 `CSAPP_ADMIN_TOKEN=xxx`,访问 `/admin` 与 `/api/v1/admin/*` 时请求头带 `X-Admin-Token`。
> 每轮咨询的 Trace 落在 `data/analytics.db`(默认保留 180 天,可用 `CSAPP_ANALYTICS_RETENTION_DAYS` 调整)。

国内 ECS 建议同时在 `.env` 打开两行加速(去 #):

```
PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
# HF_ENDPOINT 保持默认 https://hf-mirror.com(国内镜像)
```

海外 ECS(新加坡/悉尼等)建议改为官方源:

```
PIP_INDEX_URL=https://pypi.org/simple
HF_ENDPOINT=https://huggingface.co
```

### A4. 构建并启动

```bash
docker compose up -d --build        # 首次构建:下载 pip 依赖,约几分钟
docker compose logs -f              # 观察首次启动:下载 e5 模型 ~2.3GB(视带宽数分钟~数十分钟)
```

看到 `[preload] e5 模型已就绪。` 与 `csapp server on http://0.0.0.0:8000 (threaded)` 即成功。

### A5. 验证

```bash
curl http://127.0.0.1:8000/health        # 服务器本机: {"ok": true}
# 公网(浏览器/本地机器): http://<ECS公网IP>:8000/        聊天页
curl -X POST http://<ECS公网IP>:8000/api/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"What is the range of the AION UT?"}'
```

> 公网不通先查 ECS **安全组入方向**是否放行 TCP 8000(轻量服务器则在控制台「防火墙」放行)。

### A6. 日常运维

```bash
docker compose logs -f --tail 200          # 看日志
docker compose restart                      # 重启
docker compose up -d --build                # 升级代码后重建
docker compose down                         # 停止(数据在卷里,不丢)
docker volume ls                            # 卷:data / emb_cache / hf_cache

# 数据落盘位置(备份/迁移用):
#   docker volume inspect cs-bot-aion_data        → /var/lib/docker/volumes/.../_data
#   备份 = 把该目录(或整卷)拷走;恢复 = 拷回后 docker compose up -d
```

> 首次启动若因模型下载中断(restart 策略会自动重试),`tools/emb_cache` 卷已持久化,断点续传。
> 想彻底重来: `docker compose down -v`(会清空数据+模型缓存,慎用)。

### A7. 灰度测试(--test)与切换/回滚

> 适用:线上已是旧镜像,想先部署一个**最新代码的并行测试容器**,测 OK 再替换线上。

```bash
cd cs-bot-aion && git pull                       # 拉到 main 最新代码

# 1) 并行灰度测试(构建最新镜像,起 csbot-test :8001,独立数据卷,复用模型缓存卷)
#    线上 csbot 完全不受影响;--admin-token 为分析台 /admin 口令(可选)
./deploy/onekey-deploy.sh --test --admin-token '你的token'
#    验证: http://<公网IP>:8001/  与  /admin(X-Admin-Token); curl http://127.0.0.1:8001/health

# 2) 测试 OK → 把线上容器换成最新代码(镜像已就绪,本次只替换 csbot)
./deploy/onekey-deploy.sh --admin-token '你的token'
#    或手动: docker compose -f deploy/docker-compose.yml up -d --build
#    清理测试容器(可选): docker rm -f csbot-test

# 回滚(任一模式构建前都会把旧镜像存为 cs-bot-aion:prev)
docker tag cs-bot-aion:prev cs-bot-aion:latest \
  && docker compose -f deploy/docker-compose.yml up -d --force-recreate
```

`--test` 可选参数:`--test-port PORT`(默认 8001)、`--test-name NAME`(默认 csbot-test)、
`--test` 使用独立数据卷 `csbot_test_data`,不会读/写线上 `cs-bot-aion_data`,也绝不停删线上容器;
如需为线上用户持续保留灰度环境,给测试端口在安全组放行即可。
CSAPP_ADMIN_TOKEN 已由 compose/脚本透传;不设则 /admin 仅本机回环可访问。

> ⚠️ **内存**：e5 模型被**每个容器各加载一份**，并行灰度 = 线上与测试两份常驻内存
> （单份约 2.6GB，4GB 实例会 OOM）。脚本 `--test` 启动前会自动做内存预检，不足会拒绝
> 并给出三条出路：①加 swap 后重试；②小内存用**停机灰度**——
> `docker stop csbot && ./deploy/onekey-deploy.sh --test --force-test`（此时只有测试容器在跑，
> 测完 `docker rm -f csbot-test` 再跑默认脚本切线上）；③升级实例内存。
> 确知可承担风险再 `--force-test` 强行并行。

> 方式 B(离线 tar.gz)同样支持并行:见下文 `load-and-run.sh` 的 `--container/-p/--data-volume`。

---

## 方式 B:本机构建镜像 → 上传 → 云上直接跑(无需在云上 clone)

> 适合:不想在云上 clone/装构建环境、GitHub 慢、想本地统一出镜像的场景。
> 前提:本地已装 Docker。**架构必须匹配** —— ECS 基本是 x86_64(amd64),
> 本机若是 Apple Silicon(arm64,`uname -m` 输出 arm64)必须交叉构建 `linux/amd64`,
> 否则镜像无法在 ECS 运行。先在两边确认:

```bash
uname -m        # 本机与 ECS 各跑一次;ECS 应为 x86_64
```

### B0. 离线部署一键流程(内置 E5，推荐)

适用于云服务器无法下载 Embedding 模型的场景。本地脚本会清理同名旧镜像/导出包，
构建 `linux/amd64` 镜像并内置 E5，断网验证后导出压缩包和 SHA-256：

```bash
cd <cs-bot-aion 项目根目录>
./deploy/build-offline-image.sh
# 可选：使用版本标签
# ./deploy/build-offline-image.sh --tag v1.0.0
```

默认产物位于 `csbot-dist/`：

```text
cs-bot-aion-al4-amd64-e5.tar.gz
cs-bot-aion-al4-amd64-e5.tar.gz.sha256
```

把上述两个文件和 `deploy/load-and-run.sh` 上传到服务器同一目录，然后执行：

```bash
chmod +x load-and-run.sh
export DEEPSEEK_API_KEY='sk-你的实际Key'
./load-and-run.sh cs-bot-aion-al4-amd64-e5.tar.gz
```

服务器脚本会先校验并导入新镜像，再停删旧容器、启动新容器并检查 `/health`。
业务数据卷 `csbot_data` 会保留；新版本未通过健康检查时会尝试恢复旧镜像。
内置模型时不要挂载 `/app/tools/emb_cache` 或 `/app/tools/hf_cache`，否则旧空卷可能遮住镜像内的模型。

> 该脚本默认目标是 `x86_64` ECS（`linux/amd64`）。服务器 `uname -m` 必须输出 `x86_64`。
> Dockerfile 已将 E5 设为独立缓存层，仅更新业务代码时通常不会重新下载 2.3GB 模型。

### B0-A. ACR 一键构建推送

仓库自带脚本 [`deploy/build-push.sh`](build-push.sh),本机执行即可完成
「交叉构建 amd64 → 登录 ACR → 推送」,并在结尾打印 ECS 上的拉取/运行命令:

```bash
cd <cs-bot-aion 项目根目录>
./deploy/build-push.sh --namespace <你的ACR命名空间>            # 推 registry.cn-hangzhou.aliyuncs.com/<ns>/cs-bot-aion:latest
# 常用变体:
./deploy/build-push.sh -n <ns> -t v1 --with-model               # 打 v1 并预置 2.3GB 模型(云上零下载)
./deploy/build-push.sh -n <ns> -t v1 --push-only                # 复用本地镜像,只推送
./deploy/build-push.sh -r registry.ap-southeast-1.aliyuncs.com -n <ns>   # 海外地域
# 或 export ACR_NAMESPACE=<ns> 后用默认参数;细节见脚本内帮助(./deploy/build-push.sh --help)
```

> ACR 首次使用:控制台开通「容器镜像服务」→ 创建**命名空间**与镜像仓库
> (名称建议 `cs-bot-aion`);登录密码用镜像仓库的**独立登录密码**,不是控制台登录密码。
> 国内 pip 慢可先 `export PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple` 再跑。

### B1. 手动版:本地构建 amd64 镜像

```bash
cd <cs-bot-aion 项目根目录>
# 国内本地网络可加 --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple
# --load 让镜像进入本地 docker images,便于后续 docker tag/push
docker buildx build --platform linux/amd64 --load -f deploy/Dockerfile -t cs-bot-aion:latest .
# 老版本 docker 用: docker build --platform linux/amd64 -f deploy/Dockerfile -t cs-bot-aion:latest .
```

> **可选:把 2.3GB 模型打进镜像**,让云上首次启动零下载(镜像 ~3.6GB):
> 构建命令加 `--build-arg BAKE_MODEL=1`(或脚本 `--with-model`)。
> 不预置也没关系——云上首次启动由 entrypoint 自动下载一次并存入持久卷。

### B2. 上传镜像(二选一)

**(a) 阿里云 ACR 容器镜像服务(推荐,同地域内网拉取快)**

```bash
# 控制台开通「容器镜像服务」→ 创建命名空间与仓库(如 cs-bot-aion),记下仓库地址
docker tag cs-bot-aion:latest registry.cn-hangzhou.aliyuncs.com/<命名空间>/cs-bot-aion:latest
docker login registry.cn-hangzhou.aliyuncs.com    # 用户名=阿里云账号,密码=仓库「独立登录密码」
docker push registry.cn-hangzhou.aliyuncs.com/<命名空间>/cs-bot-aion:latest
```

> 海外 ECS 选对应地域仓库,如新加坡 `registry.ap-southeast-1.aliyuncs.com`。

**(b) docker save / load(不走公网仓库)**

```bash
# 压缩后直接管道传过去(文件大,耗时取决于带宽)
docker save cs-bot-aion:latest | gzip | ssh <user>@<ECS公网IP> 'gunzip | docker load'
# 更稳妥:存成 tar.gz,传到 OSS 再从 ECS 下载后 docker load
docker save cs-bot-aion:latest | gzip > cs-bot-aion.tar.gz
```

### B3. 云上运行(不需要任何项目文件)

```bash
# 先取镜像:
#   (a) ACR:   docker pull registry.cn-hangzhou.aliyuncs.com/<命名空间>/cs-bot-aion:latest
#   (b) load:  镜像名即 cs-bot-aion:latest

docker run -d --name csbot --restart unless-stopped \
  -p 8000:8000 \
  -e DEEPSEEK_API_KEY='sk-xxx' \
  -e CSAPP_LLM_MODE=deepseek \
  -v csbot_data:/app/data \
  -v csbot_emb:/app/tools/emb_cache \
  -v csbot_hf:/app/tools/hf_cache \
  cs-bot-aion:latest
```

首次启动 entrypoint 会预下载 e5 模型(未预置到镜像时,~2.3GB):

```bash
docker logs -f csbot        # 看到 [preload] e5 模型已就绪 与
                            # csapp server on http://0.0.0.0:8000 (threaded) 即成功
```

验证与公网放行同「方式 A5」(安全组 TCP 8000)。

> 若想复用 compose 的 healthcheck/日志轮转:只需把 `deploy/docker-compose.yml` 和 `.env`
> 传到云上同一目录,再 `docker compose up -d`(**不要加 --build**,用已上传的镜像)。

**升级**:本地改代码 → 重新构建并打新 tag(如 `:v2`)→ push/load →
云上 `docker pull` 新 tag 后 `docker rm -f csbot && docker run ...`(卷复用,数据不丢)。

---

## 方式 C:systemd + venv(不用 Docker)

```bash
# 1) 系统依赖 + 用户
sudo apt-get update && sudo apt-get install -y python3 python3-venv python3-pip
sudo useradd -m -s /bin/bash csapp || true

# 2) 代码
sudo mkdir -p /opt && cd /opt
sudo git clone https://github.com/xingtu-power/cs-bot-aion.git
sudo chown -R csapp:csapp /opt/cs-bot-aion

# 3) 虚拟环境 + 依赖
cd /opt/cs-bot-aion
sudo -u csapp python3 -m venv venv
sudo -u csapp ./venv/bin/pip install -U pip
sudo -u csapp ./venv/bin/pip install -r requirements.txt     # 国内可加 -i https://mirrors.aliyun.com/pypi/simple

# 4) 密钥文件
echo 'DEEPSEEK_API_KEY=sk-xxx' | sudo tee /etc/csapp.env
sudo chmod 600 /etc/csapp.env

# 5) 首次启动前先下载模型(可选;不执行则服务启动时兜底,首条消息慢)
sudo -u csapp /opt/cs-bot-aion/venv/bin/python /opt/cs-bot-aion/tools/preload_model.py 3

# 6) 注册服务并启动
sudo cp deploy/csapp.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now csapp
sudo systemctl status csapp                                   # 确认 active (running)
curl http://127.0.0.1:8000/health

# 7) 开机自启的 swap(可选,防 OOM)
# 见「0. 服务器要求」;防火墙放行 8000(systemd 版本已绑 0.0.0.0,走安全组即可)

# 升级:
cd /opt/cs-bot-aion && sudo -u csapp git pull && sudo -u csapp ./venv/bin/pip install -r requirements.txt && sudo systemctl restart csapp
```

日志: `journalctl -u csapp -f` 。数据目录 `/opt/cs-bot-aion/data/`(备份时整体拷走)。

---

## 常见问题排查

### 留资报错 `table leads has no column named user_id`

原因是数据卷中的旧 `leads` 表缺少新字段；`CREATE TABLE IF NOT EXISTS` 不会更新已存在的表。
新版会在服务启动时自动补齐 `user_id TEXT`，保留已有记录；旧记录的该字段为 `NULL`。
升级时复用原数据卷，不要删除 `kd.db` 或执行 `docker compose down -v`。

若暂时无法更新镜像，可在云服务器执行以下命令（默认容器名 `csbot`）。
它先用 SQLite backup API 备份，再在写事务内检查并补列，可以重复执行：

```bash
docker exec -i csbot python - <<'PY'
import sqlite3
from datetime import datetime, timezone

path = '/app/data/kd.db'
backup = path + '.bak-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
with sqlite3.connect('file:' + path + '?mode=rw', uri=True, timeout=30) as conn:
    with sqlite3.connect(backup) as dest:
        conn.backup(dest)
    print('Backup:', backup)
    conn.execute('BEGIN IMMEDIATE')
    columns = {row[1] for row in conn.execute('PRAGMA table_info(leads)')}
    if not columns:
        raise RuntimeError('leads table missing; check database path')
    if 'user_id' not in columns:
        conn.execute('ALTER TABLE leads ADD COLUMN user_id TEXT')
        print('Added leads.user_id')
    else:
        print('leads.user_id already exists')
print('Migration committed')
PY
```

执行成功后重新提交留资并查看 `docker logs --tail 100 csbot`；无需重启容器。
此操作只修复现有数据库，后续仍应部署包含自动迁移的新镜像。

| 现象 | 处理 |
|---|---|
| `DEEPSEEK_API_KEY` 报错 | 方式 A/B: 检查 `deploy/.env` 或 `docker run -e` 参数;方式 C: 检查 `/etc/csapp.env` 与 unit 的 `EnvironmentFile` |
| API 401/403 | Key 无效或欠费,去 platform.deepseek.com 核实 |
| 模型下载失败/超时 | 看 `docker compose logs`;国内换 `HF_ENDPOINT=https://hf-mirror.com`,海外换 `https://huggingface.co`;带宽小耐心等,卷已断点续传 |
| 回答质量下降但能跑 | 说明向量模型没加载成功,已自动退化关键词 FAQ+规格——按上行修复后 `docker compose restart` |
| 内存不足 OOM | 升级实例或加 swap;`free -h` 观察 |
| 端口占用 | `docker compose down` 后改 `.env` 的 `HOST_PORT` |
| 单条回复慢(~1-2s 是常态) | LLM 直连 API 延迟;确保安全组只开必要端口、无丢包 |
| 升级后没生效 | `docker compose up -d --build` 后 `docker compose restart` 确认日志 |

## 后续演进(可选)

- **HTTPS + 域名**:nginx 反代 `127.0.0.1:8000`,阿里云免费证书或 acme.sh;中国内地实例需 ICP 备案。
- **并发/横向扩展**:当前 SQLite + 文件会话为单机设计。流量上来后,可换 FastAPI+uvicorn 多 worker(`csapp/api.py`,需补 /health、静态页与 db 写入),再把会话/留资迁到 Redis + RDS。
- **接入渠道**:当前为网页聊天窗 /api/v1/chat;后续可对接到钉钉/企微/LINE 等,复用同一 pipeline。
