import copy, math, statistics
from collections import Counter, defaultdict
from typing import Any
from .engine import stable_hash


def _shape(value: Any):
    if isinstance(value, dict): return {k:_shape(v) for k,v in sorted(value.items())}
    if isinstance(value, list): return [_shape(value[0])] if value else []
    return type(value).__name__


def _error_kind(result: Any):
    if isinstance(result, dict):
        if result.get("error"): return result.get("error")
        status=result.get("status_code", result.get("http_status"))
        try:
            return "http_error" if status is not None and int(status) >= 400 else None
        except (TypeError, ValueError):
            return None
    return None


def learn_profile(name: str, traces: list[dict[str, Any]]) -> dict[str, Any]:
    by_tool = defaultdict(list)
    for t in traces:
        tool=t.get("tool") or t.get("name")
        if tool: by_tool[tool].append(t)
    contracts={}
    for tool, rows in by_tool.items():
        lat=[float(r.get("latency_ms",0)) for r in rows if r.get("latency_ms") is not None]
        results=[r.get("response",r.get("result")) for r in rows]
        errors=Counter(_error_kind(x) or "success" for x in results)
        shapes=Counter(str(_shape(x)) for x in results)
        samples=[]
        for x in results:
            if len(samples)>=8: break
            if x not in samples: samples.append(copy.deepcopy(x))
        transitions=[]
        for r in rows:
            before,after=r.get("state_before"),r.get("state_after")
            if isinstance(before,dict) and isinstance(after,dict) and before!=after:
                transitions.append({"before_hash":stable_hash(before),"after_hash":stable_hash(after),"before":before,"after":after})
        n=len(rows)
        contracts[tool]={
            "calls":n,
            "response_shapes":dict(shapes),
            "outcomes":{k:round(v/n,4) for k,v in errors.items()},
            "latency_ms":{"p50":round(statistics.median(lat),2) if lat else None,"p95":round(sorted(lat)[max(0,math.ceil(.95*len(lat))-1)],2) if lat else None},
            "samples":samples,
            "state_transitions":transitions[:20],
            "confidence":round(min(0.995, 1-math.exp(-n/20)),3),
        }
    return {"name":name,"trace_count":len(traces),"contracts":contracts,"profile_hash":stable_hash(contracts),"method":"empirical-contract-v1"}


def simulated_from_profile(profile: dict, tool: str, occurrence: int = 1):
    contract=profile.get("contracts",{}).get(tool)
    if not contract: return None
    samples=contract.get("samples",[])
    if not samples: return None
    return copy.deepcopy(samples[(occurrence-1)%len(samples)])


def conformance(profile: dict, traces: list[dict[str,Any]]) -> dict[str,Any]:
    total=0; shape_hits=0; outcome_hits=0; unseen=[]
    for t in traces:
        tool=t.get("tool") or t.get("name"); result=t.get("response",t.get("result")); c=profile.get("contracts",{}).get(tool)
        if not c:
            unseen.append(tool); continue
        total+=1
        if str(_shape(result)) in c.get("response_shapes",{}): shape_hits+=1
        if (_error_kind(result) or "success") in c.get("outcomes",{}): outcome_hits+=1
    score=((shape_hits+outcome_hits)/(2*total)) if total else 0
    return {"evaluated":total,"shape_match":shape_hits/total if total else 0,"outcome_match":outcome_hits/total if total else 0,"fidelity_score":round(score,4),"unseen_tools":sorted(set(x for x in unseen if x))}
