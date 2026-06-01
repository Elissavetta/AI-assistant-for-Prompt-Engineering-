MODULE_NAMES = {
    1: "Структура промпта",
    2: "Улучшение плохого промпта",
    3: "Few-shot prompting",
    4: "Chain-of-thought",
    5: "Форматирование и ограничения модели",
    6: "Комплексный промпт с нуля",
}

MODULE_BADGES = {
    1: "🏗️ Архитектор промптов",
    2: "🔧 Мастер улучшений",
    3: "🎯 Few-shot эксперт",
    4: "🧠 Мыслитель цепочек",
    5: "🎨 Формат-дизайнер",
    6: "🏆 Промпт-архитектор",
}

LEVEL_THRESHOLDS = {
    "newbie": (0, 30),
    "intermediate": (31, 70),
    "advanced": (71, 100),
}


def calculate_level(total_score: int) -> str:
    for level, (low, high) in LEVEL_THRESHOLDS.items():
        if low <= total_score <= high:
            return level
    if total_score > 70:
        return "advanced"
    return "newbie"


def get_module_badge(module_id: int, score: int, max_score: int) -> str:
    if score >= max_score * 0.7:
        return MODULE_BADGES.get(module_id, "")
    return ""


def score_to_points(score: int) -> int:
    return score
