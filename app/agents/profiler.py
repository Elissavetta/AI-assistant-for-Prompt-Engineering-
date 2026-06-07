import re

from app.config import PROFILER_MAX_TURNS, LEVEL_NEWBIE, MARKER_LEVEL


def parse_profile(response: str) -> dict:
    result = {"level": "", "sphere": "", "goals": ""}
    level_match = re.search(r'УРОВЕНЬ:\s*(\w+)', response, re.IGNORECASE)
    if level_match:
        val = level_match.group(1).lower()
        if val in ("newbie", "intermediate", "advanced"):
            result["level"] = val
    sphere_match = re.search(r'СФЕРА:\s*([^\n|]+)', response, re.IGNORECASE)
    if sphere_match:
        result["sphere"] = sphere_match.group(1).strip()
    goals_match = re.search(r'ЦЕЛИ?:\s*([^\n|]+)', response, re.IGNORECASE)
    if goals_match:
        result["goals"] = goals_match.group(1).strip()
    return result


def force_profile_completion(session) -> bool:
    if session.profile.level:
        return False
    assistant_turns = sum(1 for m in session.conversation if m.get("role") == "assistant")
    if assistant_turns >= PROFILER_MAX_TURNS:
        session.profile.level = LEVEL_NEWBIE
        if not session.profile.sphere:
            session.profile.sphere = "общее"
        if not session.profile.goals:
            session.profile.goals = "научиться писать промпты"
        return True
    return False


def update_user_from_profile(session, response: str):
    if MARKER_LEVEL in response.upper():
        profile_data = parse_profile(response)
        if profile_data.get("level"):
            session.profile.level = profile_data["level"]
            session.profile.profiler_done = True
        if profile_data.get("sphere"):
            session.profile.sphere = profile_data["sphere"]
        if profile_data.get("goals"):
            session.profile.goals = profile_data["goals"]
