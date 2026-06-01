import re

from app.agents.llm_client import call_llm
from app.prompts.evaluator_prompt import EVALUATOR_SYSTEM_PROMPT


class EvaluatorAgent:
    def __init__(self):
        self.system_prompt = EVALUATOR_SYSTEM_PROMPT

    async def evaluate(self, messages: list[dict], assignment_context: str = "") -> str:
        full_system = self.system_prompt
        if assignment_context:
            full_system += f"\n\nКОНТЕКСТ ЗАДАНИЯ:\n{assignment_context}"
        return await call_llm(full_system, messages, temperature=0.3)

    @staticmethod
    def extract_score(response: str) -> int:
        score_match = re.search(r"SCORE:\s*(-?\d+)", response)
        if score_match:
            return min(10, max(0, int(score_match.group(1))))
        rating_match = re.search(r"[⭐🌟]\s*\**Оценка\**:\s*\**?\s*(-?\d+)", response)
        if rating_match:
            return min(10, max(0, int(rating_match.group(1))))
        return 5
