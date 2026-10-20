"""ask_question 节点：首问 / 追问 / 下一题，interrupt 挂起等待回答。"""
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.types import interrupt

from app.exceptions import BizError
from app.graph.route import should_follow_up
from app.graph.state import InterviewState
from app.services.interview_turns import record_turn


def ask_question(state: InterviewState) -> dict:
    """生成当前问题并用 interrupt 挂起，恢复后写入「AI 问题 + 用户回答」。

    关键约定：
    - 首问 / 下一题：使用模板预设问题（questions[current_idx]），不额外调用 LLM；
    - 追问：复用 evaluate 节点已生成并追加到 evaluations 的 follow_up_question；
    - 挂起前的计算必须完全确定性（仅读已提交状态），以保证中断恢复时
      interrupt() 命中同一调用并拿到 resume 值，且计数推进结果一致（共享知识 #10）。
    """
    questions = state.get("questions") or []
    current_idx = state.get("current_idx", 0)
    follow_up_count = state.get("follow_up_count", 0)
    evaluations = state.get("evaluations") or []

    if not questions:
        raise BizError(400, "面试问题集为空，无法开始面试", http_status=400)

    if evaluations and should_follow_up(state):
        # ── 追问：当前题继续，追问次数 +1 ──
        last = evaluations[-1]
        follow_up_count += 1
        question_text = (last.get("follow_up_question") or "").strip() or "请再展开说明一下。"
        question_id = last.get("question_id") or questions[current_idx]["id"]
        q_type = "follow_up"
    elif evaluations:
        # ── 下一题：进入下一题，重置追问次数 ──
        current_idx += 1
        if current_idx >= len(questions):
            current_idx = len(questions) - 1  # 防御性兜底，正常由 route 保证不越界
        follow_up_count = 0
        question_text = questions[current_idx]["text"]
        question_id = questions[current_idx]["id"]
        q_type = "question"
    else:
        # ── 首问 ──
        current_idx = 0
        follow_up_count = 0
        question_text = questions[0]["text"]
        question_id = questions[0]["id"]
        q_type = "question"

    # 当前题的题型与选项（选择题才有；追问恒为主观题）
    current_question = questions[current_idx]
    if q_type == "follow_up":
        question_type = "subjective"
        options: list[str] = []
    else:
        question_type = (current_question.get("type") or "subjective").strip() or "subjective"
        options = list(current_question.get("options") or [])

    # 挂起并把当前问题返回给候选人（payload 也会被 API 层用于响应组装）
    payload = {
        "status": "waiting_answer",
        "type": q_type,  # 流程类型：question / follow_up
        "question_id": question_id,
        "question": question_text,
        "question_type": question_type,  # 题目类型：subjective / choice
        "options": options,  # 选择题选项（主观题为空列表）
        "current_idx": current_idx,
        "total": len(questions),
        "follow_up_count": follow_up_count,
    }
    answer = interrupt(payload)

    # 原话落库（旁路，失败不阻断面试）：把「本轮提问 + 候选人回答」逐字写进 interview_turns。
    # turn_index 由「已答轮数 + 1」推导 —— messages 里每条 HumanMessage 即一轮作答，
    # 开场白是 AIMessage 不计入；唯一约束 (session_id, turn_index) 保证断点恢复重放时幂等。
    answered = sum(
        1 for m in (state.get("messages") or []) if isinstance(m, HumanMessage)
    )
    record_turn(
        session_id=state.get("session_id"),
        turn_index=answered + 1,
        question_id=question_id,
        question_type=question_type,
        is_follow_up=q_type == "follow_up",
        question_text=question_text,
        answer_text=str(answer or ""),
    )

    # 恢复后：把「AI 问题 + 用户回答」写入 messages，并提交进度计数
    return {
        "messages": [AIMessage(content=question_text), HumanMessage(content=answer or "")],
        "current_idx": current_idx,
        "follow_up_count": follow_up_count,
    }
