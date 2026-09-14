from .flexible import FlexibleFrameworkAdapter


class CrewAIAdapter(FlexibleFrameworkAdapter):
    def __init__(self, target, name, version="dev"):
        super().__init__(target, name, version, "crewai", ("kickoff_async", "kickoff", "run"))

    async def run(self, request):
        if hasattr(self.target, "kickoff_async"):
            value = self.target.kickoff_async(inputs={"instruction": request.instruction, **dict(request.metadata)})
        elif hasattr(self.target, "kickoff"):
            value = self.target.kickoff(inputs={"instruction": request.instruction, **dict(request.metadata)})
        else:
            return await super().run(request)
        import inspect
        from ..agents import AgentResponse
        from .flexible import _extract_usage, _output
        if inspect.isawaitable(value):
            value = await value
        metadata = {"framework": "crewai"}
        usage = _extract_usage(value)
        if usage:
            metadata["usage"] = usage
        return AgentResponse(_output(value), metadata)
