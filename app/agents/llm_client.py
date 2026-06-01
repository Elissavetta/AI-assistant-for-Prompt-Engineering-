from openai import OpenAI

from app.config import settings


def get_openai_client() -> OpenAI:
    return OpenAI(api_key=settings.OPENAI_API_KEY)


async def call_llm(system_prompt: str, messages: list[dict], temperature: float = 0.7) -> str:
    client = get_openai_client()
    all_messages = [{"role": "system", "content": system_prompt}] + messages
    response = client.chat.completions.create(
        model=settings.OPENAI_MODEL,
        messages=all_messages,
        temperature=temperature,
        max_tokens=2000,
    )
    return response.choices[0].message.content
