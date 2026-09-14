"""Dependency-free redaction helpers. Pattern matching cannot detect every sensitive value."""
from __future__ import annotations
import re
import json
from typing import Any

DEFAULT_SENSITIVE_KEYS = {"password", "passwd", "authorization", "api_key", "apikey", "secret", "access_token", "refresh_token", "cookie", "set-cookie", "client_secret"}
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
API_SECRET = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{12,}|ord_[A-Za-z0-9_-]{20,}|AKIA[A-Z0-9]{16})\b")


def redact(value: Any, *, suppress_inputs: bool = False, suppress_outputs: bool = False,
           sensitive_fields: list[str] | None = None, pii: bool = True, depth: int = 0) -> Any:
    if depth > 40:
        return "[DEPTH_LIMIT]"
    keys = DEFAULT_SENSITIVE_KEYS | {s.lower() for s in sensitive_fields or []}
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            lower = str(key).lower()
            suppressed = lower in keys or lower.replace("-", "_") in keys
            suppressed |= suppress_inputs and lower in {"input", "inputs", "arguments", "instruction", "prompt", "input.value"}
            suppressed |= suppress_outputs and lower in {"output", "outputs", "result", "final_output", "output.value"}
            # OTel attributes often carry dotted input/output names.
            suppressed |= suppress_inputs and (lower.startswith("gen_ai.prompt") or lower.startswith("llm.input_messages"))
            suppressed |= suppress_outputs and (lower.startswith("gen_ai.completion") or lower.startswith("llm.output_messages"))
            out[str(key)] = "[REDACTED]" if suppressed else redact(item, suppress_inputs=suppress_inputs, suppress_outputs=suppress_outputs, sensitive_fields=sensitive_fields, pii=pii, depth=depth + 1)
        return out
    if isinstance(value, list):
        return [redact(v, suppress_inputs=suppress_inputs, suppress_outputs=suppress_outputs, sensitive_fields=sensitive_fields, pii=pii, depth=depth + 1) for v in value]
    if isinstance(value, str):
        if value.lstrip().startswith(("{", "[")) and len(value) <= 1048576:
            try:
                parsed = json.loads(value)
                if isinstance(parsed, (dict, list)):
                    return json.dumps(redact(parsed, suppress_inputs=suppress_inputs, suppress_outputs=suppress_outputs, sensitive_fields=sensitive_fields, pii=pii, depth=depth + 1), ensure_ascii=False, separators=(",", ":"))
            except (ValueError, RecursionError):
                pass
        value = API_SECRET.sub("[SECRET]", value)
        if pii:
            value = EMAIL.sub("[EMAIL]", SSN.sub("[SSN]", value))
        return value
    return value

