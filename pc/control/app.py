"""文字 → LLM 計畫 → 選配實際執行的命令列入口。"""

from __future__ import annotations

import argparse
import json
import sys

from pc.llm import AgentPlanner

from .browser import BrowserControlError
from .executor import ControlExecutionError, ControlExecutor
from .media import MediaControlError
from .youtube import YouTubeError
from .timer import TimerError
from .windows_input import WindowsInputError


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("text", help="使用者輸入文字")
    parser.add_argument("--screen-context", default="未知")
    parser.add_argument(
        "--execute",
        action="store_true",
        help="明確確認並實際執行；省略時只預覽計畫",
    )
    parser.add_argument(
        "--search-results-only",
        action="store_true",
        help="搜尋時停在結果頁，不自動前往第一個結果",
    )
    args = parser.parse_args()

    plan = AgentPlanner().plan(args.text, screen_context=args.screen_context)
    print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
    print(f"規劃來源：{plan.source}；延遲：{plan.latency_seconds:.3f} 秒")
    if plan.error:
        print(f"計畫被攔截：{plan.error}", file=sys.stderr)
        return 1
    if not args.execute:
        print("預覽模式：沒有執行任何動作。加上 --execute 才會執行。")
        return 0

    try:
        search_override = False if args.search_results_only else None
        results = ControlExecutor(
            open_first_search_result=search_override
        ).execute(plan, confirmed=True)
    except (
        BrowserControlError,
        ControlExecutionError,
        MediaControlError,
        TimerError,
        WindowsInputError,
        YouTubeError,
    ) as exc:
        print(f"執行失敗：{exc}", file=sys.stderr)
        return 1
    for result in results:
        print(result.message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
