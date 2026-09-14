# Local behavior SDK quick guide

Install the local source or supplied wheel; server dependencies are optional. The public API includes `World`, `Scenario`, `Suite`, `CallableAgent`, `Runner`, `function_tool`, deterministic assertions, faults, cassettes and regression comparisons. The executable is `ordeal-behavior`.

```python
from ordeal_agent import (World, Scenario, Suite, CallableAgent, Runner,
                          function_tool, ToolCalled, ToolOrder, StateEquals)

def lookup(order_id: str, ctx=None):
    return ctx.world.get("orders")[order_id]

def refund(order_id: str, ctx=None):
    orders = dict(ctx.world.get("orders"))
    orders[order_id] = {**orders[order_id], "refunded": True}
    ctx.world.set("orders", orders)
    return {"ok": True}

world = World("support", initial_state={"orders": {"A1": {"paid": True}}},
              tools=[function_tool(lookup), function_tool(refund)])
async def support(request):
    order = await request.runtime.call("lookup", order_id="A1")
    if order["paid"]:
        await request.runtime.call("refund", order_id="A1")
    return "done"
agent = CallableAgent(support, name="support", version="1")
scenario = Scenario("refund", "Refund A1", world).expect(
    ToolCalled("refund"), ToolOrder("lookup", "refund"))
suite = Suite.of("support", scenario)
```

Save as a Python module exporting `agent` and `suite`, then `ordeal-behavior run path/to/suite.py`. Module loading executes trusted Python; do not load unknown test files on a privileged host. Function annotations generate validation schemas, including postponed annotations. Ambiguous variadic/positional-only functions require an explicit Tool. JSON Schema validates input/output shape but does not make a handler safe.

Every scenario/repetition starts with isolated state and replay cursors. Use faults to inject delay, timeout, errors or intentionally malformed returns; injected malformed outputs deliberately model a broken dependency. Ordering assertions concern observed calls, not authorization truth for arbitrary entities. Pair them with state/argument/custom checks when needed.

Use `Scenario.repeat`, parameterized cases, budgets and `Suite` concurrency for stochastic regression. Seeds control Ordeal simulation, not necessarily a remote model/provider. Unknown usage remains unknown and cannot silently satisfy a budget. A scenario with no assertions or budgets now returns INCOMPLETE. Do not use final text equality as the only oracle for a side-effecting agent.

CLI examples:

```bash
ordeal-behavior run examples/enterprise/suite.py --update-baseline baseline.json --json before.json
ordeal-behavior run examples/enterprise/suite.py --baseline baseline.json --json after.json
ordeal-behavior diff before.json after.json --html diff.html
ordeal-behavior experiment examples/experiments/compare.py --json experiment.json
```

A baseline change is a reviewed test-definition change. Fingerprint drift alone is not necessarily a regression, and repeated observations do not establish a statistically significant difference automatically. Read the full report and affected cases. See `enterprise/API.md` for production traces, pinned dataset replay and shared reports.
