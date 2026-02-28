from abc import ABC, abstractmethod
from typing import Any

class LLMProviderInterface(ABC):
    """
    Application layer interface for LLM provider.

    All external LLM Adapters (Ollama, OpenAI, Anthropic, etc ...)
    must implement this interface

    This ensure that the domain and application layers are decoupled from third party implementation.
    """

    @abstractmethod
    def get(self) -> Any:
        """
        Return an LLM instance (e.g , ChatOpenAI) configured and ready to use.

        The returned object should provide the methods excepted by the application / use cases to
        interact with LLM's.
        :return:
        """
        pass