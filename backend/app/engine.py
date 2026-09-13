import asyncio, copy, hashlib, json, random, time, uuid
from dataclasses import dataclass, field
from typing import Any
import httpx
from .core.contracts import normalize_action
from .settings import settings


def get_path(obj: dict, path: str):
    cur = obj
    for part in path.split('.') if path else []:
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


def set_path(obj: dict, path: str, value: Any):
    parts = path.split('.')
    cur = obj
    for part in parts[:-1]:
        cur = cur.setdefault(part, {})
    cur[parts[-1]] = value


def delete_path(obj: dict, path: str):
    parts = path.split('.')
    cur = obj
    for part in parts[:-1]:
        cur = cur.get(part, {})
    if isinstance(cur, dict):
        cur.pop(parts[-1], None)


def stable_hash(value: Any) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    return hashlib.sha256(raw).hexdigest()[:16]



def validate_schema(value: Any, schema: dict[str, Any] | None) -> tuple[bool, str | None]:
    """Small dependency-free JSON-schema subset for tool contracts."""
    if not schema:
        return True, None
    t=schema.get("type")
    checks={"object":dict,"array":list,"string":str,"number":(int,float),"integer":int,"boolean":bool}
    if t in checks and not isinstance(value, checks[t]):
        return False, f"expected {t}"
    if isinstance(value,dict):
        for key in schema.get("required",[]):
            if key not in value: return False, f"missing required field {key}"
        props=schema.get("properties",{})
        for key,sub in props.items():
            if key in value:
                ok,err=validate_schema(value[key],sub)
                if not ok: return False, f"{key}: {err}"
    if isinstance(value,list) and schema.get("items"):
        for i,item in enumerate(value):
            ok,err=validate_schema(item,schema["items"])
            if not ok: return False, f"[{i}]: {err}"
    if "enum" in schema and value not in schema["enum"]:
        return False, "value not in enum"
    return True, None

@dataclass
class LedgerEntry:
    seq: int
    op: str
    path: str
    before: Any
    after: Any
    source: str
    ts: float


class WorldLedger:
    """Canonical world state. Tools may propose effects; only the ledger commits them."""
    def __init__(self, initial_state: dict[str, Any]):
        self.initial_state = copy.deepcopy(initial_state)
        self.state = copy.deepcopy(initial_state)
        self.entries: list[LedgerEntry] = []
        self.snapshots: list[dict[str, Any]] = [{"seq": 0, "hash": stable_hash(self.state), "state": copy.deepcopy(self.state)}]

    def read(self, path: str | None = None):
        return copy.deepcopy(self.state if not path else get_path(self.state, path))

    def commit(self, op: str, path: str, value: Any = None, source: str = "tool"):
        before = copy.deepcopy(get_path(self.state, path))
        if op in {"set", "update"}:
            set_path(self.state, path, copy.deepcopy(value))
        elif op == "add":
            current = get_path(self.state, path)
            if current is None:
                set_path(self.state, path, copy.deepcopy(value))
            elif isinstance(current, list):
                current.append(copy.deepcopy(value))
            elif isinstance(current, dict) and isinstance(value, dict):
                current.update(copy.deepcopy(value))
            else:
                raise ValueError(f"cannot add to {path}")
        elif op == "delete":
            delete_path(self.state, path)
        elif op == "increment":
            set_path(self.state, path, (before or 0) + value)
        else:
            raise ValueError(f"unsupported ledger op: {op}")
        after = copy.deepcopy(get_path(self.state, path))
        entry = LedgerEntry(len(self.entries) + 1, op, path, before, after, source, time.time())
        self.entries.append(entry)
        self.snapshots.append({"seq": entry.seq, "hash": stable_hash(self.state), "state": copy.deepcopy(self.state)})
        return entry

    def export(self):
        return {
            "initial_state": copy.deepcopy(self.initial_state),
            "final_state": copy.deepcopy(self.state),
            "initial_hash": stable_hash(self.initial_state),
            "final_hash": stable_hash(self.state),
            "entries": [e.__dict__ for e in self.entries],
            "snapshots": copy.deepcopy(self.snapshots),
        }


