from __future__ import annotations

from nano_strix.config.schema import LLMConfig
from nano_strix.llm.adapter import LLMProvider
from nano_strix.llm.registry import get_provider_class


def create_provider(config: LLMConfig) -> LLMProvider:
    cls = get_provider_class(config.provider)
    return cls(api_key=config.api_key, base_url=config.base_url, model=config.model)
