from typing import Any


def first_divergence(failing: dict[str,Any], passing: dict[str,Any] | None = None):
    fe=failing.get("events",[])
    if not passing: return fe[-1] if fe else None
    pe=passing.get("events",[])
    for i in range(min(len(fe),len(pe))):
        a,b=fe[i],pe[i]
        sig_a=(a.get("tool"),a.get("args"),a.get("result"),a.get("state_after"))
        sig_b=(b.get("tool"),b.get("args"),b.get("result"),b.get("state_after"))
        if sig_a!=sig_b: return {"index":i,"failing":a,"passing":b}
    return {"index":min(len(fe),len(pe)),"failing":fe[min(len(fe),len(pe))] if len(fe)>len(pe) else None,"passing":pe[min(len(fe),len(pe))] if len(pe)>len(fe) else None}


def explain_failure(failing: dict[str,Any], passing: dict[str,Any] | None = None):
    failed_grades=[g for g in failing.get("grades",[]) if not g.get("passed")]
    events=failing.get("events",[])
    fault_events=[e for e in events if e.get("fault")]
    mutation_events=[e for e in events if e.get("state_before") and e.get("state_after") and e.get("state_before")!=e.get("state_after")]
    divergence=first_divergence(failing,passing)
    chain=[]
    if fault_events:
        f=fault_events[-1]; chain.append({"kind":"fault","event":f.get("seq"),"tool":f.get("tool"),"detail":f.get("fault")})
    if divergence: chain.append({"kind":"divergence","detail":divergence})
    if mutation_events:
        m=mutation_events[-1]; chain.append({"kind":"world_change","event":m.get("seq"),"tool":m.get("tool"),"state_after":m.get("state_after")})
    if failed_grades:
        chain.append({"kind":"violation","detail":failed_grades[0]})
    confidence=min(.99,.55+.12*len(chain))
    return {"summary": failed_grades[0].get("message","behavioral assertion failed") if failed_grades else "trial failed", "first_divergence":divergence,"cause_chain":chain,"failed_assertions":failed_grades,"confidence":round(confidence,2)}