@dataclass
class TrialRuntime:
    id: str
    ledger: WorldLedger
    events: list[dict[str, Any]] = field(default_factory=list)
    call_counts: dict[str, int] = field(default_factory=dict)
    tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    faults: list[dict[str, Any]] = field(default_factory=list)
    seed: int = 1
    scenario: dict[str, Any] = field(default_factory=dict)

    @property
    def state(self):
        return self.ledger.state


class SimulationEngine:
    def __init__(self):
        self.trials: dict[str, TrialRuntime] = {}

    def start_trial(self, state, tools=None, faults=None, seed=1, scenario=None):
        t = TrialRuntime(
            id=str(uuid.uuid4()),
            ledger=WorldLedger(state),
            tools={x["name"]: x for x in (tools or [])},
            faults=faults or [],
            seed=seed,
            scenario=copy.deepcopy(scenario or {}),
        )
        self.trials[t.id] = t
        t.events.append({"seq": 0, "type": "trial.started", "state_hash": stable_hash(t.state), "seed": seed})
        return t

    async def tool_call(self, trial, tool, args, faults, rng):
        name = tool.get("name")
        trial.call_counts[name] = trial.call_counts.get(name, 0) + 1
        occurrence = trial.call_counts[name]
        before_hash = stable_hash(trial.state)
        fault = self._match_fault(name, occurrence, faults, rng)
        fault_phase = (fault or {}).get("inject", {}).get("phase", "before_execution")
        if fault and fault_phase != "after_commit":
            result = await self._apply_fault(fault)
            trial.events.append(normalize_action({"seq": len(trial.events), "type":"tool.call", "tool":name, "adapter":"simulated", "runtime":"local", "occurrence":occurrence, "args":copy.deepcopy(args), "result":result, "fault":fault, "state_before":before_hash, "state_after":stable_hash(trial.state)}, len(trial.events)))
            return result

        mode = tool.get("mode", "simulated")
        if mode == "passthrough" and tool.get("endpoint"):
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(tool["endpoint"], json=args)
                r.raise_for_status()
                result = r.json()
        elif tool.get("simulation",{}).get("op") == "llm":
            result = await self._simulate_llm(trial, tool, args, occurrence)
        else:
            result = self._simulate_tool(trial, tool, args)

        ok, schema_error = validate_schema(result, tool.get("output_schema"))
        if not ok:
            trial.events.append({"seq": len(trial.events), "type":"tool.schema_violation", "tool":name, "occurrence":occurrence, "error":schema_error, "result":copy.deepcopy(result)})
            fallback = tool.get("simulation",{}).get("schema_fallback")
            if fallback is not None:
                result = copy.deepcopy(fallback)
                ok, schema_error = validate_schema(result, tool.get("output_schema"))
            if not ok:
                result = {"error":"simulation_schema_violation","detail":schema_error,"tool":name}
        self._apply_declared_mutations(trial, tool, args, result)
        if fault and fault_phase == "after_commit":
            committed_result = copy.deepcopy(result)
            visible_result = await self._apply_fault(fault)
            trial.events.append(normalize_action({"seq": len(trial.events), "type":"tool.call", "tool":name, "adapter":mode, "runtime":"local", "occurrence":occurrence, "args":copy.deepcopy(args), "result":copy.deepcopy(visible_result), "committed_result":committed_result, "fault":fault, "fault_phase":"after_commit", "mode":mode, "state_before":before_hash, "state_after":stable_hash(trial.state)}, len(trial.events)))
            return visible_result
        trial.events.append(normalize_action({"seq": len(trial.events), "type":"tool.call", "tool":name, "adapter":mode, "runtime":"local", "occurrence":occurrence, "args":copy.deepcopy(args), "result":copy.deepcopy(result), "mode":mode, "state_before":before_hash, "state_after":stable_hash(trial.state)}, len(trial.events)))
        return result

    async def _simulate_llm(self, trial, tool, args, occurrence):
        sim=tool.get("simulation",{})
        endpoint=(sim.get("endpoint") or settings.simulator_endpoint or "").rstrip("/")
        model=sim.get("model") or settings.simulator_model
        if not endpoint or not model:
            return {"error":"simulator_not_configured","detail":"set ORDEAL_SIMULATOR_ENDPOINT and ORDEAL_SIMULATOR_MODEL"}
        url=endpoint if endpoint.endswith("/chat/completions") else endpoint+"/chat/completions"
        headers={"Content-Type":"application/json"}
        key=sim.get("api_key") or settings.simulator_api_key
        if key: headers["Authorization"]=f"Bearer {key}"
        schema=tool.get("output_schema") or {"type":"object"}
        prompt={"role":"user","content":json.dumps({"task":"Return only JSON for the simulated tool response.","tool":tool.get("name"),"description":tool.get("description",""),"arguments":args,"world_state":trial.state,"scenario":trial.scenario.get("instruction",""),"output_schema":schema},default=str)}
        last=None
        async with httpx.AsyncClient(timeout=float(sim.get("timeout_seconds",60))) as client:
            for attempt in range(int(sim.get("schema_retries",3))+1):
                r=await client.post(url,headers=headers,json={"model":model,"messages":[prompt],"temperature":float(sim.get("temperature",0)),"seed":trial.seed+occurrence+attempt,"response_format":{"type":"json_object"}})
                r.raise_for_status();content=r.json()["choices"][0]["message"].get("content") or "{}"
                try:last=json.loads(content)
                except Exception:last={"error":"invalid_json","raw":content[:500]}
                ok,_=validate_schema(last,schema)
                if ok:return last
                prompt={"role":"user","content":json.dumps({"task":"Previous JSON violated the schema. Regenerate only valid JSON.","previous":last,"output_schema":schema},default=str)}
        return last or {"error":"simulation_generation_failed"}

    def _resolve_path_template(self, template, args):
        try:
            return template.format(**args)
        except Exception:
            return template

    def _simulate_tool(self, trial, tool, args):
        state = trial.state
        sim = tool.get("simulation", {})
        op = sim.get("op", "lookup")
        path_template = sim.get("path")
        if op == "lookup":
            if path_template:
                value = get_path(state, self._resolve_path_template(path_template, args))
                return copy.deepcopy(value) if value is not None else {"error":"not_found"}
            collection = sim.get("collection") or tool.get("name", "").replace("get_", "") + "s"
            key = str(args.get(sim.get("key_arg", "id"), ""))
            return copy.deepcopy(state.get(collection, {}).get(key, {"error":"not_found"}))
        if op == "list":
            collection = sim.get("collection")
            values = list(state.get(collection, {}).values())
            filters = sim.get("filters", [])
            for f in filters:
                arg = args.get(f.get("arg")); field = f.get("field")
                if arg is not None:
                    values = [v for v in values if isinstance(v, dict) and v.get(field) == arg]
            return {"items": copy.deepcopy(values)}
        if op == "create":
            if path_template:
                path = self._resolve_path_template(path_template, args)
                value = copy.deepcopy(args)
                trial.ledger.commit("add", path, value, source=f"tool:{tool.get('name')}")
                return copy.deepcopy(value)
            collection = sim.get("collection")
            prefix = sim.get("id_prefix", "item")
            new_id = f"{prefix}_{len(state.get(collection, {}))+1:04d}"
            value = {"id":new_id, **copy.deepcopy(args)}
            trial.ledger.commit("set", f"{collection}.{new_id}", value, source=f"tool:{tool.get('name')}")
            return copy.deepcopy(value)
        if op == "update":
            if path_template:
                path = self._resolve_path_template(path_template, args)
                value_from = sim.get("value_from")
                value = args.get(value_from) if value_from else args
                trial.ledger.commit("set", path, value, source=f"tool:{tool.get('name')}")
                return {"ok": True, "path": path, "value": value}
            collection = sim.get("collection")
            key_arg = sim.get("key_arg", "id")
            key = str(args.get(key_arg, ""))
            current = copy.deepcopy(state.setdefault(collection, {}).get(key, {"id": key}))
            current.update({k:v for k,v in args.items() if k != key_arg})
            trial.ledger.commit("set", f"{collection}.{key}", current, source=f"tool:{tool.get('name')}")
            return copy.deepcopy(current)
        if op == "delete":
            collection = sim.get("collection")
            key = str(args.get(sim.get("key_arg", "id"), ""))
            existed = get_path(state, f"{collection}.{key}") is not None
            if existed: trial.ledger.commit("delete", f"{collection}.{key}", source=f"tool:{tool.get('name')}")
            return {"ok": existed, "id": key}
        if op == "state_path":
            return {"value": trial.ledger.read(sim.get("path", ""))}
        if op == "template":
            template = copy.deepcopy(sim.get("response", {"ok": True}))
            return self._render(template, args, state)
        return {"ok": True, "echo": copy.deepcopy(args)}

    def _render(self, value, args, state):
        if isinstance(value, str):
            if value.startswith("$args."):
                return get_path(args, value[6:])
            if value.startswith("$state."):
                return get_path(state, value[7:])
            return value
        if isinstance(value, list): return [self._render(v,args,state) for v in value]
        if isinstance(value, dict): return {k:self._render(v,args,state) for k,v in value.items()}
        return value

    def _apply_declared_mutations(self, trial, tool, args, result):
        context = {**args, **(result if isinstance(result, dict) else {})}
        for mut in tool.get("mutations", []):
            path = mut.get("path", "")
            try: path = path.format(**context)
            except Exception: pass
            op = mut.get("op", "set")
            value = mut.get("value", result)
            if isinstance(value, str) and value.startswith("$result."):
                value = get_path(result, value[8:])
            trial.ledger.commit(op, path, value, source=f"declared:{tool.get('name')}")

    def _match_fault(self, name, occurrence, faults, rng):
        for fault in faults:
            if fault.get("tool") not in {None, name}: continue
            when = fault.get("when", {})
            if "call" in when and when["call"] != occurrence: continue
            if "after_call" in when and occurrence <= int(when["after_call"]): continue
            if "probability" in when and rng.random() > float(when["probability"]): continue
            return fault
        return None

    async def _apply_fault(self, fault):
        inject = fault.get("inject", {})
        if "latency_ms" in inject:
            await asyncio.sleep(float(inject["latency_ms"])/1000)
        if "error" in inject: return {"error": inject["error"], "injected": True}
        if "response" in inject: return copy.deepcopy(inject["response"])
        return {"error":"injected_fault", "injected": True}


