import httpx
from openai import AsyncOpenAI

from app.config import settings

_client: AsyncOpenAI | None = None


def _build_verify() -> str | bool:
    if settings.LLM_CERT_PATH:
        return settings.LLM_CERT_PATH
    return settings.LLM_SSL_VERIFY


def get_llm_client() -> AsyncOpenAI:
    global _client
    if _client is not None:
        return _client
    _client = AsyncOpenAI(
        api_key=settings.LLM_API_KEY,
        base_url=settings.LLM_BASE_URL,
        http_client=httpx.AsyncClient(verify=_build_verify(), timeout=60.0),
    )
    return _client


async def call_llm(
    system_prompt: str,
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int | None = None,
) -> str:
    client = get_llm_client()
    all_messages = [{"role": "system", "content": system_prompt}] + messages
    response = await client.chat.completions.create(
        model=settings.LLM_MODEL,
        messages=all_messages,
        temperature=temperature,
        max_tokens=max_tokens or settings.LLM_MAX_TOKENS,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    message = response.choices[0].message
    return message.content or getattr(message, "reasoning_content", None) or ""


async def stream_llm(
    system_prompt: str,
    messages: list[dict],
    temperature: float = 0.7,
    max_tokens: int | None = None,
):
    client = get_llm_client()
    all_messages = [{"role": "system", "content": system_prompt}] + messages
    stream = await client.chat.completions.create(
        model=settings.LLM_MODEL,
        messages=all_messages,
        temperature=temperature,
        max_tokens=max_tokens or settings.LLM_MAX_TOKENS,
        stream=True,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    async for chunk in stream:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
            yield delta.content
