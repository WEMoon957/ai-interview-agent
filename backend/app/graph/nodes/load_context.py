"""load_context 节点：初始化面试进度并写入开场白。"""
from langchain_core.messages import AIMessage

from app.graph.state import InterviewState


def load_context(state: InterviewState) -> dict:
    """加载面试上下文：重置进度、置 running、写开场白。

    该节点在每次面试开始时执行（首次取上下文时触发）。
    注意：DB 层 session.status 的 running 由 API 层同步更新（A9）。
    """
    candidate = state.get("candidate") or {}
    name = candidate.get("name") or "候选人"

    opening = (
        f"{name}，你好！欢迎参加本次 AI 面试。"
        "接下来我会围绕岗位要求逐题向你提问，请尽量结合真实经历具体作答。"
        "部分问题我可能会根据你的回答进行追问，请保持放松，正常发挥即可。"
    )

    return {
        "current_idx": 0,
        "follow_up_count": 0,
        "status": "running",
        "messages": [AIMessage(content=opening)],
    }
