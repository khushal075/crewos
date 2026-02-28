from langchain_openai import ChatOpenAI
from crewos.application.interfaces.llm_provider import LLMProviderInterface
from crewos.core.config import settings

class OllamaLLMProvider(LLMProviderInterface):
    """
        Third-party LLM adapter for Ollama (or OpenAI in LangChain).
        Implements the LLMProviderInterface defined in application layer.
    """

    def get(self):
        """
            Returns a ChatOpenAI instance configured with settings.
            This is the external service adapter layer.
        """
        return ChatOpenAI(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.API_KEY or 'dummy',
            model=settings.LLM_MODEL,
            temperature=0
        )