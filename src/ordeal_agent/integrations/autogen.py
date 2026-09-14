from .flexible import FlexibleFrameworkAdapter


class AutoGenAdapter(FlexibleFrameworkAdapter):
    def __init__(self, target, name, version="dev"):
        super().__init__(target, name, version, "autogen", ("run", "run_stream", "on_messages"), "task")
