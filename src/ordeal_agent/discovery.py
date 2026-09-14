from __future__ import annotations

import importlib.util
import hashlib
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

from .agents import Agent
from .experiments import Variant
from .scenario import Suite


def load_module(path: str | Path) -> ModuleType:
    path = Path(path).resolve()
    spec = importlib.util.spec_from_file_location("ordeal_suite_" + hashlib.sha256(str(path).encode()).hexdigest()[:16], path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(spec.name)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        if previous is None:
            sys.modules.pop(spec.name, None)
        else:
            sys.modules[spec.name] = previous
        raise
    return module


def load_target(target: str) -> tuple[Agent, Suite]:
    path_text, _, suite_name = target.partition(":")
    module = load_module(path_text)
    agent: Any = getattr(module, "agent", None)
    suite: Any = getattr(module, suite_name or "suite", None)
    if agent is None:
        raise RuntimeError(f"{path_text} must export 'agent'")
    if not isinstance(suite, Suite):
        raise RuntimeError(f"{path_text} must export a Suite named {suite_name or 'suite'}")
    return agent, suite


def load_experiment_target(target: str) -> tuple[list[Variant], Suite]:
    path_text, _, suite_name = target.partition(":")
    module = load_module(path_text)
    variants = getattr(module, "variants", None)
    suite = getattr(module, suite_name or "suite", None)
    if not isinstance(variants, (list, tuple)) or not variants or not all(isinstance(v, Variant) for v in variants):
        raise RuntimeError(f"{path_text} must export a non-empty list[Variant] named 'variants'")
    if not isinstance(suite, Suite):
        raise RuntimeError(f"{path_text} must export a Suite named {suite_name or 'suite'}")
    return list(variants), suite
