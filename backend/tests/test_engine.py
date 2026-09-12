import pytest, random
from app.engine import SimulationEngine, evaluate_assertions, WorldLedger

@pytest.mark.asyncio
async def test_stateful_create_and_ledger():
    sim=SimulationEngine(); t=sim.start_trial({"refunds":{}})
    tool={"name":"create_refund","simulation":{"op":"create","collection":"refunds","id_prefix":"refund"}}
    r=await sim.tool_call(t,tool,{"amount":10},[],random.Random(1))
    assert r["id"]=="refund_0001"
    assert t.ledger.export()["entries"][0]["path"]=="refunds.refund_0001"
    grades=evaluate_assertions(t.state,t.events,[{"type":"state_exists","path":"refunds.refund_0001"}],t.ledger.export())
    assert grades[0]["passed"]

@pytest.mark.asyncio
async def test_fault_is_repeatable_and_does_not_mutate():
    sim=SimulationEngine(); t=sim.start_trial({"payments":{"p1":{"status":"captured"}}},seed=7)
    tool={"name":"get_payment","simulation":{"op":"lookup","collection":"payments","key_arg":"id"}}
    faults=[{"tool":"get_payment","when":{"call":1},"inject":{"error":"timeout"}}]
    r=await sim.tool_call(t,tool,{"id":"p1"},faults,random.Random(7))
    assert r["error"]=="timeout"
    assert t.ledger.export()["entries"]==[]

@pytest.mark.asyncio
async def test_after_commit_fault_hides_success_but_preserves_mutation():
    sim=SimulationEngine(); t=sim.start_trial({"deployments":{}})
    tool={"name":"deploy","simulation":{"op":"create","collection":"deployments","id_prefix":"release"}}
    faults=[{"tool":"deploy","when":{"call":1},"inject":{"phase":"after_commit","error":"connection_reset"}}]
    result=await sim.tool_call(t,tool,{"version":"v42"},faults,random.Random(1))
    assert result=={"error":"connection_reset","injected":True}
    assert t.state["deployments"]["release_0001"]["version"]=="v42"
    assert t.events[-1]["fault_phase"]=="after_commit"
    assert t.events[-1]["committed_result"]["id"]=="release_0001"

@pytest.mark.asyncio
async def test_order_and_duplicate_assertions():
    sim=SimulationEngine(); t=sim.start_trial({"refunds":{}})
    a={"name":"get_payment","simulation":{"op":"template","response":{"status":"captured"}}}
    b={"name":"create_refund","simulation":{"op":"create","collection":"refunds","id_prefix":"refund"}}
    rng=random.Random(1)
    await sim.tool_call(t,a,{"id":"p1"},[],rng)
    await sim.tool_call(t,b,{"payment_id":"p1","amount":10},[],rng)
    assertions=[
      {"type":"called_before","first":"get_payment","second":"create_refund"},
      {"type":"no_duplicate_tool_args","tool":"create_refund"},
      {"type":"ledger_op_count","op":"set","path_prefix":"refunds.","value":1},
    ]
    assert all(x["passed"] for x in evaluate_assertions(t.state,t.events,assertions,t.ledger.export()))


def test_ledger_snapshots_and_hashes():
    l=WorldLedger({"x":{"n":1}}); initial=l.export()["initial_hash"]
    l.commit("set","x.n",2,"test")
    out=l.export()
    assert out["final_state"]["x"]["n"]==2
    assert out["final_hash"]!=initial
    assert len(out["snapshots"])==2

@pytest.mark.asyncio
async def test_nested_path_lookup_update_create_matches_docs():
    """The README / examples/enterprise-suite walkthrough documents simulation
    configs shaped like {"op": "lookup", "path": "identity.users.{user_id}"}
    and {"op": "update", "path": "...{service}...", "value_from": "version"} —
    nested, templated paths rather than flat collection/key_arg pairs."""
    sim=SimulationEngine()
    t=sim.start_trial({
        "identity":{"tokens":{"tok-1":{"owner":"u1","status":"active"}}},
        "deployment":{"services":{"checkout-api":{"active_version":"v41"}}},
        "audit":{"events":[]},
    })
    get_token={"name":"get_token","simulation":{"op":"lookup","path":"identity.tokens.{token_id}"}}
    r=await sim.tool_call(t,get_token,{"token_id":"tok-1"},[],random.Random(1))
    assert r=={"owner":"u1","status":"active"}

    deploy={"name":"deploy_release","simulation":{"op":"update","path":"deployment.services.{service}.active_version","value_from":"version"}}
    await sim.tool_call(t,deploy,{"service":"checkout-api","version":"v42"},[],random.Random(1))
    assert t.state["deployment"]["services"]["checkout-api"]["active_version"]=="v42"

    audit={"name":"write_audit_event","simulation":{"op":"create","path":"audit.events"}}
    await sim.tool_call(t,audit,{"action":"deploy","version":"v42"},[],random.Random(1))
    assert len(t.state["audit"]["events"])==1
    assert t.state["audit"]["events"][0]["action"]=="deploy"

@pytest.mark.asyncio
async def test_openai_compatible_agent_includes_scenario_variables(monkeypatch):
    from app import agent_runtime
    from app.engine import SimulationEngine

    captured = {}

    class FakeResponse:
        status_code = 200
        headers = {}
        def raise_for_status(self): pass
        def json(self):
            return {"choices":[{"message":{"role":"assistant","content":"done"}}]}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            captured["payload"] = json
            return FakeResponse()

    monkeypatch.setattr(agent_runtime.httpx, "AsyncClient", FakeClient)
    sim = SimulationEngine()
    trial = sim.start_trial({})
    agent = {"endpoint": "https://openrouter.ai/api/v1", "model": "test/model", "tools": []}
    scenario = {"instruction": "Refund the payment", "variables": {"payment_id": "p1"}}
    result = await agent_runtime.run_openai_compatible(agent, scenario, trial, sim, seed=1)
    assert result == "done"
    user_msg = captured["payload"]["messages"][0]["content"]
    assert "Refund the payment" in user_msg
    assert "p1" in user_msg

@pytest.mark.asyncio
async def test_openai_compatible_retries_on_429(monkeypatch):
    from app import agent_runtime
    from app.engine import SimulationEngine

    calls = {"n": 0}

    class FakeResponse:
        def __init__(self, status_code, body):
            self.status_code = status_code
            self._body = body
            self.headers = {}
        def raise_for_status(self):
            if self.status_code >= 400:
                raise RuntimeError(f"status {self.status_code}")
        def json(self):
            return self._body

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, url, headers=None, json=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return FakeResponse(429, {})
            return FakeResponse(200, {"choices": [{"message": {"role": "assistant", "content": "done"}}]})

    async def fake_sleep(_):
        return None

    monkeypatch.setattr(agent_runtime.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(agent_runtime.asyncio, "sleep", fake_sleep)
    sim = SimulationEngine()
    trial = sim.start_trial({})
    agent = {"endpoint": "https://openrouter.ai/api/v1", "model": "test/model", "tools": []}
    scenario = {"instruction": "hi"}
    result = await agent_runtime.run_openai_compatible(agent, scenario, trial, sim, seed=1)
    assert result == "done"
    assert calls["n"] == 2
