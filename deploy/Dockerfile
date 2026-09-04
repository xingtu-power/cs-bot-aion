# cs-bot-aion 生产镜像(Python 3.11 slim + numpy + fastembed)
# 构建: docker buildx build --platform linux/amd64 -f deploy/Dockerfile -t cs-bot-aion .
# 说明: e5 模型(~2.3GB)默认不预置,由 entrypoint 首次启动下载并挂卷持久化;
#       如需构建期预置(云上首次零下载,镜像 ~3.6GB):
#         docker build --build-arg BAKE_MODEL=1 …
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HF_ENDPOINT=https://hf-mirror.com \
    HF_HOME=/app/tools/hf_cache \
    TZ=Asia/Shanghai

# onnxruntime 需要 libgomp1;ca-certificates 供模型/API 下载
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# pip 源可用构建参数覆盖(国内 ECS 建议阿里云镜像)
ARG PIP_INDEX_URL=https://pypi.org/simple
COPY requirements.txt ./
RUN pip install --no-cache-dir --index-url ${PIP_INDEX_URL} -r requirements.txt

# 先创建运行用户和缓存目录。模型直接由 app 用户下载，避免后续对 2.3GB
# 模型执行 chown -R 而产生额外的镜像层 copy-up。
RUN useradd --create-home --uid 1000 app \
    && chown app:app /app \
    && install -d -o app -g app /app/data/sessions /app/tools/emb_cache /app/tools/hf_cache

USER app

# 可选:构建期预下载 e5 模型(镜像 +2.3GB)。放在 COPY 项目代码之前，
# 这样日常代码更新不会使模型层失效并重复下载。
ARG BAKE_MODEL=0
RUN if [ "$BAKE_MODEL" = "1" ]; then \
         python -c "from fastembed import TextEmbedding; TextEmbedding(model_name='intfloat/multilingual-e5-large', cache_dir='/app/tools/emb_cache')"; \
       fi

# 代码 + 知识库 + 静态页(含内置 npz 向量索引)
COPY --chown=app:app . .

EXPOSE 8000

ENTRYPOINT ["/app/deploy/docker-entrypoint.sh"]
CMD ["python", "-m", "csapp.server", "--host", "0.0.0.0", "--port", "8000"]
