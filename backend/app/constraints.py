from __future__ import annotations
from collections import defaultdict
from typing import Any
from .engine import evaluate_assertions, stable_hash



def _resolve_refs(value: Any, scenario: dict[str, Any]):
    if isinstance(value,str) and value.startswith("$vars."):
        cur=scenario.get("variables",{})
        for part in value[6:].split('.'):
            if not isinstance(cur,dict): return None
            cur=cur.get(part)
        return cur
    if isinstance(value,str) and value.startswith("$scenario."):
        cur=scenario
        for part in value[10:].split('.'):
            if not isinstance(cur,dict): return None
            cur=cur.get(part)
        return cur
    if isinstance(value,list): return [_resolve_refs(v,scenario) for v in value]
    if isinstance(value,dict): return {k:_resolve_refs(v,scenario) for k,v in value.items()}
    return value

def constraint_applies(constraint: dict[str, Any], scenario: dict[str, Any], suite_name: str | None = None) -> bool:
    binding = constraint.get("bindings", {}) or {}
    scenarios = set(binding.get("scenarios", []) or [])
    tags = set(binding.get("tags", []) or [])
    suites = set(binding.get("suites", []) or [])
    name = scenario.get("name")
    scenario_tags = set(scenario.get("tags", []) or [])
    if not scenarios and not tags and not suites:
        return True
    return bool((name and name in scenarios) or (tags & scenario_tags) or (suite_name and suite_name in suites))


def evaluate_constraints(state: dict[str, Any], events: list[dict[str, Any]], ledger: dict[str, Any], scenario: dict[str, Any], constraints: list[dict[str, Any]], suite_name: str | None = None):
    results = []
    for c in constraints:
        if not c.get("enabled", True) or not constraint_applies(c, scenario, suite_name):
            continue
        assertion = _resolve_refs(c.get("assertion", {}), scenario)
        grade = evaluate_assertions(state, events, [assertion], ledger)[0]
        results.append({
            "constraint": c.get("name"),
            "version": c.get("version", "v1"),
            "severity": c.get("severity", "major"),
            "engine": "constraint",
            "passed": grade["passed"],
            "actual": grade.get("actual"),
            "assertion": assertion,
            "description": c.get("description", ""),
        })
    return results


def stability_report(trials: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for t in trials:
        grouped[t.get("scenario") or "unknown"].append(t)
    scenarios=[]
    for name, rows in sorted(grouped.items()):
        passes=sum(bool(r.get("passed")) for r in rows)
        total=len(rows)
        hashes=sorted({r.get("final_hash") for r in rows})
        if passes == total:
            status="stable_pass"
        elif passes == 0:
            status="stable_fail"
        else:
            status="in_variance"
        scenarios.append({
            "scenario": name,
            "repeats": total,
            "passes": passes,
            "fails": total-passes,
            "pass_rate": passes/total if total else 0,
            "status": status,
            "distinct_final_states": len(hashes),
            "state_fingerprints": hashes[:12],
        })
    stable=[s for s in scenarios if s["status"] != "in_variance"]
    return {
        "scenarios": scenarios,
        "stable_count": len(stable),
        "variance_count": sum(s["status"] == "in_variance" for s in scenarios),
        "stable_pass_count": sum(s["status"] == "stable_pass" for s in scenarios),
        "stable_fail_count": sum(s["status"] == "stable_fail" for s in scenarios),
        "stability_rate": len(stable)/len(scenarios) if scenarios else 1.0,
    }


def constraint_summary(trials: list[dict[str, Any]]) -> dict[str, Any]:
    by_name: dict[str, dict[str, Any]] = {}
    for t in trials:
        for g in t.get("constraint_grades", []):
            name=g.get("constraint") or "unknown"
            row=by_name.setdefault(name, {"constraint":name,"bindings_executed":0,"upheld":0,"violated":0,"violations":[],"severity":g.get("severity","major"),"engine":"constraint"})
            row["bindings_executed"] += 1
            if g.get("passed"):
                row["upheld"] += 1
            else:
                row["violated"] += 1
                if len(row["violations"]) < 25:
                    row["violations"].append({"scenario":t.get("scenario"),"seed":t.get("seed"),"actual":g.get("actual"),"final_hash":t.get("final_hash")})
    return {"tracked_constraints":len(by_name),"violations":sum(v["violated"] for v in by_name.values()),"constraints":sorted(by_name.values(),key=lambda x:(-x["violated"],x["constraint"]))}


def regression_attribution(baseline_trials: list[dict[str, Any]], candidate_trials: list[dict[str, Any]]) -> dict[str, Any]:
    def grouped(rows):
        out=defaultdict(list)
        for t in rows: out[t.get("scenario")].append(t)
        return out
    a,b=grouped(baseline_trials),grouped(candidate_trials)
    rows=[]
    for scenario in sorted(set(a)|set(b),key=str):
        br=a.get(scenario,[]); cr=b.get(scenario,[])
        b_pass=sum(bool(x.get("passed")) for x in br); c_pass=sum(bool(x.get("passed")) for x in cr)
        b_rate=b_pass/len(br) if br else None; c_rate=c_pass/len(cr) if cr else None
        b_stable=(b_pass in {0,len(br)}) if br else False; c_stable=(c_pass in {0,len(cr)}) if cr else False
        verdict="unchanged"
        if not br or not cr: verdict="missing"
        elif b_stable and c_stable and b_rate==1.0 and c_rate==0.0: verdict="new_regression"
        elif b_stable and c_stable and b_rate==0.0 and c_rate==1.0: verdict="fixed"
        elif b_rate != c_rate: verdict="distribution_shift"
        world_changed=bool(br and cr and {x.get('final_hash') for x in br}!={x.get('final_hash') for x in cr})
        base_constraints={g.get('constraint') for t in br for g in t.get('constraint_grades',[]) if not g.get('passed')}
        cand_constraints={g.get('constraint') for t in cr for g in t.get('constraint_grades',[]) if not g.get('passed')}
        rows.append({"scenario":scenario,"baseline_pass_rate":b_rate,"candidate_pass_rate":c_rate,"baseline_stable":b_stable,"candidate_stable":c_stable,"world_changed":world_changed,"verdict":verdict,"new_constraint_violations":sorted(cand_constraints-base_constraints),"resolved_constraint_violations":sorted(base_constraints-cand_constraints)})
    return {"scenarios":rows,"new_regressions":sum(r['verdict']=='new_regression' for r in rows),"fixed":sum(r['verdict']=='fixed' for r in rows),"distribution_shifts":sum(r['verdict']=='distribution_shift' for r in rows),"blocking":any(r['verdict']=='new_regression' or r['new_constraint_violations'] for r in rows),"fingerprint":stable_hash(rows)}
