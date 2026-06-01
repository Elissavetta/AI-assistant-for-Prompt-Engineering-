from app.agents.llm_client import call_llm
from app.prompts.profiler_prompt import PROFILER_SYSTEM_PROMPT


class ProfilerAgent:
    def __init__(self):
        self.system_prompt = PROFILER_SYSTEM_PROMPT

    async def chat(self, messages: list[dict]) -> str:
        return await call_llm(self.system_prompt, messages, temperature=0.5)

    @staticmethod
    def parse_profile(response: str) -> dict:
        level = "newbie"
        sphere = ""
        goals = ""

        if "УРОВЕНЬ:" in response.upper() or "УРОВЕНЬ:" in response:
            parts = response.split("|")
            for part in parts:
                part_stripped = part.strip()
                if "УРОВЕНЬ:" in part_stripped.upper():
                    level_raw = part_stripped.split(":")[-1].strip().lower()
                    if "intermediate" in level_raw or "средний" in level_raw:
                        level = "intermediate"
                    elif "advanced" in level_raw or "продвинутый" in level_raw:
                        level = "advanced"
                    else:
                        level = "newbie"
                elif "СФЕРА:" in part_stripped.upper():
                    sphere = part_stripped.split(":")[-1].strip()
                elif "ЦЕЛИ:" in part_stripped.upper():
                    goals = part_stripped.split(":")[-1].strip()

        return {"level": level, "sphere": sphere, "goals": goals}
