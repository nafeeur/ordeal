from ordeal_agent import AgentResponse, CallableAgent, OutputContains, Scenario, Suite, Variant, World


async def baseline_agent(_request):
    return AgentResponse("approved", {"usage": {"total_tokens": 12, "cost_usd": 0.002}})


async def candidate_agent(_request):
    return AgentResponse("approved quickly", {"usage": {"total_tokens": 8, "cost_usd": 0.001}})


suite = Suite.of(
    "approval-comparison",
    Scenario("approval", "Approve the request", World("empty"), assertions=[OutputContains("approved")]).repeat(3),
)

variants = [
    Variant("baseline-model-prompt", CallableAgent(baseline_agent, name="approval-agent", version="1")),
    Variant("candidate-model-prompt", CallableAgent(candidate_agent, name="approval-agent", version="2")),
]
