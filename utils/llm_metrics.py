"""Reusable LLM benchmarking utility.

Wraps any LangChain chat-model call to capture latency, token usage, and an
estimated USD cost, then appends one row per call to a JSONL log. Designed to be
provider-agnostic (OpenAI, Azure, Anthropic, Gemini) so the USA scraper,
evidence analyzer, translator, and future country scrapers can all reuse it.

Usage:
    from utils.llm_metrics import invoke_with_metrics, aggregate

    response, metrics = await invoke_with_metrics(
        llm, messages, provider="openai", model="gpt-4o", call_label="usa_research"
    )
    ...
    totals = aggregate(all_metrics)
"""
import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PRICING_PATH = Path("config/llm_pricing.json")
DEFAULT_METRICS_LOG = Path("data/llm_metrics.jsonl")

_pricing_cache: Optional[Dict[str, Any]] = None


def _load_pricing() -> Dict[str, Any]:
    """Load and cache the pricing table. Returns {} if the file is missing."""
    global _pricing_cache
    if _pricing_cache is not None:
        return _pricing_cache
    if not PRICING_PATH.exists():
        _pricing_cache = {}
        return _pricing_cache
    with open(PRICING_PATH, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    _pricing_cache = {k: v for k, v in data.items() if not k.startswith("_")}
    return _pricing_cache


def _lookup_price(provider: str, model: str) -> Optional[Dict[str, float]]:
    """Find the price entry for a provider/model.

    Matching is by substring (the model name contains the pricing key), and the
    longest matching key wins so that e.g. "gpt-4o-mini" beats "gpt-4o".
    """
    pricing = _load_pricing()
    provider_table = pricing.get((provider or "").strip().lower())
    if not provider_table or not model:
        return None

    model_lower = model.lower()
    best_key = None
    for key in provider_table:
        if key.lower() in model_lower:
            if best_key is None or len(key) > len(best_key):
                best_key = key
    return provider_table.get(best_key) if best_key else None


def _extract_token_usage(response: Any) -> Dict[str, Optional[int]]:
    """Pull input/output/total token counts from a LangChain response.

    Tries the standardized ``usage_metadata`` first, then falls back to the
    provider-specific ``response_metadata`` shapes (OpenAI ``token_usage``,
    Anthropic ``usage``).
    """
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    total_tokens: Optional[int] = None

    usage = getattr(response, "usage_metadata", None)
    if isinstance(usage, dict) and usage:
        input_tokens = usage.get("input_tokens")
        output_tokens = usage.get("output_tokens")
        total_tokens = usage.get("total_tokens")

    if input_tokens is None or output_tokens is None:
        meta = getattr(response, "response_metadata", None) or {}
        token_usage = meta.get("token_usage") or meta.get("usage") or {}
        if isinstance(token_usage, dict):
            input_tokens = input_tokens if input_tokens is not None else (
                token_usage.get("prompt_tokens") or token_usage.get("input_tokens")
            )
            output_tokens = output_tokens if output_tokens is not None else (
                token_usage.get("completion_tokens") or token_usage.get("output_tokens")
            )
            total_tokens = total_tokens if total_tokens is not None else token_usage.get("total_tokens")

    if total_tokens is None and input_tokens is not None and output_tokens is not None:
        total_tokens = input_tokens + output_tokens

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def estimate_cost(
    provider: str,
    model: str,
    input_tokens: Optional[int],
    output_tokens: Optional[int],
) -> Optional[float]:
    """Estimate USD cost from token counts and the pricing table.

    Returns ``None`` when pricing or token counts are unavailable.
    """
    price = _lookup_price(provider, model)
    if not price or input_tokens is None or output_tokens is None:
        return None
    cost = (
        input_tokens * price.get("input_per_1m", 0.0)
        + output_tokens * price.get("output_per_1m", 0.0)
    ) / 1_000_000
    return round(cost, 6)


def _log_metrics(metrics: Dict[str, Any], log_path: Path) -> None:
    """Append one metrics row to the JSONL log (best effort)."""
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(metrics, ensure_ascii=False) + "\n")
    except Exception:
        # Never let logging break the actual research call.
        pass


async def invoke_with_metrics(
    llm: Any,
    messages: Any,
    *,
    provider: str,
    model: str,
    call_label: str = "",
    web_search: Optional[bool] = None,
    log_path: Optional[Path] = None,
    extra: Optional[Dict[str, Any]] = None,
) -> Tuple[Any, Dict[str, Any]]:
    """Invoke an async LangChain model, capturing latency, tokens, and cost.

    Args:
        llm: A LangChain chat model (or a bound model) exposing ``ainvoke``.
        messages: Messages to send.
        provider: Provider name (e.g. "openai", "anthropic", "gemini").
        model: Exact model name (used for cost lookup + logging).
        call_label: Free-form label to group calls (e.g. "usa_research").
        web_search: Whether web search was enabled for this call (logged only).
        log_path: Override the JSONL log path.
        extra: Optional extra fields to merge into the logged row.

    Returns:
        Tuple of (response, metrics dict).
    """
    log_path = log_path or DEFAULT_METRICS_LOG

    start = time.perf_counter()
    error: Optional[str] = None
    response: Any = None
    try:
        response = await llm.ainvoke(messages)
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    latency_ms = round((time.perf_counter() - start) * 1000, 2)

    tokens = (
        _extract_token_usage(response)
        if response is not None
        else {"input_tokens": None, "output_tokens": None, "total_tokens": None}
    )
    cost = estimate_cost(provider, model, tokens["input_tokens"], tokens["output_tokens"])

    metrics: Dict[str, Any] = {
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "call_label": call_label,
        "provider": provider,
        "model": model,
        "web_search": web_search,
        "latency_ms": latency_ms,
        "input_tokens": tokens["input_tokens"],
        "output_tokens": tokens["output_tokens"],
        "total_tokens": tokens["total_tokens"],
        "estimated_cost_usd": cost,
        "success": error is None,
        "error": error,
    }
    if extra:
        metrics.update(extra)

    _log_metrics(metrics, log_path)

    if error is not None:
        # Re-raise so callers can handle failures as before, but the metric is logged.
        raise RuntimeError(error)

    return response, metrics


def aggregate(metrics_list: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarize a list of per-call metrics into totals/averages.

    Token/cost totals only count calls where the value is available; latency
    averages over all calls.
    """
    def _sum(field: str) -> int:
        return sum(m.get(field) or 0 for m in metrics_list)

    costs = [m.get("estimated_cost_usd") for m in metrics_list if m.get("estimated_cost_usd") is not None]
    latencies = [m.get("latency_ms") for m in metrics_list if m.get("latency_ms") is not None]
    successes = sum(1 for m in metrics_list if m.get("success"))

    total_cost = round(sum(costs), 6) if costs else None

    return {
        "total_calls": len(metrics_list),
        "successful_calls": successes,
        "failed_calls": len(metrics_list) - successes,
        "total_input_tokens": _sum("input_tokens"),
        "total_output_tokens": _sum("output_tokens"),
        "total_tokens": _sum("total_tokens"),
        "total_estimated_cost_usd": total_cost,
        "total_latency_ms": round(sum(latencies), 2) if latencies else 0.0,
        "avg_latency_ms": round(sum(latencies) / len(latencies), 2) if latencies else 0.0,
    }
