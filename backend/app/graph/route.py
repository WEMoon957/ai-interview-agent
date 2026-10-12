"""条件边 route：追问 / 下一题 / 收尾 分流。"""
from app.graph.state import InterviewState


def _get_max_follow_ups(state: InterviewState) -> int:
    """读取追问上限：0 表示禁止追问（合法值），仅缺省/None 时回落默认 2。"""
    value = state.get("max_follow_ups")
    if value is None:
        return 2
    return int(value)


def should_follow_up(state: InterviewState) -> bool:
    """判断是否需要追问（与 route 分流条件保持一致）。

    条件：最近一次评分标记 follow_up_needed，且当前题追问次数未达上限。
    max_follow_ups=0 时恒为 False（彻底禁止追问）。
    """
    evaluations = state.get("evaluations") or []
    if not evaluations:
        return False
    last = evaluations[-1]
    follow_up_count = state.get("follow_up_count", 0) or 0
    max_follow_ups = _get_max_follow_ups(state)
    return bool(last.get("follow_up_needed")) and follow_up_count < max_follow_ups


def route(state: InterviewState) -> str:
    """条件边分流逻辑（共享知识 #9）。

    Returns:
        "ask_question"：追问或下一题；"final_report"：题目完成收尾。
    """
    if should_follow_up(state):
        return "ask_question"  # 追问

    current_idx = state.get("current_idx", 0)
    questions = state.get("questions") or []
    if current_idx + 1 < len(questions):
        return "ask_question"  # 下一题

    return "final_report"  # 收尾
