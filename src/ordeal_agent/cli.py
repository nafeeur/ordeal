from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

import typer

from .diffing import diff_reports, read_report, render_diff_html, render_diff_text
from .discovery import load_experiment_target, load_target
from .experiments import run_experiment
from .models import Verdict
from .regression import compare_to_baseline, load_baseline, save_baseline
from .reporting import write_json, write_junit
from .runner import Runner

def _usage_text(usage):
    tokens = str(usage.total_tokens) if "total_tokens" in usage.measured else "unknown"
    cost = f"${usage.cost_usd:.6f}" if "cost_usd" in usage.measured else "unknown"
    return f"tokens={tokens} cost={cost}"


app = typer.Typer(no_args_is_help=True, help="Local-first AI-agent behavior simulation and regression testing.")


@app.command("run")
def run_cmd(
    target: str = typer.Argument(..., help="Python file[:suite_name] exporting agent and suite"),
    baseline: Optional[Path] = typer.Option(None, "--baseline", help="Compare against a saved baseline"),
    update_baseline: Optional[Path] = typer.Option(None, "--update-baseline", help="Write this run as the baseline"),
    json_out: Optional[Path] = typer.Option(None, "--json", help="Write full JSON report"),
    junit_out: Optional[Path] = typer.Option(None, "--junit", help="Write JUnit XML report"),
    min_pass_rate: float = typer.Option(1.0, "--min-pass-rate", min=0.0, max=1.0),
    max_pass_rate_drop: float = typer.Option(0.0, "--max-pass-rate-drop", min=0.0, max=1.0),
    max_latency_ratio: float = typer.Option(2.0, "--max-latency-ratio", min=1.0),
    max_cost_ratio: float = typer.Option(2.0, "--max-cost-ratio", min=1.0),
    max_token_ratio: float = typer.Option(2.0, "--max-token-ratio", min=1.0),
    max_flaky_rate: float = typer.Option(1.0, "--max-flaky-rate", min=0.0, max=1.0),
) -> None:
    agent, suite = load_target(target)
    report = asyncio.run(Runner().run_suite(agent, suite))
    counts = report.counts
    typer.echo(
        f"{suite.name}: pass={counts['pass']} fail={counts['fail']} incomplete={counts['incomplete']} "
        f"error={counts['error']} pass_rate={report.pass_rate:.3f} flaky_rate={report.flaky_rate:.3f} "
        f"{_usage_text(report.usage)} p50={report.latency['p50_ms']:.1f}ms p95={report.latency['p95_ms']:.1f}ms"
    )
    for result in report.results:
        marker = {Verdict.PASS: "PASS", Verdict.FAIL: "FAIL", Verdict.INCOMPLETE: "INCOMPLETE", Verdict.ERROR: "ERROR"}[result.verdict]
        typer.echo(
            f"  {marker:10} {result.scenario}[{result.case_id}][{result.repetition}] "
            f"{result.duration_ms:.1f}ms {_usage_text(result.usage)}"
        )
        for check in result.checks:
            if check.verdict != Verdict.PASS:
                typer.echo(f"    - {check.name}: {check.verdict.value}: {check.message}")

    if json_out:
        write_json(report, json_out)
    if junit_out:
        write_junit(report, junit_out)
    if update_baseline:
        save_baseline(report, update_baseline)
        typer.echo(f"baseline written: {update_baseline}")

    regression_failed = False
    if baseline:
        regression = compare_to_baseline(
            report,
            load_baseline(baseline),
            max_pass_rate_drop=max_pass_rate_drop,
            max_latency_ratio=max_latency_ratio,
            max_cost_ratio=max_cost_ratio,
            max_token_ratio=max_token_ratio,
        )
        typer.echo(
            f"regression: {'PASS' if regression.ok else 'FAIL'} "
            f"({len(regression.regressions)} regressions, {len(regression.changes)} behavior changes)"
        )
        for item in regression.regressions:
            typer.echo(f"  REGRESSION {item.scenario}[{item.case_id}]: {item.kind}: {item.message}")
        regression_failed = not regression.ok

    if report.pass_rate < min_pass_rate or report.flaky_rate > max_flaky_rate or counts[Verdict.ERROR.value] or regression_failed:
        raise typer.Exit(code=1)


@app.command("diff")
def diff_cmd(
    before: Path = typer.Argument(..., exists=True),
    after: Path = typer.Argument(..., exists=True),
    html: Optional[Path] = typer.Option(None, "--html", help="Write a self-contained HTML behavior diff"),
    json_out: Optional[Path] = typer.Option(None, "--json", help="Write machine-readable diff JSON"),
) -> None:
    diff = diff_reports(read_report(before), read_report(after))
    typer.echo(render_diff_text(diff))
    if html:
        html.write_text(render_diff_html(diff))
        typer.echo(f"HTML diff written: {html}")
    if json_out:
        json_out.write_text(json.dumps(diff.to_dict(), indent=2, sort_keys=True) + "\n")


@app.command("experiment")
def experiment_cmd(
    target: str = typer.Argument(..., help="Python file[:suite_name] exporting variants and suite"),
    baseline_variant: Optional[str] = typer.Option(None, "--baseline-variant"),
    json_out: Optional[Path] = typer.Option(None, "--json"),
) -> None:
    variants, suite = load_experiment_target(target)
    report = asyncio.run(run_experiment(suite, variants, baseline=baseline_variant))
    typer.echo("variant ranking (pass_rate desc, then cost, then latency):")
    for name, pass_rate, cost, latency in report.ranking:
        cost_text = f"${cost:.6f}" if cost is not None else "unknown"
        typer.echo(f"  {name:24} pass_rate={pass_rate:.3f} cost={cost_text} p50={latency:.1f}ms")
    if json_out:
        json_out.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n")


@app.command("init")
def init_cmd(directory: Path = typer.Argument(Path("ordeal_tests"))) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / "test_agent.py"
    if target.exists():
        raise typer.BadParameter(f"{target} already exists")
    target.write_text(_SCAFFOLD)
    typer.echo(f"created {target}")
    typer.echo(f"run: ordeal-behavior run {target}")


@app.command("doctor")
def doctor_cmd() -> None:
    import importlib.util

    frameworks = {
        "Anthropic": "anthropic",
        "OpenAI Agents": "agents",
        "LangChain/LangGraph": "langchain_core",
        "PydanticAI": "pydantic_ai",
        "Google ADK": "google.adk",
        "CrewAI": "crewai",
        "AutoGen": "autogen_agentchat",
        "LlamaIndex": "llama_index",
        "Smolagents": "smolagents",
        "Strands": "strands",
        "MCP v2": "mcp",
    }
    for label, module in frameworks.items():
        try:
            present = importlib.util.find_spec(module) is not None
        except (ImportError, ModuleNotFoundError):
            present = False
        typer.echo(f"{label:20} {'installed' if present else '-'}")


_SCAFFOLD = '''from ordeal_agent import CallableAgent, OutputContains, Scenario, Suite, World\n\n\nasync def run_agent(request):\n    return "hello from my agent"\n\n\nagent = CallableAgent(run_agent, name="my-agent", version="dev")\nworld = World("empty")\nscenario = Scenario(\n    name="smoke",\n    instruction="Say hello to {name}",\n    world=world,\n    assertions=[OutputContains("hello")],\n).parameterize([\n    {"id": "alice", "name": "Alice"},\n    {"id": "bob", "name": "Bob"},\n])\nsuite = Suite.of("default", scenario)\n'''

if __name__ == "__main__":
    app()
