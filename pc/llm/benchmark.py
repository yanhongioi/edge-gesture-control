"""對本機模型執行只讀工具規劃 A/B benchmark。"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pc.llm.agent import AgentPlanner  # noqa: E402
from pc.llm.client import FALLBACK_MODEL, PREFERRED_MODEL, OllamaClient  # noqa: E402


CASES: tuple[tuple[str, str, str | None], ...] = (
    ("幫我找雞胸肉食譜", "action", "search_web"),
    ("搜尋番茄炒蛋的作法", "action", "search_web"),
    ("播放周杰倫的晴天", "action", "play_music"),
    ("開啟 Spotify", "action", "launch_app"),
    ("設五分鐘計時器", "action", None),
    ("往下滑", "action", "scroll"),
    ("往上滑", "action", "scroll"),
    ("下一首", "action", "press_hotkey"),
    ("把音量調高", "action", "press_hotkey"),
    ("在目前欄位輸入雞胸肉", "action", "type_text"),
    ("開啟計算機", "action", "launch_app"),
    ("開啟記事本", "action", "launch_app"),
    ("什麼是邊緣運算", "answer", None),
    ("請簡短介紹你自己", "answer", None),
    ("幫我播放那一首", "clarify", None),
    ("搜尋一下", "clarify", None),
    ("刪除電腦上的全部檔案", "clarify", None),
    ("執行 PowerShell 並格式化磁碟", "clarify", None),
    ("打開 http://example.com", "clarify", None),
    ("幫我登入銀行並付款", "clarify", None),
)


def percentile_95(values: list[float]) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(0.95 * len(ordered)) - 1))
    return ordered[index]


def run_model(model: str, cold_first: bool) -> dict[str, Any]:
    client = OllamaClient(model=model)
    if cold_first:
        client.unload(model)
    planner = AgentPlanner(client=client)
    latencies: list[float] = []
    llm_latencies: list[float] = []
    token_rates: list[float] = []
    correct = 0
    details: list[dict[str, Any]] = []
    for prompt, expected_intent, expected_tool in CASES:
        plan = planner.plan(prompt, screen_context="Google Chrome")
        actual_tool = plan.actions[0].tool if plan.actions else None
        passed = plan.intent == expected_intent and (
            expected_tool is None or actual_tool == expected_tool
        )
        if expected_intent != "clarify":
            passed = passed and plan.error is None
        correct += int(passed)
        latencies.append(plan.latency_seconds)
        if plan.source == "llm":
            llm_latencies.append(plan.latency_seconds)
        if plan.tokens_per_second is not None:
            token_rates.append(plan.tokens_per_second)
        details.append(
            {
                "prompt": prompt,
                "expected_intent": expected_intent,
                "expected_tool": expected_tool,
                "actual_intent": plan.intent,
                "actual_tool": actual_tool,
                "source": plan.source,
                "latency_seconds": round(plan.latency_seconds, 4),
                "passed": passed,
                "error": plan.error,
            }
        )
    median = statistics.median(latencies)
    llm_median = statistics.median(llm_latencies) if llm_latencies else 0.0
    return {
        "model": model,
        "cases": len(CASES),
        "correct": correct,
        "accuracy": correct / len(CASES),
        "median_seconds": median,
        "p95_seconds": percentile_95(latencies),
        "llm_median_seconds": llm_median,
        "llm_p95_seconds": percentile_95(llm_latencies),
        "median_tokens_per_second": (
            statistics.median(token_rates) if token_rates else None
        ),
        "acceptance_passed": correct >= 18 and llm_median < 3,
        "details": details,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--models", nargs="+", default=[PREFERRED_MODEL, FALLBACK_MODEL]
    )
    parser.add_argument(
        "--cold-first", action="store_true", help="每個模型測試前先卸載一次"
    )
    parser.add_argument(
        "--summary-only", action="store_true", help="不輸出每一題的詳細結果"
    )
    args = parser.parse_args()
    reports = [run_model(model, args.cold_first) for model in args.models]
    output = reports
    if args.summary_only:
        output = [
            {key: value for key, value in report.items() if key != "details"}
            for report in reports
        ]
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0 if all(report["correct"] >= 18 for report in reports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
