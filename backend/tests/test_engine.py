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
