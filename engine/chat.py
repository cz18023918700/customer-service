"""对话引擎 - 核心对话处理流程"""

import json
import logging
import time

from openai import OpenAI

from config import config
from engine.constants import SUGGESTIONS_SEPARATOR, FAULT_KEYWORDS
from engine.prompt import build_system_prompt
from engine.faq import match_faq
from knowledge.loader import query_knowledge
from models.db import get_conversation_messages

logger = logging.getLogger(__name__)

# 内存缓存（热会话），带 TTL 过期清理
_sessions: dict[str, dict] = {}
SESSION_TTL = 1800
SESSION_CLEANUP_INTERVAL = 300
_last_cleanup = 0.0

DEFAULT_SUGGESTIONS = ["包厢价格是多少？", "怎么预约？", "有什么会员优惠？"]
LLM_ERROR_MSG = "不好意思，系统暂时出了点问题，请稍后再试或直接联系工作人员 🙏"

_llm_client: OpenAI | None = None


def get_llm_client() -> OpenAI:
    """获取 DeepSeek 客户端（单例）"""
    global _llm_client
    if _llm_client is None:
        _llm_client = OpenAI(
            api_key=config.DEEPSEEK_API_KEY,
            base_url=config.DEEPSEEK_BASE_URL,
        )
    return _llm_client


# ============ 内部共享函数 ============

def _cleanup_expired_sessions() -> None:
    """清理过期会话"""
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
    """获取会话消息列表（带 DB 回退）"""
    _cleanup_expired_sessions()
    if session_id not in _sessions:
        db_msgs = get_conversation_messages(session_id)
        msgs = [{"role": m["role"], "content": m["content"], "ts": m["created_at"]} for m in db_msgs]
        _sessions[session_id] = {"msgs": msgs, "last_active": time.time()}
    else:
        _sessions[session_id]["last_active"] = time.time()
    return _sessions[session_id]["msgs"]


def _prepare_context(session_id: str, user_message: str) -> dict:
    """准备对话上下文（FAQ检查 + RAG + 消息列表），chat/chat_stream 共用"""
    # FAQ
    faq_result = match_faq(user_message)
    if faq_result:
        return {"faq": faq_result, "history": _get_session_msgs(session_id)}

    # RAG
    rag_results = query_knowledge(user_message)
    context = "\n\n".join(
        f"【{r['source']}】{r['content']}" for r in rag_results
    ) if rag_results else "（未找到相关信息）"

    sources = list({r["source"] for r in rag_results})
    avg_score = sum(r["score"] for r in rag_results) / len(rag_results) if rag_results else 0.0

    # 消息列表
    system_prompt = build_system_prompt(context)
    messages = [{"role": "system", "content": system_prompt}]
    history = _get_session_msgs(session_id)
    recent = history[-(config.MAX_HISTORY_TURNS * 2):]
    for h in recent:
        messages.append({"role": h["role"], "content": h["content"]})
    messages.append({"role": "user", "content": user_message})

    return {
        "faq": None,
        "messages": messages,
        "history": history,
        "sources": sources,
        "avg_score": avg_score,
    }


def _parse_reply(raw_reply: str) -> tuple[str, list[str]]:
    """解析 LLM 回复，分离 suggestions"""
    suggestions = DEFAULT_SUGGESTIONS
    reply = raw_reply
    if SUGGESTIONS_SEPARATOR in reply:
        parts = reply.split(SUGGESTIONS_SEPARATOR, 1)
        reply = parts[0].strip()
        parsed = [s.strip() for s in parts[1].strip().split("\n") if s.strip()]
        if parsed:
            suggestions = parsed[:3]
    return reply, suggestions


def _finalize(session_id: str, history: list, user_message: str, reply: str) -> None:
    """保存历史 + 裁剪"""
    now = time.time()
    history.append({"role": "user", "content": user_message, "ts": now})
    history.append({"role": "assistant", "content": reply, "ts": now})
    max_msgs = config.MAX_HISTORY_TURNS * 2
    if len(history) > max_msgs + 10:
        _sessions[session_id]["msgs"] = history[-max_msgs:]


