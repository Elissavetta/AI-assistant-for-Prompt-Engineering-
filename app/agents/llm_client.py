import asyncio
import logging

import httpx
from openai import AsyncOpenAI

from app.config import settings

logger = logging.getLogger("prompt_trainer")

_client: AsyncOpenAI | None = None
_client_lock = asyncio.Lock()

_profiler_client: AsyncOpenAI | None = None
_profiler_client_lock = asyncio.Lock()


def _build_verify() -> str | bool:
    if settings.LLM_CERT_PATH:
        return settings.LLM_CERT_PATH
    return settings.LLM_SSL_VERIFY


async def get_llm_client() -> AsyncOpenAI:
    global _client
    if _client is not None:
        return _client
    async with _client_lock:
        if _client is not None:
            return _client
        verify = _build_verify()
        logger.info(
            "Creating LLM client: base_url=%s, verify=%s, api_key=%s...",
            settings.LLM_BASE_URL, verify, settings.LLM_API_KEY[:8] + "..." if settings.LLM_API_KEY else "EMPTY",
        )
        _client = AsyncOpenAI(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            http_client=httpx.AsyncClient(verify=verify, timeout=30.0),
        )
        return _client


async def get_profiler_llm_client() -> AsyncOpenAI:
    global _profiler_client
    if _profiler_client is not None:
        return _profiler_client
    async with _profiler_client_lock:
        if _profiler_client is not None:
            return _profiler_client
        if not settings.PROFILER_LLM_API_KEY:
            logger.warning(
                "PROFILER_LLM_API_KEY is empty, falling back to main LLM client for profiler"
            )
            _profiler_client = await get_llm_client()
            return _profiler_client
        verify = _build_verify()
        logger.info(
            "Creating profiler LLM client: base_url=%s, verify=%s, api_key=%s..., model=%s",
            settings.LLM_BASE_URL, verify,
            settings.PROFILER_LLM_API_KEY[:8] + "...",
            settings.PROFILER_LLM_MODEL,
        )
        _profiler_client = AsyncOpenAI(
            api_key=settings.PROFILER_LLM_API_KEY,
            base_url=settings.LLM_BASE_URL,
            http_client=httpx.AsyncClient(verify=verify, timeout=30.0),
        )
        return _profiler_client


async def call_llm(
    system_prompt: str,
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int | None = None,
    client: AsyncOpenAI | None = None,
    model: str | None = None,
) -> str:
    llm_client = client or await get_llm_client()
    use_model = model or settings.LLM_MODEL
    all_messages = [{"role": "system", "content": system_prompt}] + messages

    for attempt in range(1, settings.LLM_RETRY_ATTEMPTS + 1):
        try:
            response = await llm_client.chat.completions.create(
                model=use_model,
                messages=all_messages,
                temperature=temperature,
                max_tokens=max_tokens or settings.LLM_MAX_TOKENS,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            message = response.choices[0].message
            content = message.content
            if content:
                return content
            reasoning = getattr(message, "reasoning_content", None)
            if reasoning:
                return reasoning
            return ""
        except Exception as e:
            logger.warning(
                "LLM call attempt %d/%d failed: %s | base_url=%s model=%s",
                attempt, settings.LLM_RETRY_ATTEMPTS, e, settings.LLM_BASE_URL, use_model,
            )
            if attempt == settings.LLM_RETRY_ATTEMPTS:
                raise
            backoff = settings.LLM_RETRY_BACKOFF * (2 ** (attempt - 1))
            await asyncio.sleep(backoff)


async def stream_llm(
    system_prompt: str,
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int | None = None,
    client: AsyncOpenAI | None = None,
    model: str | None = None,
):
    llm_client = client or await get_llm_client()
    use_model = model or settings.LLM_MODEL
    all_messages = [{"role": "system", "content": system_prompt}] + messages

    for attempt in range(1, settings.LLM_RETRY_ATTEMPTS + 1):
        try:
            stream = await llm_client.chat.completions.create(
                model=use_model,
                messages=all_messages,
                temperature=temperature,
                max_tokens=max_tokens or settings.LLM_MAX_TOKENS,
                stream=True,
                extra_body={"chat_template_kwargs": {"enable_thinking": False}},
            )
            break
        except Exception as e:
            logger.warning(
                "LLM stream attempt %d/%d failed: %s | base_url=%s model=%s",
                attempt, settings.LLM_RETRY_ATTEMPTS, e, settings.LLM_BASE_URL, use_model,
            )
            if attempt == settings.LLM_RETRY_ATTEMPTS:
                raise
            backoff = settings.LLM_RETRY_BACKOFF * (2 ** (attempt - 1))
            await asyncio.sleep(backoff)
    else:
        return

    async for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            yield delta.content
