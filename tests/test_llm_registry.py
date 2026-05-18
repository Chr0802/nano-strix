import pytest

from nano_strix.config.schema import LLMConfig
from nano_strix.llm.adapter import LLMProvider, LLMResponse
from nano_strix.llm.factory import create_provider
from nano_strix.llm.registry import (
    PROVIDER_REGISTRY,
    get_provider_class,
    register_provider,
)


class FakeProvider(LLMProvider):
    def __init__(self, api_key="", base_url="", model=""):
        self.api_key = api_key
        self.base_url = base_url
        self.model = model

    async def chat(self, messages, tools=None, temperature=0.7, max_tokens=4096):
        return LLMResponse(content="fake", model="fake-model")

    async def stream_chat(self, messages, tools=None, temperature=0.7, max_tokens=4096):
        yield "fake"


def test_register_provider():
    original = dict(PROVIDER_REGISTRY)
    try:
        register_provider("fake")(FakeProvider)
        assert "fake" in PROVIDER_REGISTRY
        assert PROVIDER_REGISTRY["fake"] is FakeProvider
    finally:
        PROVIDER_REGISTRY.clear()
        PROVIDER_REGISTRY.update(original)


def test_get_provider_class():
    original = dict(PROVIDER_REGISTRY)
    try:
        register_provider("fake")(FakeProvider)
        cls = get_provider_class("fake")
        assert cls is FakeProvider
    finally:
        PROVIDER_REGISTRY.clear()
        PROVIDER_REGISTRY.update(original)


def test_get_unknown_provider():
    with pytest.raises(KeyError):
        get_provider_class("nonexistent")


def test_create_provider():
    original = dict(PROVIDER_REGISTRY)
    try:
        register_provider("fake")(FakeProvider)
        config = LLMConfig(provider="fake", api_key="test-key", model="fake-model")
        provider = create_provider(config)
        assert isinstance(provider, FakeProvider)
    finally:
        PROVIDER_REGISTRY.clear()
        PROVIDER_REGISTRY.update(original)
