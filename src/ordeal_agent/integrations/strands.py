from .flexible import FlexibleFrameworkAdapter


class StrandsAdapter(FlexibleFrameworkAdapter):
    def __init__(self, target, name, version="dev"):
        super().__init__(target, name, version, "strands", ("invoke_async", "run", "invoke"))
