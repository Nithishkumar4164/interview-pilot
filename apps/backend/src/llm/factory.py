from src.llm.interface import ILLMProvider
from src.llm.providers.anthropic import AnthropicProvider
from src.llm.providers.mock import MockProvider
from src.llm.providers.openai import OpenAIProvider

_PROVIDER_REGISTRY: dict[str, type[ILLMProvider]] = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
    "mock": MockProvider,
}


class ProviderFactory:
    """Creates LLM provider instances from provider type and model strings."""

    @staticmethod
    def create(provider_type: str, model: str) -> ILLMProvider:
        cls = _PROVIDER_REGISTRY.get(provider_type)
        if cls is None:
            raise ValueError(
                f"Unknown provider '{provider_type}'. Available: {list(_PROVIDER_REGISTRY.keys())}"
            )
        return cls(model=model)

    @staticmethod
    def register(name: str, cls: type[ILLMProvider]) -> None:
        """Register a new provider type (for plugins/extensions)."""
        _PROVIDER_REGISTRY[name] = cls
