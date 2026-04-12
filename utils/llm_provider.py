"""Centralized LLM provider factory.

Allows switching between Azure OpenAI, OpenAI, Gemini, and Anthropic
via the LLM_PROVIDER environment variable. All consuming code uses
LLMProvider.get_llm() so no code changes are needed when switching.
"""
import os
from typing import Any, Optional

from dotenv import load_dotenv

load_dotenv(override=True)


class LLMProvider:
    """Factory for LangChain chat models. Switch providers via LLM_PROVIDER env var."""

    PROVIDER_AZURE = "azure_openai"
    PROVIDER_OPENAI = "openai"
    PROVIDER_GEMINI = "gemini"
    PROVIDER_ANTHROPIC = "anthropic"

    @staticmethod
    def get_llm(
        temperature: float = 0.0,
        max_tokens: int = 500,
        model_override: Optional[str] = None,
    ) -> Any:
        """Return the configured LLM based on LLM_PROVIDER env var.

        Args:
            temperature: Model temperature (0.0 = deterministic).
            max_tokens: Maximum tokens in response.
            model_override: Optional model name to use instead of env default.

        Returns:
            LangChain chat model (BaseChatModel) instance.

        Raises:
            ValueError: If provider is unknown or required env vars are missing.
        """
        provider = (os.getenv("LLM_PROVIDER") or "azure_openai").strip().lower()

        if provider == LLMProvider.PROVIDER_AZURE:
            return LLMProvider._get_azure_llm(
                temperature=temperature,
                max_tokens=max_tokens,
                model_override=model_override,
            )
        if provider == LLMProvider.PROVIDER_OPENAI:
            return LLMProvider._get_openai_llm(
                temperature=temperature,
                max_tokens=max_tokens,
                model_override=model_override,
            )
        if provider == LLMProvider.PROVIDER_GEMINI:
            return LLMProvider._get_gemini_llm(
                temperature=temperature,
                max_tokens=max_tokens,
                model_override=model_override,
            )
        if provider == LLMProvider.PROVIDER_ANTHROPIC:
            return LLMProvider._get_anthropic_llm(
                temperature=temperature,
                max_tokens=max_tokens,
                model_override=model_override,
            )

        raise ValueError(
            f"Unknown LLM_PROVIDER: {provider}. "
            f"Use one of: {LLMProvider.PROVIDER_AZURE}, {LLMProvider.PROVIDER_OPENAI}, "
            f"{LLMProvider.PROVIDER_GEMINI}, {LLMProvider.PROVIDER_ANTHROPIC}"
        )

    @staticmethod
    def get_source_name() -> str:
        """Return a human-readable string of the active provider and exact model name.

        Reads the same env variables used by each _get_*_llm() method so the
        string always reflects what was actually used when the model was built.

        Example outputs:
            "LLM Research (Azure OpenAI - gpt-4o)"
            "LLM Research (Google Gemini - gemini-2.5-flash)"
            "LLM Research (Anthropic Claude - claude-3-5-sonnet-20241022)"
        """
        provider = (os.getenv("LLM_PROVIDER") or "azure_openai").strip().lower()

        provider_display = {
            LLMProvider.PROVIDER_AZURE: "Azure OpenAI",
            LLMProvider.PROVIDER_OPENAI: "OpenAI",
            LLMProvider.PROVIDER_GEMINI: "Google Gemini",
            LLMProvider.PROVIDER_ANTHROPIC: "Anthropic Claude",
        }.get(provider, provider)

        # Mirror exactly what each _get_*_llm() reads from env.
        if provider == LLMProvider.PROVIDER_AZURE:
            model = os.getenv("AZURE_OPENAI_DEPLOYMENT") or "(not set)"
        elif provider == LLMProvider.PROVIDER_OPENAI:
            model = os.getenv("OPENAI_MODEL") or "(not set)"
        elif provider == LLMProvider.PROVIDER_GEMINI:
            model = os.getenv("GEMINI_MODEL") or "(not set)"
        elif provider == LLMProvider.PROVIDER_ANTHROPIC:
            model = os.getenv("ANTHROPIC_MODEL") or "(not set)"
        else:
            model = "unknown"

        return f"LLM Research ({provider_display} - {model})"

    @staticmethod
    def _get_azure_llm(
        temperature: float = 0.0,
        max_tokens: int = 500,
        model_override: Optional[str] = None,
    ) -> Any:
        from langchain_openai import AzureChatOpenAI

        api_key = os.getenv("AZURE_OPENAI_API_KEY")
        endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        if not api_key or not endpoint:
            raise ValueError(
                "AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT must be set when LLM_PROVIDER=azure_openai"
            )

        model = model_override or os.getenv("AZURE_OPENAI_DEPLOYMENT")
        if not model:
            raise ValueError(
                "AZURE_OPENAI_DEPLOYMENT must be set when LLM_PROVIDER=azure_openai"
            )
        return AzureChatOpenAI(
            model=model,
            api_key=api_key,
            azure_endpoint=endpoint.rstrip("/"),
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
            temperature=temperature,
            max_tokens=max_tokens,
        )

    @staticmethod
    def _get_openai_llm(
        temperature: float = 0.0,
        max_tokens: int = 500,
        model_override: Optional[str] = None,
    ) -> Any:
        from langchain_openai import ChatOpenAI

        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError(
                "OPENAI_API_KEY must be set when LLM_PROVIDER=openai"
            )

        model = model_override or os.getenv("OPENAI_MODEL")
        if not model:
            raise ValueError(
                "OPENAI_MODEL must be set when LLM_PROVIDER=openai"
            )
        
        # Reasoning models (o1, o3, gpt-5, etc.) use tokens for internal reasoning
        # before producing output. We need significantly more tokens to ensure
        # the actual response isn't truncated. Use max_completion_tokens for these.
        is_reasoning_model = any(
            indicator in model.lower() 
            for indicator in ["o1", "o3", "gpt-5", "-pro"]
        )
        
        if is_reasoning_model:
            # For reasoning models, multiply the token limit to account for
            # reasoning overhead. The reasoning can take 50-80% of tokens.
            # Use max_completion_tokens which is the newer OpenAI parameter.
            adjusted_max = max(max_tokens * 4, 16000)  # At least 16k for reasoning models
            return ChatOpenAI(
                model=model,
                api_key=api_key,
                temperature=temperature,
                max_completion_tokens=adjusted_max,
            )
        else:
            return ChatOpenAI(
                model=model,
                api_key=api_key,
                temperature=temperature,
                max_tokens=max_tokens,
            )

    @staticmethod
    def _get_gemini_llm(
        temperature: float = 0.0,
        max_tokens: int = 500,
        model_override: Optional[str] = None,
    ) -> Any:
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError:
            raise ImportError(
                "langchain-google-genai is required for LLM_PROVIDER=gemini. "
                "Install with: pip install langchain-google-genai"
            )

        api_key = os.getenv("GOOGLE_API_KEY")
        if not api_key:
            raise ValueError(
                "GOOGLE_API_KEY must be set when LLM_PROVIDER=gemini"
            )

        model = model_override or os.getenv("GEMINI_MODEL")
        if not model:
            raise ValueError(
                "GEMINI_MODEL must be set when LLM_PROVIDER=gemini"
            )
        return ChatGoogleGenerativeAI(
            model=model,
            google_api_key=api_key,
            temperature=temperature,
            max_output_tokens=max_tokens,
        )

    @staticmethod
    def _get_anthropic_llm(
        temperature: float = 0.0,
        max_tokens: int = 500,
        model_override: Optional[str] = None,
    ) -> Any:
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError:
            raise ImportError(
                "langchain-anthropic is required for LLM_PROVIDER=anthropic. "
                "Install with: pip install langchain-anthropic"
            )

        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise ValueError(
                "ANTHROPIC_API_KEY must be set when LLM_PROVIDER=anthropic"
            )

        model = model_override or os.getenv("ANTHROPIC_MODEL")
        if not model:
            raise ValueError(
                "ANTHROPIC_MODEL must be set when LLM_PROVIDER=anthropic"
            )
        return ChatAnthropic(
            model=model,
            api_key=api_key,
            temperature=temperature,
            max_tokens=max_tokens,
        )
