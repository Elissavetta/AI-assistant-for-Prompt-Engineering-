from app.agents.profiler import ProfilerAgent
from app.agents.tutor import TutorAgent
from app.agents.evaluator import EvaluatorAgent


class OrchestratorAgent:
    PROFILER = "PROFILER"
    TUTOR = "TUTOR"
    EVALUATOR = "EVALUATOR"

    def __init__(self):
        self.profiler = ProfilerAgent()
        self.tutor = TutorAgent()
        self.evaluator = EvaluatorAgent()
        self.system_prompt = "Deprecated: routing is handled in chat.py"

    def determine_agent(self, user_level: str, conversation_history: list[dict], is_submission: bool) -> str:
        if not user_level or user_level == "newbie":
            has_profiling_result = any(
                "УРОВЕНЬ:" in msg.get("content", "").upper()
                for msg in conversation_history
                if msg.get("role") == "assistant"
            )
            if not has_profiling_result:
                return self.PROFILER

        if is_submission:
            return self.EVALUATOR

        return self.TUTOR

    async def route(
        self,
        messages: list[dict],
        user_level: str = "",
        user_context: str = "",
        assignment_context: str = "",
        is_submission: bool = False,
    ) -> dict:
        agent_name = self.determine_agent(user_level, messages, is_submission)

        if agent_name == self.PROFILER:
            response = await self.profiler.chat(messages)
            profile_data = self.profiler.parse_profile(response)
            return {
                "agent": self.PROFILER,
                "response": response,
                "profile_data": profile_data,
            }

        elif agent_name == self.EVALUATOR:
            response = await self.evaluator.evaluate(messages, assignment_context)
            score = self.evaluator.extract_score(response)
            return {
                "agent": self.EVALUATOR,
                "response": response,
                "score": score,
            }

        else:
            response = await self.tutor.chat(messages, user_context)
            return {
                "agent": self.TUTOR,
                "response": response,
            }
