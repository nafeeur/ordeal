from __future__ import annotations

from dataclasses import asdict, dataclass, field
from html import escape
import json
from pathlib import Path
from typing import Any, Mapping

JSON = Any


@dataclass(frozen=True, slots=True)
class BehaviorChange:
    result_id: str
    kind: str
    before: JSON
    after: JSON


@dataclass(slots=True)
class BehaviorDiff:
    before_agent: str
    after_agent: str
    changes: list[BehaviorChange] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.changes)

    def to_dict(self) -> dict[str, JSON]:
        return {
            "schema": "ordeal.behavior-diff/v1",
            "before_agent": self.before_agent,
            "after_agent": self.after_agent,
            "changed": self.changed,
            "changes": [asdict(c) for c in self.changes],
        }


def _key(row: Mapping[str, JSON]) -> str:
    return f"{row.get('scenario')}:{row.get('case_id', 'default')}:{int(row.get('repetition', 0))}"


def diff_reports(before: Mapping[str, JSON], after: Mapping[str, JSON]) -> BehaviorDiff:
    left = {_key(row): row for row in before.get("results", [])}
    right = {_key(row): row for row in after.get("results", [])}
    out = BehaviorDiff(
        str(before.get("agent", {}).get("version", "unknown")),
        str(after.get("agent", {}).get("version", "unknown")),
    )
    for key in sorted(set(left) | set(right)):
        a, b = left.get(key), right.get(key)
        if a is None:
            out.changes.append(BehaviorChange(key, "added", None, b))
            continue
        if b is None:
            out.changes.append(BehaviorChange(key, "removed", a, None))
            continue
        fields = [
            ("verdict", a.get("verdict"), b.get("verdict")),
            ("structure", a.get("trajectory", {}).get("structural_fingerprint"), b.get("trajectory", {}).get("structural_fingerprint")),
            ("content", a.get("trajectory", {}).get("content_fingerprint"), b.get("trajectory", {}).get("content_fingerprint")),
            ("tokens", (a.get("usage") or {}).get("total_tokens", 0), (b.get("usage") or {}).get("total_tokens", 0)),
            ("cost", (a.get("usage") or {}).get("cost_usd", 0.0), (b.get("usage") or {}).get("cost_usd", 0.0)),
        ]
        for kind, av, bv in fields:
            if av != bv:
                out.changes.append(BehaviorChange(key, kind, av, bv))
    return out


def read_report(path: str | Path) -> dict[str, JSON]:
    return json.loads(Path(path).read_text())


def render_diff_text(diff: BehaviorDiff) -> str:
    lines = [f"behavior diff {diff.before_agent} -> {diff.after_agent}: {len(diff.changes)} changes"]
    for change in diff.changes:
        lines.append(f"  {change.result_id} {change.kind}: {change.before!r} -> {change.after!r}")
    return "\n".join(lines)


def render_diff_html(diff: BehaviorDiff) -> str:
    rows = "\n".join(
        "<tr>"
        f"<td>{escape(c.result_id)}</td><td>{escape(c.kind)}</td>"
        f"<td><pre>{escape(json.dumps(c.before, indent=2, default=str))}</pre></td>"
        f"<td><pre>{escape(json.dumps(c.after, indent=2, default=str))}</pre></td>"
        "</tr>"
        for c in diff.changes
    ) or '<tr><td colspan="4">No behavior changes</td></tr>'
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Ordeal behavior diff</title>
<style>
body{{font-family:ui-sans-serif,system-ui;margin:2rem;background:#0b1020;color:#e8ecf3}}
h1{{font-size:1.5rem}} .meta{{color:#aab4c5;margin-bottom:1rem}}
table{{border-collapse:collapse;width:100%}} th,td{{border:1px solid #344057;padding:.65rem;text-align:left;vertical-align:top}}
th{{background:#172036}} pre{{white-space:pre-wrap;max-width:42rem;margin:0}} .badge{{padding:.2rem .5rem;border:1px solid #52617b;border-radius:999px}}
</style></head><body>
<h1>Ordeal behavior diff</h1><div class="meta"><span class="badge">{escape(diff.before_agent)}</span> &rarr; <span class="badge">{escape(diff.after_agent)}</span> &middot; {len(diff.changes)} changes</div>
<table><thead><tr><th>Result</th><th>Change</th><th>Before</th><th>After</th></tr></thead><tbody>{rows}</tbody></table>
</body></html>"""
