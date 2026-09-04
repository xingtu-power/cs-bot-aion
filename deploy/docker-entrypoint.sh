#!/bin/sh
# cs-bot-aion 容器入口:先确保 e5 向量模型可用(首次启动下载 ~2.3GB),再启动服务。
set -e

echo "[entrypoint] cs-bot-aion 启动,检查向量模型 ..."
python tools/preload_model.py 3
echo "[entrypoint] 模型就绪,启动服务: $*"
exec "$@"
