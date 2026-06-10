import logging

from app.config import MARKER_LEVEL
from app.services.scoring_service import MODULE_NAMES, MODULE_ORDER
from app.services.session_cache import AwaitingState

logger = logging.getLogger("prompt_trainer")


def build_user_context(session, eval_context: str = "", score: int | None = None, module_id: int | None = None) -> str:
    mode = session.mode
    profile = session.profile

    if mode == "prompt_up":
        ctx = "Режим: prompt_up\n\nВАЖНО: Этот режим ПОЛНОСТЬЮ независим от профиля пользователя. НЕ используй данные о сфере, уровне, целях из предыдущих сообщений. Анализируй ТОЛЬКО сам промпт без привязки к профессии."
        if getattr(session, '_is_api', False):
            ctx += "\n\nSKIP_INTRO: да"
        state = session.get_awaiting_state_enum()
        if state == AwaitingState.NONE and not session._last_eval_context:
            ctx += "\n\nЭТО ПЕРВЫЙ АНАЛИЗ ПРОМПТА: СНАЧАЛА задай 1-3 уточняющих вопроса (роль? контекст? формат? ограничения? цель?). Заканчивай [ОЖИДАЕТСЯ УТОЧНЕНИЕ]. НЕ давай улучшенную версию промпта сразу."
        elif state == AwaitingState.CLARIFICATION:
            ctx += "\n\nПользователь ответил на уточняющие вопросы. Если ответов достаточно — дай улучшенную версию. Если нет — задай ещё вопросы (максимум 2 раунда)."
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

        last_user_msg = session.conversation[-1].get("content", "") if session.conversation else ""
        if any(kw in last_user_msg.lower() for kw in ["режим обучения", "вернуться к обучению", "вернуться к урокам", "перейти в режим обучения"]):
            ctx += "\n\nПользователь переключился в режим обучения. Дай задание текущего модуля. НЕ продолжай прошлую тему из Prompt Up."

    if eval_context:
        ctx += f"\n\nРЕЗУЛЬТАТ ОЦЕНКИ:\n{eval_context}"

    if score is not None:
        ctx += f"\n\nФАКТИЧЕСКИЙ БАЛЛ: {score}/10"

    return ctx


def get_agent_config(agent_name: str, user_context: str = "") -> tuple[str, float, int]:
    if agent_name == "PROFILER":
        from app.prompts.profiler_prompt import PROFILER_SYSTEM_PROMPT
        return PROFILER_SYSTEM_PROMPT, 0.5, 250
    from app.prompts.tutor_prompt import TUTOR_SYSTEM_PROMPT
    system = TUTOR_SYSTEM_PROMPT
    if user_context:
        system += f"\n\nДАННЫЕ ПОЛЬЗОВАТЕЛЯ: {user_context}"
    return system, 0.6, 600
