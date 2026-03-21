"""对话引擎 - 核心对话处理流程"""

import logging
import time
from collections import defaultdict

from openai import OpenAI

from config import config
from engine.prompt import build_system_prompt
from knowledge.loader import query_knowledge
from models.db import get_conversation_messages

logger = logging.getLogger(__name__)

# 内存缓存（热会话），冷数据从 DB 加载
_sessions: dict[str, list[dict]] = defaultdict(list)


def get_llm_client() -> OpenAI:
    """获取 DeepSeek 客户端"""
    return OpenAI(
        api_key=config.DEEPSEEK_API_KEY,
        base_url=config.DEEPSEEK_BASE_URL,
    )


def chat(session_id: str, user_message: str) -> dict:
    """处理用户消息，返回 AI 回复

    Args:
        session_id: 会话ID（用户唯一标识）
        user_message: 用户消息

    Returns:
        {
            "reply": str,          # AI 回复内容
            "need_human": bool,    # 是否需要转人工
            "sources": list[str],  # 引用的知识库来源
            "confidence": float,   # 置信度 0-1
        }
    """
    # 1. RAG 检索
    rag_results = query_knowledge(user_message)
    context = "\n\n".join(
        f"【{r['source']}】{r['content']}" for r in rag_results
    ) if rag_results else "（未找到相关信息）"

    sources = list({r["source"] for r in rag_results})
    avg_score = sum(r["score"] for r in rag_results) / len(rag_results) if rag_results else 0.0

    # 2. 构建消息列表
    system_prompt = build_system_prompt(context)
    messages = [{"role": "system", "content": system_prompt}]

    # 加入历史对话（内存优先，没有则从 DB 加载）
    history = _sessions[session_id]
    if not history:
        db_msgs = get_conversation_messages(session_id)
        for m in db_msgs:
            history.append({"role": m["role"], "content": m["content"], "ts": m["created_at"]})

    recent = history[-(config.MAX_HISTORY_TURNS * 2):]
    for h in recent:
        messages.append({"role": h["role"], "content": h["content"]})

    messages.append({"role": "user", "content": user_message})

    # 3. 调用 DeepSeek
    try:
        client = get_llm_client()
        response = client.chat.completions.create(
            model=config.DEEPSEEK_MODEL,
            messages=messages,
            temperature=0.7,
            max_tokens=500,
        )
        reply = response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"LLM 调用失败: {e}")
        reply = "不好意思，系统暂时出了点问题，请稍后再试或直接联系工作人员 🙏"
        return {
            "reply": reply,
            "need_human": True,
            "sources": [],
            "confidence": 0.0,
        }

    # 4. 判断是否需要转人工
    need_human = _check_need_human(user_message, reply, avg_score)

    if need_human:
        reply += "\n\n💬 我已帮你记录，工作人员会尽快联系你处理~"

    # 5. 保存对话历史
    now = time.time()
    history.append({"role": "user", "content": user_message, "ts": now})
    history.append({"role": "assistant", "content": reply, "ts": now})

    # 限制历史长度
    if len(history) > config.MAX_HISTORY_TURNS * 2 + 10:
        _sessions[session_id] = history[-(config.MAX_HISTORY_TURNS * 2):]

    return {
        "reply": reply,
        "need_human": need_human,
        "sources": sources,
        "confidence": round(avg_score, 2),
    }


def _check_need_human(user_msg: str, ai_reply: str, rag_score: float) -> bool:
    """判断是否需要转人工"""
    # 关键词触发
    urgent_keywords = ["投诉", "退款", "退钱", "报警", "打人", "受伤", "着火", "漏水", "触电"]
    for kw in urgent_keywords:
        if kw in user_msg:
            return True

    # RAG 置信度太低
    if rag_score < config.TRANSFER_CONFIDENCE_THRESHOLD:
        return True

    # AI 自己说不确定
    uncertain_phrases = ["不太确定", "转给工作人员", "联系人工", "无法确认"]
    for phrase in uncertain_phrases:
        if phrase in ai_reply:
            return True

    return False


def clear_session(session_id: str) -> None:
    """清除会话历史"""
    _sessions.pop(session_id, None)


def get_session_history(session_id: str) -> list[dict]:
    """获取会话历史（内存 + DB 兜底）"""
    history = _sessions.get(session_id, [])
    if history:
        return list(history)
    # 内存没有则从 DB 加载
    return get_conversation_messages(session_id)
