"""手動測試本機 Agent 計畫；本程式不會執行任何工具。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pc.llm.agent import AgentPlanner  # noqa: E402
from pc.llm.client import OllamaClient  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("prompt", nargs="?", default="幫我找雞胸肉食譜")
    parser.add_argument("--screen-context", default="Google Chrome")
    parser.add_argument("--model", default=None, help="預設讀取 LLM_MODEL；可填 auto")
    args = parser.parse_args()

    planner = AgentPlanner(client=OllamaClient(model=args.model))
    plan = planner.plan(args.prompt, screen_context=args.screen_context)
    print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
    print(f"來源：{plan.source}")
    print(f"模型：{plan.model or '未呼叫模型'}")
    print(f"延遲：{plan.latency_seconds * 1000:.2f} ms")
    if plan.tokens_per_second is not None:
        print(f"生成速度：{plan.tokens_per_second:.2f} tokens/s")
    if plan.error:
        print(f"攔截原因：{plan.error}", file=sys.stderr)
    return 0 if plan.error is None else 1


if __name__ == "__main__":
    raise SystemExit(main())
