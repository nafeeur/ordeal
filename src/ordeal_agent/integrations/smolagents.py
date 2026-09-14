from .flexible import FlexibleFrameworkAdapter


class SmolagentsAdapter(FlexibleFrameworkAdapter):
    def __init__(self, target, name, version="dev"):
        super().__init__(target, name, version, "smolagents", ("run",))