def evaluate_assertions(state, events, assertions, ledger=None):
    grades=[]
    tool_events=[e for e in events if e.get("type")=="tool.call"]
    for a in assertions:
        kind=a.get("type"); passed=False; actual=None
        if kind == "state_equals":
            actual=get_path(state,a["path"]); passed=actual==a.get("value")
        elif kind == "state_exists":
            actual=get_path(state,a["path"]); passed=actual is not None
        elif kind == "state_not_exists":
            actual=get_path(state,a["path"]); passed=actual is None
        elif kind == "tool_called":
            actual=sum(1 for e in tool_events if e.get("tool")==a.get("tool")); passed=actual>=a.get("min",1)
        elif kind == "tool_not_called":
            actual=sum(1 for e in tool_events if e.get("tool")==a.get("tool")); passed=actual==0
        elif kind == "tool_call_count":
            actual=sum(1 for e in tool_events if e.get("tool")==a.get("tool")); passed=actual==a.get("value",0)
        elif kind == "max_tool_calls":
            actual=len(tool_events); passed=actual<=a.get("value",999)
        elif kind == "called_before":
            order=[e.get("tool") for e in tool_events]; x=a.get("first"); y=a.get("second")
            actual=order; passed=x in order and y in order and order.index(x)<order.index(y)
        elif kind == "no_duplicate_tool_args":
            target=a.get("tool"); seen=set(); duplicate=False
            for e in tool_events:
                if e.get("tool")!=target: continue
                sig=stable_hash(e.get("args",{})); duplicate |= sig in seen; seen.add(sig)
            actual=duplicate; passed=not duplicate
        elif kind == "ledger_op_count":
            entries=(ledger or {}).get("entries",[]) if isinstance(ledger,dict) else []
            matched=[e for e in entries if (not a.get("op") or e.get("op")==a.get("op")) and (not a.get("path_prefix") or e.get("path","").startswith(a.get("path_prefix")))]
            actual=len(matched); passed=actual==a.get("value",0)
        elif kind == "collection_unique_by":
            collection=get_path(state,a.get("path",""))
            rows=list(collection.values()) if isinstance(collection,dict) else collection if isinstance(collection,list) else []
            fields=a.get("fields") or [a.get("field","id")]; seen=set(); dup=[]
            for row in rows:
                if not isinstance(row,dict): continue
                sig=tuple(row.get(f) for f in fields)
                if sig in seen: dup.append(sig)
                seen.add(sig)
            actual=dup; passed=not dup
        elif kind == "numeric_lte":
            actual=get_path(state,a.get("path","")); limit=a.get("value")
            passed=isinstance(actual,(int,float)) and isinstance(limit,(int,float)) and actual<=limit
        elif kind == "numeric_gte":
            actual=get_path(state,a.get("path","")); limit=a.get("value")
            passed=isinstance(actual,(int,float)) and isinstance(limit,(int,float)) and actual>=limit
        elif kind == "ledger_after_field_equals":
            entries=(ledger or {}).get("entries",[]) if isinstance(ledger,dict) else []
            prefix=a.get("path_prefix",""); field=a.get("field"); expected=a.get("value")
            matched=[e for e in entries if e.get("path","").startswith(prefix) and isinstance(e.get("after"),dict)]
            bad=[{"path":e.get("path"),"actual":e.get("after",{}).get(field)} for e in matched if e.get("after",{}).get(field)!=expected]
            actual=bad; passed=not bad
        elif kind == "ledger_no_repeated_path":
            entries=(ledger or {}).get("entries",[]) if isinstance(ledger,dict) else []
            prefix=a.get("path_prefix",""); seen=set(); repeated=[]
            for e in entries:
                path=e.get("path","")
                if prefix and not path.startswith(prefix): continue
                if path in seen: repeated.append(path)
                seen.add(path)
            actual=repeated; passed=not repeated
        grades.append({"assertion":a,"passed":passed,"actual":actual})
    return grades


async def run_mock_agent(agent, scenario, trial, simulator, seed):
    rng=random.Random(seed)
    tools={t["name"]:t for t in agent.get("tools",[])}
    behavior=agent.get("behavior",{}); plan=behavior.get("plan") or []
    last={}
    for step in plan:
        if "if" in step:
            cond=step["if"]
            actual=get_path(last,cond.get("path","")) if cond.get("source","last")=="last" else get_path(trial.state,cond.get("path",""))
            if actual != cond.get("equals"): continue
        name=step["tool"]
        if name not in tools: continue
        args=copy.deepcopy(step.get("args",{})); vars=scenario.get("variables",{})
        for k,v in list(args.items()):
            if isinstance(v,str) and v.startswith("$"):
                key=v[1:]; args[k]=vars.get(key,last.get(key,v))
        result=await simulator.tool_call(trial,tools[name],args,scenario.get("faults",[]),rng)
        last=result if isinstance(result,dict) else {}
    return behavior.get("final_response", "Completed scenario")
