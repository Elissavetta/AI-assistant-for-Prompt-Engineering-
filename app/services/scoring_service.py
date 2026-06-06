MODULE_NAMES = {
    1: "Структура промпта",
    2: "Улучшение плохого промпта",
    3: "Few-shot prompting",
    4: "Chain-of-thought",
    5: "Добавление контекста",
    6: "Комплексный промпт с нуля",
}

MODULE_BADGES = {
    1: "🏗️ Архитектор промптов",
    2: "🔧 Мастер улучшений",
    3: "🎯 Few-shot эксперт",
    4: "🧠 Мыслитель цепочек",
    5: "📎 Мастер контекста",
    6: "🏆 Промпт-архитектор",
}

LEVEL_THRESHOLDS = {
    "newbie": (0, 99),
    "intermediate": (100, 299),
    "advanced": (300, 9999),
}


def calculate_level(total_score: int) -> str:
    if total_score >= 300:
        return "advanced"
    if total_score >= 100:
        return "intermediate"
    return "newbie"


def get_module_badge(module_id: int, score: int, max_score: int) -> str:
    if score >= 50:
        return MODULE_BADGES.get(module_id, "")
    return ""


def score_to_points(score: int) -> int:
    return score
