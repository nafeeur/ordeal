from .flexible import FlexibleFrameworkAdapter


class LlamaIndexAdapter(FlexibleFrameworkAdapter):
    def __init__(self, target, name, version="dev"):
        super().__init__(target, name, version, "llamaindex", ("run", "achat", "chat"))
