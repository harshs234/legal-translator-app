"""Model-agnostic LLM client (via LiteLLM).

LiteLLM gives one OpenAI-style interface over ~100 providers, so the rest of the
app never changes when you swap models. Choose the model with LLM_MODEL:

    claude-sonnet-5-5            -> Anthropic   (needs ANTHROPIC_API_KEY)
    gpt-4o                       -> OpenAI      (needs OPENAI_API_KEY)
    gemini/gemini-2.0-flash      -> Google      (needs GEMINI_API_KEY)
    azure/<deployment>           -> Azure OpenAI (needs AZURE_API_KEY + LLM_API_BASE)
    ollama/llama3                -> local Ollama (set LLM_API_BASE=http://localhost:11434)
    openai/<name>                -> any OpenAI-compatible server (vLLM, LM Studio, etc.)
                                    with LLM_API_BASE=http://host:port/v1

Keys: LiteLLM reads each provider's standard env var automatically
(ANTHROPIC_API_KEY, OPENAI_API_KEY, GEMINI_API_KEY, ...). You can also force one
with LLM_API_KEY, and point at a custom/local endpoint with LLM_API_BASE.
"""
import litellm
from litellm import completion
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from config import settings

# Silently ignore params a given model/endpoint doesn't accept (e.g. temperature).
litellm.drop_params = True


def _exc(name):
    return getattr(litellm, name, None)


# Build the retryable-exception tuple defensively so it works across LiteLLM versions.
_RETRYABLE = tuple(
    e for e in (
        _exc("RateLimitError"), _exc("APIConnectionError"), _exc("InternalServerError"),
        _exc("Timeout"), _exc("ServiceUnavailableError"), _exc("APIError"),
    ) if isinstance(e, type) and issubclass(e, Exception)
) or (Exception,)


@retry(stop=stop_after_attempt(5), wait=wait_exponential(min=2, max=60),
       retry=retry_if_exception_type(_RETRYABLE))
def _complete_raw(system: str, user: str, max_tokens: int):
    """Single call to the model. Returns (text, finish_reason).

    finish_reason is normalised by LiteLLM to OpenAI style:
      "stop"           -> model finished naturally (good)
      "length"         -> model hit the output-token cap (TRUNCATED)
      "content_filter" -> blocked by a safety filter
    Gemini's free tier caps output at 8192 tokens, and when it reaches that it
    returns finish_reason="length"; the translator uses this to split + retry.
    The retry here (5 attempts, exp backoff to 60s) also absorbs the free-tier
    15-requests/minute rate limit without any extra throttling code.
    """
    kwargs = dict(
        model=settings.model,
        max_tokens=max_tokens,
        temperature=0,
        timeout=180,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    # Optional explicit overrides; otherwise LiteLLM uses the provider's env var.
    if settings.api_key:
        kwargs["api_key"] = settings.api_key
    if settings.api_base:
        kwargs["api_base"] = settings.api_base

    r = completion(**kwargs)
    choice = r.choices[0]
    text = (choice.message.content or "").strip()
    finish = (getattr(choice, "finish_reason", None) or "").lower()
    return text, finish


def complete_ex(system: str, user: str, max_tokens: int = 8192):
    """Translate/generate and report whether the output was truncated.

    Returns (text, finish_reason). Default max_tokens=8192 is the Gemini
    free-tier ceiling; LiteLLM clamps it for providers that allow less/more.
    """
    return _complete_raw(system, user, max_tokens)


def complete(system: str, user: str, max_tokens: int = 4096) -> str:
    """Backwards-compatible helper used by the classifier: text only."""
    return _complete_raw(system, user, max_tokens)[0]