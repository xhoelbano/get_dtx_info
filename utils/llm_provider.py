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

    # Which providers expose a native web-search / grounding tool we can bind.
    # Azure OpenAI does not generally expose the built-in web_search tool, so
    # it is intentionally disabled here.
    WEB_SEARCH_SUPPORTED = {
        PROVIDER_AZURE: False,
        PROVIDER_OPENAI: True,
        PROVIDER_GEMINI: True,
        PROVIDER_ANTHROPIC: True,
    }

    @staticmethod
    def get_active_provider() -> str:
        """Return the active provider name from LLM_PROVIDER (normalized)."""
        return (os.getenv("LLM_PROVIDER") or "azure_openai").strip().lower()

    @staticmethod
    def get_active_model(model_override: Optional[str] = None) -> str:
        """Return the exact model name that get_llm() would use."""
        if model_override:
            return model_override
        provider = LLMProvider.get_active_provider()
        if provider == LLMProvider.PROVIDER_AZURE:
            return os.getenv("AZURE_OPENAI_DEPLOYMENT") or ""
        if provider == LLMProvider.PROVIDER_OPENAI:
            return os.getenv("OPENAI_MODEL") or ""
        if provider == LLMProvider.PROVIDER_GEMINI:
            return os.getenv("GEMINI_MODEL") or ""
        if provider == LLMProvider.PROVIDER_ANTHROPIC:
            return os.getenv("ANTHROPIC_MODEL") or ""
        return ""

    @staticmethod
    def get_browser_use_provider() -> str:
        """Return the provider for the browser-use website agent.

        Reads BROWSER_USE_PROVIDER and falls back to LLM_PROVIDER, so by default
        the website agent uses the same provider as the rest of the pipeline.
        """
        return (
            os.getenv("BROWSER_USE_PROVIDER") or LLMProvider.get_active_provider()
        ).strip().lower()

    @staticmethod
    def get_browser_use_model() -> str:
        """Return the model name the browser-use agent will use.

        Reads BROWSER_USE_MODEL, else the chosen provider's standard model var.
        """
        explicit = os.getenv("BROWSER_USE_MODEL")
        if explicit:
            return explicit
        provider = LLMProvider.get_browser_use_provider()
        if provider == LLMProvider.PROVIDER_AZURE:
            return os.getenv("AZURE_OPENAI_DEPLOYMENT") or ""
        if provider == LLMProvider.PROVIDER_OPENAI:
            return os.getenv("OPENAI_MODEL") or ""
        if provider == LLMProvider.PROVIDER_GEMINI:
            return os.getenv("GEMINI_MODEL") or ""
        if provider == LLMProvider.PROVIDER_ANTHROPIC:
            return os.getenv("ANTHROPIC_MODEL") or ""
        return ""

    @staticmethod
    def get_browser_use_source_name() -> str:
        """Human-readable provider/model string for the browser-use agent."""
        provider = LLMProvider.get_browser_use_provider()
        provider_display = {
            LLMProvider.PROVIDER_AZURE: "Azure OpenAI",
            LLMProvider.PROVIDER_OPENAI: "OpenAI",
            LLMProvider.PROVIDER_GEMINI: "Google Gemini",
            LLMProvider.PROVIDER_ANTHROPIC: "Anthropic Claude",
        }.get(provider, provider)
        model = LLMProvider.get_browser_use_model() or "(not set)"
        return f"browser-use ({provider_display} - {model})"

    @staticmethod
    def get_browser_use_llm(temperature: float = 0.1):
        """Build the browser-use chat model for the website evidence agent.

        The provider defaults to LLM_PROVIDER (override with BROWSER_USE_PROVIDER)
        and the model to that provider's standard model var (override with
        BROWSER_USE_MODEL). This reuses the existing per-provider API keys, so the
        website agent no longer depends on a separate hardcoded OpenAI account.

        Returns:
            Tuple of (llm, provider, model) where llm is a ``browser_use`` chat
            model instance.

        Raises:
            ValueError: If the provider is unknown or a required env var is missing.
            ImportError: If browser-use is not installed.
        """
        provider = LLMProvider.get_browser_use_provider()
        model = LLMProvider.get_browser_use_model()

        try:
            from browser_use import (
                ChatAnthropic,
                ChatAzureOpenAI,
                ChatGoogle,
                ChatOpenAI,
            )
        except ImportError as exc:
            raise ImportError(
                "browser-use is required for the website evidence agent. "
                "Install with: pip install browser-use"
            ) from exc

        if not model:
            raise ValueError(
                f"No model configured for the browser-use agent. Set BROWSER_USE_MODEL "
                f"or the model var for provider '{provider}'."
            )

        if provider == LLMProvider.PROVIDER_OPENAI:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                raise ValueError("OPENAI_API_KEY must be set for browser-use provider 'openai'")
            llm = ChatOpenAI(model=model, api_key=api_key, temperature=temperature)
        elif provider == LLMProvider.PROVIDER_GEMINI:
            api_key = os.getenv("GOOGLE_API_KEY")
            if not api_key:
                raise ValueError("GOOGLE_API_KEY must be set for browser-use provider 'gemini'")
            llm = ChatGoogle(model=model, api_key=api_key, temperature=temperature)
        elif provider == LLMProvider.PROVIDER_ANTHROPIC:
            api_key = os.getenv("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError("ANTHROPIC_API_KEY must be set for browser-use provider 'anthropic'")
            llm = ChatAnthropic(model=model, api_key=api_key, temperature=temperature)
        elif provider == LLMProvider.PROVIDER_AZURE:
            api_key = os.getenv("AZURE_OPENAI_API_KEY")
            endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
            if not api_key or not endpoint:
                raise ValueError(
                    "AZURE_OPENAI_API_KEY and AZURE_OPENAI_ENDPOINT must be set for "
                    "browser-use provider 'azure_openai'"
                )
            llm = ChatAzureOpenAI(
                model=model,
                api_key=api_key,
                azure_endpoint=endpoint.rstrip("/"),
                api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-08-01-preview"),
                temperature=temperature,
            )
        else:
            raise ValueError(
                f"Unknown browser-use provider: {provider}. Use one of: "
                f"{LLMProvider.PROVIDER_AZURE}, {LLMProvider.PROVIDER_OPENAI}, "
                f"{LLMProvider.PROVIDER_GEMINI}, {LLMProvider.PROVIDER_ANTHROPIC}"
            )

        return llm, provider, model

    @staticmethod
    def _default_model_for_provider(provider: str) -> str:
        """Return the standard model env var value for a given provider."""
        if provider == LLMProvider.PROVIDER_AZURE:
            return os.getenv("AZURE_OPENAI_DEPLOYMENT") or ""
        if provider == LLMProvider.PROVIDER_OPENAI:
            return os.getenv("OPENAI_MODEL") or ""
        if provider == LLMProvider.PROVIDER_GEMINI:
            return os.getenv("GEMINI_MODEL") or ""
        if provider == LLMProvider.PROVIDER_ANTHROPIC:
            return os.getenv("ANTHROPIC_MODEL") or ""
        return ""

    @staticmethod
    def get_phase3_provider() -> str:
        """Return the provider for the Phase 3 evidence analysis step.

        Reads PHASE3_PROVIDER and falls back to LLM_PROVIDER, so by default the
        analyzer uses the same provider as the rest of the pipeline. Set
        PHASE3_PROVIDER to benchmark a different model family for analysis.
        """
        return (
            os.getenv("PHASE3_PROVIDER") or LLMProvider.get_active_provider()
        ).strip().lower()

    @staticmethod
    def get_phase3_model() -> str:
        """Return the model name the Phase 3 analyzer will use.

        Reads PHASE3_MODEL, else the chosen provider's standard model var.
        """
        explicit = os.getenv("PHASE3_MODEL")
        if explicit:
            return explicit
        return LLMProvider._default_model_for_provider(LLMProvider.get_phase3_provider())

    @staticmethod
    def get_phase3_source_name(model_override: Optional[str] = None) -> str:
        """Human-readable provider/model string for the Phase 3 analyzer."""
        provider = LLMProvider.get_phase3_provider()
        provider_display = {
            LLMProvider.PROVIDER_AZURE: "Azure OpenAI",
            LLMProvider.PROVIDER_OPENAI: "OpenAI",
            LLMProvider.PROVIDER_GEMINI: "Google Gemini",
            LLMProvider.PROVIDER_ANTHROPIC: "Anthropic Claude",
        }.get(provider, provider)
        model = model_override or LLMProvider.get_phase3_model() or "(not set)"
        return f"Phase 3 Analysis ({provider_display} - {model})"

    @staticmethod
    def get_phase3_llm(
        temperature: float = 0.0,
        max_tokens: int = 4000,
        model_override: Optional[str] = None,
    ):
        """Build the LangChain chat model used for Phase 3 evidence analysis.

        The provider defaults to LLM_PROVIDER (override with PHASE3_PROVIDER) and
        the model to that provider's standard model var (override with
        PHASE3_MODEL, or the ``model_override`` argument). This reuses the
        existing per-provider builders/keys. No web-search tool is bound: Phase 3
        is a grounded extraction step over local raw files only.

        Returns:
            Tuple of (llm, provider, model).

        Raises:
            ValueError: If the provider is unknown or a required env var is missing.
        """
        provider = LLMProvider.get_phase3_provider()
        model = model_override or LLMProvider.get_phase3_model()
        if not model:
            raise ValueError(
                f"No model configured for the Phase 3 analyzer. Set PHASE3_MODEL "
                f"or the model var for provider '{provider}'."
            )

        if provider == LLMProvider.PROVIDER_AZURE:
            llm = LLMProvider._get_azure_llm(temperature, max_tokens, model_override=model)
        elif provider == LLMProvider.PROVIDER_OPENAI:
            llm = LLMProvider._get_openai_llm(temperature, max_tokens, model_override=model)
        elif provider == LLMProvider.PROVIDER_GEMINI:
            llm = LLMProvider._get_gemini_llm(temperature, max_tokens, model_override=model)
        elif provider == LLMProvider.PROVIDER_ANTHROPIC:
            llm = LLMProvider._get_anthropic_llm(temperature, max_tokens, model_override=model)
        else:
            raise ValueError(
                f"Unknown Phase 3 provider: {provider}. Use one of: "
                f"{LLMProvider.PROVIDER_AZURE}, {LLMProvider.PROVIDER_OPENAI}, "
                f"{LLMProvider.PROVIDER_GEMINI}, {LLMProvider.PROVIDER_ANTHROPIC}"
            )

        return llm, provider, model

    @staticmethod
    def _env_web_search_enabled() -> bool:
        """Read the ENABLE_WEB_SEARCH env toggle (default: True)."""
        raw = (os.getenv("ENABLE_WEB_SEARCH") or "true").strip().lower()
        return raw not in ("0", "false", "no", "off", "")

    @staticmethod
    def web_search_active(enable_web_search: Optional[bool] = None) -> bool:
        """Return whether web search will actually be applied for the active provider.

        Combines the env/explicit toggle with provider capability so callers can
        log the real state.
        """
        enabled = (
            LLMProvider._env_web_search_enabled()
            if enable_web_search is None
            else enable_web_search
        )
        if not enabled:
            return False
        provider = LLMProvider.get_active_provider()
        return LLMProvider.WEB_SEARCH_SUPPORTED.get(provider, False)

    @staticmethod
    def _web_search_tool(provider: str) -> Optional[Any]:
        """Return the provider-native web-search tool spec, or None."""
        if provider == LLMProvider.PROVIDER_OPENAI:
            return {"type": "web_search"}
        if provider == LLMProvider.PROVIDER_ANTHROPIC:
            return {
                "type": "web_search_20250305",
                "name": "web_search",
                "max_uses": 5,
            }
        if provider == LLMProvider.PROVIDER_GEMINI:
            return {"google_search": {}}
        return None

    @staticmethod
    def _maybe_bind_web_search(
        llm: Any,
        provider: str,
        enable_web_search: Optional[bool],
    ) -> Any:
        """Bind the native web-search tool when enabled and supported.

        Falls back to the plain model (with a warning) if binding is disabled,
        unsupported, or raises.
        """
        if not LLMProvider.web_search_active(enable_web_search):
            if (
                (LLMProvider._env_web_search_enabled() if enable_web_search is None else enable_web_search)
                and not LLMProvider.WEB_SEARCH_SUPPORTED.get(provider, False)
            ):
                print(
                    f"      [web_search] requested but not supported for provider "
                    f"'{provider}'; using plain completion."
                )
            return llm

        tool = LLMProvider._web_search_tool(provider)
        if tool is None:
            return llm
        try:
            return llm.bind_tools([tool])
        except Exception as exc:
            print(
                f"      [web_search] failed to bind tool for '{provider}' "
                f"({exc}); using plain completion."
            )
            return llm

    @staticmethod
    def get_llm(
        temperature: float = 0.0,
        max_tokens: int = 500,
        model_override: Optional[str] = None,
        enable_web_search: Optional[bool] = None,
    ) -> Any:
        """Return the configured LLM based on LLM_PROVIDER env var.

        Args:
            temperature: Model temperature (0.0 = deterministic).
            max_tokens: Maximum tokens in response.
            model_override: Optional model name to use instead of env default.
            enable_web_search: Force web search on/off. When None (default),
                the ENABLE_WEB_SEARCH env toggle is used (default on). Web search
                is only applied when the active provider supports it.

        Returns:
            LangChain chat model (BaseChatModel) instance, optionally bound with
            a native web-search tool.

        Raises:
            ValueError: If provider is unknown or required env vars are missing.
        """
        provider = LLMProvider.get_active_provider()

        if provider == LLMProvider.PROVIDER_AZURE:
            llm = LLMProvider._get_azure_llm(
                temperature=temperature,
                max_tokens=max_tokens,
                model_override=model_override,
            )
        elif provider == LLMProvider.PROVIDER_OPENAI:
            llm = LLMProvider._get_openai_llm(
                temperature=temperature,
                max_tokens=max_tokens,
                model_override=model_override,
            )
        elif provider == LLMProvider.PROVIDER_GEMINI:
            llm = LLMProvider._get_gemini_llm(
                temperature=temperature,
                max_tokens=max_tokens,
                model_override=model_override,
            )
        elif provider == LLMProvider.PROVIDER_ANTHROPIC:
            llm = LLMProvider._get_anthropic_llm(
                temperature=temperature,
                max_tokens=max_tokens,
                model_override=model_override,
            )
        else:
            raise ValueError(
                f"Unknown LLM_PROVIDER: {provider}. "
                f"Use one of: {LLMProvider.PROVIDER_AZURE}, {LLMProvider.PROVIDER_OPENAI}, "
                f"{LLMProvider.PROVIDER_GEMINI}, {LLMProvider.PROVIDER_ANTHROPIC}"
            )

        return LLMProvider._maybe_bind_web_search(llm, provider, enable_web_search)

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
