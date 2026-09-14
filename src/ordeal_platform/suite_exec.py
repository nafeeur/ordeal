"""Child process entrypoint. Not exposed through the API and never loads server credentials."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stdout
import importlib.util
import inspect
import json
import math
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", required=True)
    choices = parser.add_mutually_exclusive_group(required=True)
    choices.add_argument("--suite")
    choices.add_argument("--evaluator")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    path = (root / (args.suite or args.evaluator)).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        parser.error("Execution path must be inside workspace")
    sys.path.insert(0, str(root))
    with redirect_stdout(sys.stderr):
        if args.suite:
            from ordeal_agent.discovery import load_target
            from ordeal_agent import Runner
            agent, suite = load_target(str(path))
            report = Runner().run_suite_sync(agent, suite).to_dict()
        else:
            request = json.loads(sys.stdin.read(2 * 1024 * 1024))
            spec = importlib.util.spec_from_file_location("ordeal_user_evaluator", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            fn = getattr(module, request.get("function") or "evaluate")
            value = fn(request["trace"], request.get("options", {}))
            if inspect.isawaitable(value):
                value = asyncio.run(value)
            if isinstance(value, bool):
                report = {"verdict": "pass" if value else "fail", "score": float(value), "message": "Custom Python evaluator"}
            elif isinstance(value, dict) and value.get("verdict") in {"pass", "fail", "error", "incomplete"}:
                report = value
            else:
                raise ValueError("Custom evaluator must return a bool or a verdict object")
            if report.get("score") is not None and (not isinstance(report["score"], (int, float)) or not math.isfinite(report["score"])):
                raise ValueError("Evaluator score must be finite")
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()
