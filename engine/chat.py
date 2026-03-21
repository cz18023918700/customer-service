"""对话引擎 - 核心对话处理流程"""

import logging
import time
from collections import defaultdict
from datetime import datetime

from openai import OpenAI

from config import config
from engine.prompt import build_system_prompt
from engine.faq import match_faq
from knowledge.loader import query_knowledge
from models.db import get_conversation_messages

logger = logging.getLogger(__name__)

# 内存缓存（热会话），带 TTL 过期清理
# {session_id: {"msgs": [...], "last_active": timestamp}}
_sessions: dict[str, dict] = {}
SESSION_TTL = 1800  # 30 分钟无活动过期
SESSION_CLEANUP_INTERVAL = 300  # 每 5 分钟清理一次
_last_cleanup = 0.0

# 默认追问建议（LLM 回复时用）
DEFAULT_SUGGESTIONS = ["包厢价格是多少？", "怎么预约？", "有什么会员优惠？"]


def _cleanup_expired_sessions() -> None:
    """清理过期会话，防止内存泄漏"""
    global _last_cleanup
    now = time.time()
    if now - _last_cleanup < SESSION_CLEANUP_INTERVAL:
        return
    _last_cleanup = now
    expired = [sid for sid, s in _sessions.items() if now - s["last_active"] > SESSION_TTL]
    for sid in expired:
        del _sessions[sid]
    if expired:
        logger.info(f"清理 {len(expired)} 个过期会话，剩余 {len(_sessions)} 个")


def _get_session_msgs(session_id: str) -> list[dict]:
    """获取会话消息列表（带自动初始化和 DB 回退）"""
    _cleanup_expired_sessions()

    if session_id not in _sessions:
        # 从 DB 加载冷数据
        db_msgs = get_conversation_messages(session_id)
        msgs = [{"role": m["role"], "content": m["content"], "ts": m["created_at"]} for m in db_msgs]
        _sessions[session_id] = {"msgs": msgs, "last_active": time.time()}
    else:
        _sessions[session_id]["last_active"] = time.time()

    return _sessions[session_id]["msgs"]


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
            "reply": str,           # AI 回复内容
            "need_human": bool,     # 是否需要转人工
            "sources": list[str],   # 引用的知识库来源
            "confidence": float,    # 置信度 0-1
            "suggestions": list,    # 推荐追问
            "from_faq": bool,       # 是否来自 FAQ 快速回复
        }
    """
    # 0. FAQ 快速匹配（秒回，不走 LLM）
    faq_result = match_faq(user_message)
    if faq_result:
        now = time.time()
        history = _get_session_msgs(session_id)
        history.append({"role": "user", "content": user_message, "ts": now})
        history.append({"role": "assistant", "content": faq_result["reply"], "ts": now})
        return {
            "reply": faq_result["reply"],
            "need_human": False,
            "sources": ["FAQ"],
            "confidence": 1.0,
            "suggestions": faq_result.get("suggestions", []),
            "from_faq": True,
        }

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
    history = _get_session_msgs(session_id)

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
            timeout=30,
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
            "suggestions": DEFAULT_SUGGESTIONS,
            "from_faq": False,
        }

    # 4. 解析 suggestions
    suggestions = DEFAULT_SUGGESTIONS
    if "---suggestions---" in reply:
        parts = reply.split("---suggestions---", 1)
        reply = parts[0].strip()
        raw_suggestions = parts[1].strip().split("\n")
        parsed = [s.strip() for s in raw_suggestions if s.strip()]
        if parsed:
            suggestions = parsed[:3]

    # 5. 判断是否需要转人工
    need_human = _check_need_human(user_message, reply, avg_score)

    if need_human:
        reply += "\n\n💬 我已帮你记录，工作人员会尽快联系你处理~"

    # 6. 保存对话历史
    now = time.time()
    history.append({"role": "user", "content": user_message, "ts": now})
    history.append({"role": "assistant", "content": reply, "ts": now})

    # 限制历史长度
    max_msgs = config.MAX_HISTORY_TURNS * 2
    if len(history) > max_msgs + 10:
        _sessions[session_id]["msgs"] = history[-max_msgs:]

    return {
        "reply": reply,
        "need_human": need_human,
        "sources": sources,
        "confidence": round(avg_score, 2),
        "suggestions": suggestions,
        "from_faq": False,
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
    if session_id in _sessions:
        return list(_sessions[session_id]["msgs"])
    return get_conversation_messages(session_id)
