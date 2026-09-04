"""预下载/预热 e5 向量模型(部署用)。

首次启动需下载 intfloat/multilingual-e5-large(~2.3GB)到 tools/emb_cache,
之后启动秒过。仅触发下载与加载,不做业务;真正的内存加载由
server 启动时 kb.warmup() 完成。

用法: python tools/preload_model.py [重试次数=3]
"""
import os
import sys
import time

# 以脚本方式运行时 sys.path[0]=tools/,需把项目根加进来才能 import csapp
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def main(retries: int = 3) -> int:
    for attempt in range(1, retries + 1):
        try:
            print(f"[preload] 第 {attempt}/{retries} 次尝试加载 e5 模型(首次下载约 2.3GB)...", flush=True)
            from csapp import kb  # noqa: 触发 tools/emb_cache 下载
            kb._get_model()
            print("[preload] e5 模型已就绪。", flush=True)
            return 0
        except Exception as e:  # noqa: BLE001 下载/加载失败可重试
            print(f"[preload] 第 {attempt} 次失败: {type(e).__name__}: {e}", flush=True)
            if attempt < retries:
                time.sleep(5)
    return 1


if __name__ == "__main__":
    sys.exit(main(int(sys.argv[1]) if len(sys.argv) > 1 else 3))