def _check_need_human(user_msg: str, ai_reply: str, rag_score: float) -> bool:
    """判断是否需要转人工"""
    urgent_keywords = ["投诉", "退款", "退钱", "报警", "打人", "受伤", "着火", "漏水", "触电"]
    for kw in urgent_keywords:
        if kw in user_msg:
            return True
    if rag_score < config.TRANSFER_CONFIDENCE_THRESHOLD:
        return True
    uncertain_phrases = ["不太确定", "转给工作人员", "联系人工", "无法确认"]
    for phrase in uncertain_phrases:
        if phrase in ai_reply:
            return True
    return False


# ============ 公开接口 ============

def chat(session_id: str, user_message: str) -> dict:
    """非流式对话"""
    start_time = time.time()
    ctx = _prepare_context(session_id, user_message)

    # FAQ 秒回
    if ctx["faq"]:
        faq = ctx["faq"]
        _finalize(session_id, ctx["history"], user_message, faq["reply"])
        return {
            "reply": faq["reply"],
            "need_human": False,
            "sources": ["FAQ"],
            "confidence": 1.0,
            "suggestions": faq.get("suggestions", []),
            "from_faq": True,
            "elapsed_ms": int((time.time() - start_time) * 1000),
        }

    # LLM 调用
    try:
        client = get_llm_client()
        response = client.chat.completions.create(
            model=config.DEEPSEEK_MODEL,
            messages=ctx["messages"],
            temperature=0.7,
            max_tokens=500,
            timeout=30,
        )
        raw_reply = response.choices[0].message.content.strip()
    except Exception as e:
        logger.error(f"LLM 调用失败: {e}")
        return {
            "reply": LLM_ERROR_MSG, "need_human": True, "sources": [],
            "confidence": 0.0, "suggestions": DEFAULT_SUGGESTIONS,
            "from_faq": False, "elapsed_ms": 0,
        }

    reply, suggestions = _parse_reply(raw_reply)
    need_human = _check_need_human(user_message, reply, ctx["avg_score"])
    if need_human:
        reply += "\n\n💬 我已帮你记录，工作人员会尽快联系你处理~"

    _finalize(session_id, ctx["history"], user_message, reply)
    elapsed_ms = int((time.time() - start_time) * 1000)
    logger.info(f"LLM 回复 | session={session_id[:12]} | {elapsed_ms}ms | confidence={ctx['avg_score']:.2f}")

    return {
        "reply": reply, "need_human": need_human, "sources": ctx["sources"],
        "confidence": round(ctx["avg_score"], 2), "suggestions": suggestions,
        "from_faq": False, "elapsed_ms": elapsed_ms,
    }


def chat_stream(session_id: str, user_message: str):
    """流式对话 — 返回 generator"""
    start_time = time.time()
    ctx = _prepare_context(session_id, user_message)

    # FAQ 秒回
    if ctx["faq"]:
        faq = ctx["faq"]
        _finalize(session_id, ctx["history"], user_message, faq["reply"])
        yield json.dumps({
            "type": "faq", "reply": faq["reply"],
            "suggestions": faq.get("suggestions", []), "need_human": False,
        }, ensure_ascii=False)
        return

    # 流式 LLM
    full_reply = ""
    try:
        client = get_llm_client()
        stream = client.chat.completions.create(
            model=config.DEEPSEEK_MODEL,
            messages=ctx["messages"],
            temperature=0.7, max_tokens=500, timeout=30, stream=True,
        )
        for chunk in stream:
            delta = chunk.choices[0].delta
            if delta.content:
                full_reply += delta.content
                yield json.dumps({"type": "chunk", "content": delta.content}, ensure_ascii=False)
    except Exception as e:
        logger.error(f"LLM 流式调用失败: {e}")
        full_reply = LLM_ERROR_MSG
        yield json.dumps({"type": "chunk", "content": full_reply}, ensure_ascii=False)

    reply, suggestions = _parse_reply(full_reply)
    need_human = _check_need_human(user_message, reply, ctx["avg_score"])
    _finalize(session_id, ctx["history"], user_message, reply)
    elapsed_ms = int((time.time() - start_time) * 1000)

    yield json.dumps({
        "type": "done", "reply": reply, "need_human": need_human,
        "sources": ctx["sources"], "confidence": round(ctx["avg_score"], 2),
        "suggestions": suggestions, "elapsed_ms": elapsed_ms,
    }, ensure_ascii=False)


def clear_session(session_id: str) -> None:
    _sessions.pop(session_id, None)


def get_session_history(session_id: str) -> list[dict]:
    if session_id in _sessions:
        return list(_sessions[session_id]["msgs"])
    return get_conversation_messages(session_id)
