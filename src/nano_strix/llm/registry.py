from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from nano_strix.llm.adapter import LLMProvider

PROVIDER_REGISTRY: dict[str, type[LLMProvider]] = {}


def register_provider(name: str):
    def decorator(cls: type[LLMProvider]) -> type[LLMProvider]:
        PROVIDER_REGISTRY[name] = cls
        return cls

    return decorator


def get_provider_class(name: str) -> type[LLMProvider]:
    if name not in PROVIDER_REGISTRY:
        registered = list(PROVIDER_REGISTRY.keys())
        raise KeyError(f"Unknown provider: {name}. Registered: {registered}")
    return PROVIDER_REGISTRY[name]
