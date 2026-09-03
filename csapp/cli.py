"""CLI 交互式运行骨架:python -m csapp.cli [--market AU|THA]

输入一条消息,打印会话响应;'quit' 退出。同一会话持续(断点续聊演示)。
"""
import sys, argparse
from . import pipeline


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="AU", choices=["AU", "THA"])
    ap.add_argument("--session", default=None)
    args = ap.parse_args()

    session_id = args.session
    print("csapp 客服骨架 | 市场默认:", args.market, "| 输入消息,'quit' 退出")
    print("=" * 60)
    while True:
        try:
            m = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if m.lower() in ("quit", "exit", "q"):
            break
        r = pipeline.chat(session_id=session_id, message=m,
                          explicit_market=args.market,
                          lang_hint="en" if args.market == "AU" else "th")
        session_id = r["sessionId"]
        print(f"\n[{r['market']}/{r['language']} intent={r['intent']} "
              f"step={r['state']['stepIndex']} target={r['targetReached']} esc={r['escalate']}]")
        print("BOT:", r["reply"])
        print("-" * 60)


if __name__ == "__main__":
    main()
