from ordeal_agent import (
    CallableAgent,
    OutputContains,
    Scenario,
    StateEquals,
    Suite,
    ToolCalled,
    ToolOrder,
    World,
    simulated,
)


def lookup_order(args, ctx):
    return ctx.world.get("orders", {}).get(args["order_id"], {"status": "missing"})


def refund_order(args, ctx):
    return {"ok": True, "order_id": args["order_id"]}


def mark_refunded(args, result, ctx):
    orders = dict(ctx.world.get("orders", {}))
    order = dict(orders[args["order_id"]])
    order["status"] = "refunded"
    orders[args["order_id"]] = order
    ctx.world.set("orders", orders)


world = World(
    "support",
    initial_state={"orders": {"A-100": {"status": "paid", "amount": 42}}},
    tools=[
        simulated(
            "lookup_order",
            lookup_order,
            input_schema={
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
                "additionalProperties": False,
            },
        ),
        simulated(
            "refund_order",
            refund_order,
            mutate=mark_refunded,
            input_schema={
                "type": "object",
                "properties": {"order_id": {"type": "string"}},
                "required": ["order_id"],
                "additionalProperties": False,
            },
        ),
    ],
)


async def support_agent(request):
    order = await request.runtime.call("lookup_order", order_id="A-100")
    if order.get("status") == "paid":
        await request.runtime.call("refund_order", order_id="A-100")
        return "Refund completed for A-100"
    return "No refundable order found"


agent = CallableAgent(support_agent, name="support-agent", version="1.0.0")

scenario = Scenario(
    "refund-paid-order",
    "Refund order A-100",
    world,
    assertions=[
        ToolCalled("lookup_order"),
        ToolCalled("refund_order"),
        ToolOrder("lookup_order", "refund_order"),
        StateEquals("orders", {"A-100": {"status": "refunded", "amount": 42}}),
        OutputContains("Refund completed"),
    ],
    repetitions=2,
)

suite = Suite.of("customer-support", scenario)
