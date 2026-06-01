from app.agents.llm_client import call_llm
from app.prompts.tutor_prompt import TUTOR_SYSTEM_PROMPT


class TutorAgent:
    def __init__(self):
        self.system_prompt = TUTOR_SYSTEM_PROMPT

    async def chat(self, messages: list[dict], user_context: str = "") -> str:
        full_system = self.system_prompt
        if user_context:
            full_system += f"\n\nДАННЫЕ ПОЛЬЗОВАТЕЛЯ ОТ ПРОФАЙЛЕРА:\n{user_context}"
        return await call_llm(full_system, messages, temperature=0.6)
