from .flexible import FlexibleFrameworkAdapter


class GoogleADKAdapter(FlexibleFrameworkAdapter):
    def __init__(self, target, name, version="dev"):
        super().__init__(target, name, version, "google-adk", ("run_async", "run", "invoke"), "task")
