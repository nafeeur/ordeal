from __future__ import annotations

from pathlib import Path
from xml.etree.ElementTree import Element, SubElement, ElementTree
import json

from .models import SuiteReport, Verdict


def write_json(report: SuiteReport, path: str | Path) -> None:
    Path(path).write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n")


def write_junit(report: SuiteReport, path: str | Path) -> None:
    root = Element(
        "testsuite",
        name=report.suite,
        tests=str(len(report.results)),
        failures=str(sum(r.verdict == Verdict.FAIL for r in report.results)),
        errors=str(sum(r.verdict == Verdict.ERROR for r in report.results)),
        skipped=str(sum(r.verdict == Verdict.INCOMPLETE for r in report.results)),
        time=f"{sum(r.duration_ms for r in report.results) / 1000:.6f}",
    )
    props = SubElement(root, "properties")
    SubElement(props, "property", name="pass_rate", value=f"{report.pass_rate:.6f}")
    SubElement(props, "property", name="total_tokens", value=str(report.usage.total_tokens))
    SubElement(props, "property", name="cost_usd", value=f"{report.usage.cost_usd:.8f}")
    SubElement(props, "property", name="flaky_rate", value=f"{report.flaky_rate:.6f}")
    for result in report.results:
        case = SubElement(
            root,
            "testcase",
            name=f"{result.scenario}[{result.case_id}][{result.repetition}]",
            classname=report.agent_name,
            time=f"{result.duration_ms / 1000:.6f}",
        )
        if result.verdict == Verdict.FAIL:
            node = SubElement(case, "failure", message="behavior assertions failed")
            node.text = "\n".join(c.message for c in result.checks if c.verdict == Verdict.FAIL)
        elif result.verdict == Verdict.ERROR:
            node = SubElement(case, "error", message=result.error or "agent execution error")
            node.text = result.error or ""
        elif result.verdict == Verdict.INCOMPLETE:
            SubElement(case, "skipped", message="insufficient evidence")
    ElementTree(root).write(path, encoding="utf-8", xml_declaration=True)
