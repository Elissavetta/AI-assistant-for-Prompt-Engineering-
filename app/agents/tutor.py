import logging

from app.config import MARKER_LEVEL
from app.services.scoring_service import MODULE_NAMES, MODULE_ORDER

logger = logging.getLogger("prompt_trainer")


def build_user_context(session, eval_context: str = "", score: int | None = None, module_id: int | None = None) -> str:
    mode = session.mode
    profile = session.profile

    if mode == "prompt_up":
        ctx = "Режим: prompt_up"
        if getattr(session, '_is_api', False):
            ctx += "\n\nSKIP_INTRO: да"
    else:
        ctx = f"Уровень: {profile.level}, Сфера: {profile.sphere or 'общая'}, Цели: {profile.goals or 'освоить промпт-инжиниринг'}"
        current_module = module_id or session.get_active_module()
        session.set_current_module(current_module)
        module_score = session.get_module_score(current_module)
        ctx += f", Текущий модуль: {current_module} ({MODULE_NAMES.get(current_module, '')}), Баллов: {module_score}/50"
        if session.is_module_completed(current_module):
            ctx += " [ЗАВЕРШЁН]"

        completed_ids = [mid for mid in MODULE_ORDER if session.is_module_completed(mid)]
        if completed_ids:
            ctx += f", Завершённые модули: {completed_ids}"

        if current_module == 5:
            ctx += "\n\nМОДУЛЬ 5: пользователь отвечает списком файлов с обоснованием, а не промптом. Оценивай выбор файлов, а не структуру промпта."

        last = session.get_last_assistant_message()
        if MARKER_LEVEL in last.upper():
            if not profile.tutor_introduced:
                ctx += "\n\nFIRST_TUTOR: да"

        if session.is_returning_user():
            ctx += "\n\nRETURNING_USER: да"

    if eval_context:
        ctx += f"\n\nРЕЗУЛЬТАТ ОЦЕНКИ:\n{eval_context}"

    if score is not None:
        ctx += f"\n\nФАКТИЧЕСКИЙ БАЛЛ: {score}/10"

    return ctx


def get_agent_config(agent_name: str, user_context: str = "") -> tuple[str, float, int]:
    if agent_name == "PROFILER":
        from app.prompts.profiler_prompt import PROFILER_SYSTEM_PROMPT
        return PROFILER_SYSTEM_PROMPT, 0.5, 250
    elif agent_name == "EVALUATOR":
        from app.prompts.evaluator_prompt import EVALUATOR_SYSTEM_PROMPT
        return EVALUATOR_SYSTEM_PROMPT, 0.3, 450
    else:
        from app.prompts.tutor_prompt import TUTOR_SYSTEM_PROMPT
        system = TUTOR_SYSTEM_PROMPT
        if user_context:
            system += f"\n\nДАННЫЕ ПОЛЬЗОВАТЕЛЯ: {user_context}"
        return system, 0.6, 800
