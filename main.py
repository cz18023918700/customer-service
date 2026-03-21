"""静享时空 AI 客服系统 - 主入口

端口: 8900
- /chat           POST  Web 端对话接口
- /wecom/callback GET   企微回调验证
- /wecom/callback POST  企微消息接收
- /api/stats      GET   数据统计
- /api/history    GET   最近对话
- /api/reload-kb  POST  重载知识库
- /               GET   Web 测试界面
"""

import json
import logging
import sys
import time
import uuid
from pathlib import Path

from fastapi import FastAPI, Request, Query, Depends, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import config
from engine.chat import chat, get_session_history
from knowledge.loader import load_knowledge_base
from models.db import (
    init_db, save_message, get_conversation_stats, get_recent_conversations,
    get_conversation_messages, get_hot_questions, get_human_transfer_list,
    save_feedback, get_feedback_stats, export_messages_csv, get_daily_trend,
    save_faq_miss, get_faq_misses, search_conversations, get_response_time_stats,
    get_peak_hours, get_active_sessions_count, get_pending_human_count,
    close_stale_conversations, get_human_queue, save_human_reply,
    auto_tag_conversation,
)
from wecom.callback import verify_callback, parse_message, send_text_reply, notify_human

# 日志配置
_log_handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
if not config.DEBUG:
    # 生产模式：额外写文件，10MB 轮转，保留 5 个
    from logging.handlers import RotatingFileHandler
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    file_handler = RotatingFileHandler(
        str(log_dir / "service.log"), maxBytes=10 * 1024 * 1024,
        backupCount=5, encoding="utf-8",
    )
    file_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s"))
    _log_handlers.append(file_handler)

logging.basicConfig(
    level=logging.DEBUG if config.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=_log_handlers,
)
logger = logging.getLogger(__name__)

app = FastAPI(title="静享时空 AI 客服", version="1.0.0")

# ============ 限流器 ============
_rate_limits: dict[str, list[float]] = {}  # {key: [timestamps]}
_rate_limit_last_cleanup = 0.0
RATE_LIMIT_MAX = 20  # 每分钟最多请求数
RATE_LIMIT_WINDOW = 60  # 窗口秒数


def check_rate_limit(key: str) -> bool:
    """检查是否超限，返回 True 表示允许"""
    global _rate_limit_last_cleanup
    now = time.time()

    # 每 5 分钟清理无活动的 key，防止内存泄漏
    if now - _rate_limit_last_cleanup > 300:
        _rate_limit_last_cleanup = now
        stale = [k for k, v in _rate_limits.items() if not v or now - v[-1] > RATE_LIMIT_WINDOW]
        for k in stale:
            del _rate_limits[k]

    if key not in _rate_limits:
        _rate_limits[key] = []

    _rate_limits[key] = [t for t in _rate_limits[key] if now - t < RATE_LIMIT_WINDOW]

    if len(_rate_limits[key]) >= RATE_LIMIT_MAX:
        return False

    _rate_limits[key].append(now)
    return True


# ============ HTML 缓存 ============
_html_cache: dict[str, str] = {}


def _serve_html(filename: str, fallback: str = "<h1>页面未找到</h1>") -> HTMLResponse:
    """从 static/ 读取 HTML 文件（DEBUG 模式不缓存）"""
    if config.DEBUG or filename not in _html_cache:
        html_path = Path(__file__).parent / "static" / filename
        if html_path.exists():
            _html_cache[filename] = html_path.read_text(encoding="utf-8")
        else:
            return HTMLResponse(fallback)
    return HTMLResponse(_html_cache[filename])


def verify_admin(request: Request) -> None:
    """管理接口认证：ADMIN_TOKEN 非空时，要求 header 或 query 带 token"""
    token = config.ADMIN_TOKEN
    if not token:
        return  # 未配置则不认证（本地开发模式）

    # 从 header 或 query 获取 token
    req_token = request.headers.get("X-Admin-Token", "") or request.query_params.get("token", "")
    if req_token != token:
        raise HTTPException(status_code=403, detail="管理权限不足")

# 静态文件
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.on_event("startup")
async def startup():
    """启动时初始化"""
    # 配置校验
    if not config.DEEPSEEK_API_KEY:
        logger.error("缺少 DEEPSEEK_API_KEY，LLM 功能不可用")
    if not config.WECOM_CORP_ID:
        logger.warning("未配置企业微信，WeChat 接入不可用")
    if not config.ADMIN_TOKEN:
        logger.warning("未设置 ADMIN_TOKEN，管理后台无认证保护")

    logger.info("正在初始化数据库...")
    init_db()
    logger.info("正在加载知识库...")
    count = load_knowledge_base()
    logger.info(f"知识库加载完成，共 {count} 条文档片段")

    # 清理过期会话
    closed = close_stale_conversations(hours=2)
    if closed:
        logger.info(f"清理 {closed} 个过期会话")

    # 启动自检
    _self_check()

    logger.info(f"静享时空 AI 客服启动完成 | http://localhost:{config.PORT}")
    logger.info(f"管理后台: http://localhost:{config.PORT}/admin")


def _self_check():
    """启动自检：验证 FAQ 和 RAG 核心功能"""
    from engine.faq import match_faq
    from knowledge.loader import query_knowledge

    checks = [
        ("FAQ-价格", lambda: match_faq("大包厢多少钱") is not None),
        ("FAQ-预约", lambda: match_faq("怎么预约") is not None),
        ("FAQ-会员", lambda: match_faq("会员优惠") is not None),
        ("RAG-检索", lambda: len(query_knowledge("包厢价格", top_k=1)) > 0),
    ]
    passed = 0
    for name, check_fn in checks:
        try:
            if check_fn():
                passed += 1
            else:
                logger.error(f"自检失败: {name}")
        except Exception as e:
            logger.error(f"自检异常: {name} - {e}")

    if passed == len(checks):
        logger.info(f"自检通过: {passed}/{len(checks)}")
    else:
        logger.warning(f"自检部分失败: {passed}/{len(checks)}，请检查知识库和FAQ")


@app.get("/health")
async def health():
    """健康检查"""
    return {
        "status": "healthy",
        "timestamp": time.time(),
        "has_deepseek": bool(config.DEEPSEEK_API_KEY),
        "has_wecom": bool(config.WECOM_CORP_ID),
    }


@app.get("/status", dependencies=[Depends(verify_admin)])
async def status():
    """系统状态汇总（一页看全局）"""
    import platform

    stats = get_conversation_stats()
    fb = get_feedback_stats()
    rt = get_response_time_stats()
    db_path = Path(__file__).parent / "customer_service.db"
    db_size = db_path.stat().st_size / 1024 if db_path.exists() else 0

    return {
        "system": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "port": config.PORT,
            "debug": config.DEBUG,
            "has_deepseek": bool(config.DEEPSEEK_API_KEY),
            "has_wecom": bool(config.WECOM_CORP_ID),
            "has_admin_token": bool(config.ADMIN_TOKEN),
        },
        "conversations": {
            "total": stats["total_conversations"],
            "today": stats["today_conversations"],
            "active": get_active_sessions_count(),
            "pending_human": get_pending_human_count(),
        },
        "messages": {
            "total": stats["total_messages"],
            "today": stats["today_messages"],
            "faq_replies": stats["faq_replies"],
            "llm_replies": stats["llm_replies"],
            "human_transfers": stats["human_transfers"],
        },
        "quality": {
            "satisfaction_rate": fb["satisfaction_rate"],
            "feedback_total": fb["total"],
            "avg_response_ms": rt["avg_ms"],
            "faq_rate": round(stats["faq_replies"] / max(stats["faq_replies"] + stats["llm_replies"], 1) * 100, 1),
        },
        "storage": {
            "db_size_kb": round(db_size, 1),
        },
    }


# ============ Web 对话接口 ============

@app.post("/chat")
async def web_chat(request: Request):
    """Web 端对话"""
    body = await request.body()
    data = json.loads(body.decode("utf-8", errors="replace"))
    user_message = data.get("message", "").strip()
    session_id = data.get("session_id", str(uuid.uuid4()))

    if not user_message:
        return JSONResponse({"error": "消息不能为空"}, status_code=400)

    # 限流检查
    if not check_rate_limit(session_id):
        return JSONResponse({"error": "请求太频繁，请稍后再试"}, status_code=429)

    # 保存用户消息
    save_message(session_id, "user", user_message, channel="web")

    # 对话引擎处理
    result = chat(session_id, user_message)

    elapsed_ms = result.get("elapsed_ms", 0)

    # FAQ 未命中记录
    if not result.get("from_faq"):
        save_faq_miss(user_message)

    # 自动打标签（仅在对话有足够内容时，避免每次重复写入）
    if result.get("need_human") or not result.get("from_faq"):
        auto_tag_conversation(session_id)

    # 保存 AI 回复
    msg_id = save_message(
        session_id, "assistant", result["reply"],
        confidence=result["confidence"],
        need_human=result["need_human"],
        sources=",".join(result["sources"]),
        channel="web",
        elapsed_ms=elapsed_ms,
    )

    return {
        "reply": result["reply"],
        "message_id": msg_id,
        "session_id": session_id,
        "need_human": result["need_human"],
        "confidence": result["confidence"],
        "sources": result["sources"],
        "suggestions": result.get("suggestions", []),
        "from_faq": result.get("from_faq", False),
        "elapsed_ms": elapsed_ms,
    }


# ============ 企业微信回调 ============

@app.get("/wecom/callback")
async def wecom_verify(
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
    echostr: str = Query(...),
):
    """企微回调 URL 验证"""
    try:
        echo = verify_callback(msg_signature, timestamp, nonce, echostr)
        return PlainTextResponse(echo)
    except Exception as e:
        logger.error(f"企微回调验证失败: {e}")
        return PlainTextResponse("验证失败", status_code=403)


@app.post("/wecom/callback")
async def wecom_message(
    request: Request,
    msg_signature: str = Query(...),
    timestamp: str = Query(...),
    nonce: str = Query(...),
):
    """接收企微推送的消息"""
    body = await request.body()
    body_str = body.decode("utf-8", errors="replace")

    msg = parse_message(msg_signature, timestamp, nonce, body_str)
    if not msg:
        return PlainTextResponse("success")

    # 只处理文本消息
    if msg["msg_type"] != "text" or not msg["content"]:
        return PlainTextResponse("success")

    user_id = msg["from_user"]
    user_message = msg["content"]

    masked_uid = user_id[:4] + "***" if len(user_id) > 4 else "***"
    logger.info(f"收到企微消息 | 用户: {masked_uid} | 长度: {len(user_message)}")

    # 保存用户消息
    save_message(user_id, "user", user_message, channel="wecom")

    # 对话引擎
    result = chat(user_id, user_message)

    # 保存 AI 回复
    save_message(
        user_id, "assistant", result["reply"],
        confidence=result["confidence"],
        need_human=result["need_human"],
        sources=",".join(result["sources"]),
        channel="wecom",
    )

    # 异步发送回复
    send_text_reply(user_id, result["reply"])

    # 需要转人工时通知
    if result["need_human"]:
        notify_human(user_id, user_message, result["reply"])

    return PlainTextResponse("success")


# ============ 管理接口 ============

@app.post("/api/feedback")
async def api_feedback(request: Request):
    """满意度反馈"""
    body = await request.body()
    data = json.loads(body.decode("utf-8", errors="replace"))
    session_id = data.get("session_id", "")
    message_id = data.get("message_id", 0)
    rating = data.get("rating", 0)  # 1=👍, -1=👎
    comment = data.get("comment", "")

    if not session_id or not rating:
        return JSONResponse({"error": "参数缺失"}, status_code=400)

    save_feedback(session_id, int(message_id), rating, comment)
    return {"message": "感谢反馈"}


@app.get("/api/stats", dependencies=[Depends(verify_admin)])
async def api_stats():
    """对话统计（含满意度）"""
    stats = get_conversation_stats()
    stats["feedback"] = get_feedback_stats()
    stats["response_time"] = get_response_time_stats()
    stats["active_sessions"] = get_active_sessions_count()
    stats["pending_human"] = get_pending_human_count()
    return stats


@app.get("/api/history", dependencies=[Depends(verify_admin)])
async def api_history(limit: int = Query(20)):
    """最近对话"""
    return get_recent_conversations(limit)


@app.get("/api/session/{session_id}", dependencies=[Depends(verify_admin)])
async def api_session(session_id: str):
    """获取某个会话的完整对话记录（从 DB）"""
    messages = get_conversation_messages(session_id)
    return {"session_id": session_id, "messages": messages}


@app.get("/api/hot-questions", dependencies=[Depends(verify_admin)])
async def api_hot_questions(limit: int = Query(10)):
    """高频问题统计"""
    return get_hot_questions(limit)


@app.get("/api/human-transfers", dependencies=[Depends(verify_admin)])
async def api_human_transfers(limit: int = Query(20)):
    """需要人工介入的对话"""
    return get_human_transfer_list(limit)


@app.get("/api/faq-misses", dependencies=[Depends(verify_admin)])
async def api_faq_misses(limit: int = Query(20)):
    """FAQ 未命中的高频问题（发现新规则机会）"""
    return get_faq_misses(limit)


@app.get("/api/search", dependencies=[Depends(verify_admin)])
async def api_search(q: str = Query(""), limit: int = Query(20)):
    """搜索对话"""
    if not q.strip():
        return []
    return search_conversations(q.strip(), limit)


@app.get("/api/response-time", dependencies=[Depends(verify_admin)])
async def api_response_time():
    """响应时间统计"""
    return get_response_time_stats()


@app.get("/api/peak-hours", dependencies=[Depends(verify_admin)])
async def api_peak_hours():
    """高峰时段分析"""
    return get_peak_hours()


@app.get("/api/live")
async def api_live():
    """实时状态（活跃会话 + 待处理转人工）"""
    return {
        "active_sessions": get_active_sessions_count(),
        "pending_human": get_pending_human_count(),
    }


# ============ 人工客服工作台 ============

@app.get("/api/human-queue", dependencies=[Depends(verify_admin)])
async def api_human_queue():
    """待人工处理的对话队列"""
    return get_human_queue()


@app.post("/api/human-reply", dependencies=[Depends(verify_admin)])
async def api_human_reply(request: Request):
    """人工客服回复"""
    body = await request.body()
    data = json.loads(body.decode("utf-8", errors="replace"))
    session_id = data.get("session_id", "")
    content = data.get("content", "").strip()
    operator = data.get("operator", "店长")

    if not session_id or not content:
        return JSONResponse({"error": "参数缺失"}, status_code=400)

    msg_id = save_human_reply(session_id, content, operator)
    if not msg_id:
        return JSONResponse({"error": "会话不存在或已关闭"}, status_code=404)

    return {"message": "回复已发送", "message_id": msg_id}


@app.get("/workbench", response_class=HTMLResponse)
async def workbench():
    """人工客服工作台页面"""
    return _serve_html("workbench.html")


@app.get("/api/export-csv", dependencies=[Depends(verify_admin)])
async def api_export_csv():
    """导出对话记录为 CSV"""
    from fastapi.responses import Response
    csv_data = export_messages_csv()
    return Response(
        content=csv_data,
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=conversations.csv"},
    )


@app.get("/api/trend", dependencies=[Depends(verify_admin)])
async def api_trend(days: int = Query(7)):
    """每日趋势数据"""
    return get_daily_trend(min(days, 30))


@app.get("/api/greeting")
async def api_greeting(session_id: str = Query("")):
    """智能欢迎语 — 根据时段和是否回头客返回不同问候"""
    from engine.constants import get_greeting_prefix
    period_greeting = get_greeting_prefix()

    # 检查是否回头客
    is_returning = False
    if session_id:
        msgs = get_conversation_messages(session_id)
        is_returning = len(msgs) > 0

    if is_returning:
        greeting = f"{period_greeting}！欢迎回来~ 😊 上次聊到哪了？有什么新问题随时问我！"
    else:
        tip = {
            "早上好": "上午时段包厢价格最实惠哦~",
            "中午好": "下午时段包厢也很划算~",
            "下午好": "下午时段包厢很划算，要不要来一场？",
            "晚上好": "晚上是唱歌的黄金时段，大包厢等你来嗨~",
            "夜深了": "深夜也照常营业，包厢随时可以订~",
        }.get(period_greeting, "")
        greeting = f"{period_greeting}！我是静享时空的小助手「小享」~ 😊\n{tip}\n有什么想了解的随时问我！"

    return {"greeting": greeting, "is_returning": is_returning}


@app.post("/api/reload-kb", dependencies=[Depends(verify_admin)])
async def api_reload_kb():
    """重新加载知识库"""
    count = load_knowledge_base(force_reload=True)
    return {"message": f"知识库重新加载完成，共 {count} 条片段"}


@app.get("/api/kb/list", dependencies=[Depends(verify_admin)])
async def api_kb_list():
    """列出知识库文档"""
    docs_dir = Path(config.KNOWLEDGE_DIR)
    docs = []
    for f in sorted(docs_dir.glob("*.md")):
        content = f.read_text(encoding="utf-8")
        docs.append({
            "name": f.stem,
            "filename": f.name,
            "size": len(content),
            "lines": content.count("\n") + 1,
        })
    return docs


@app.get("/api/kb/{filename}", dependencies=[Depends(verify_admin)])
async def api_kb_read(filename: str):
    """读取知识库文档内容"""
    file_path = Path(config.KNOWLEDGE_DIR) / filename
    if not file_path.exists() or not file_path.suffix == ".md":
        return JSONResponse({"error": "文档不存在"}, status_code=404)
    content = file_path.read_text(encoding="utf-8")
    return {"filename": filename, "content": content}


@app.put("/api/kb/{filename}", dependencies=[Depends(verify_admin)])
async def api_kb_update(filename: str, request: Request):
    """更新知识库文档"""
    file_path = Path(config.KNOWLEDGE_DIR) / filename
    if not file_path.exists() or not file_path.suffix == ".md":
        return JSONResponse({"error": "文档不存在"}, status_code=404)
    body = await request.body()
    data = json.loads(body.decode("utf-8", errors="replace"))
    content = data.get("content", "")
    if not content.strip():
        return JSONResponse({"error": "内容不能为空"}, status_code=400)

    # 原子写入
    tmp_path = file_path.with_suffix(".md.tmp")
    tmp_path.write_text(content, encoding="utf-8")
    tmp_path.replace(file_path)

    # 重新加载知识库
    count = load_knowledge_base(force_reload=True)
    return {"message": f"文档已更新，知识库重载 {count} 条片段"}


# ============ Web 界面 ============

@app.get("/", response_class=HTMLResponse)
async def index():
    """Web 测试聊天界面"""
    return _serve_html("index.html")


@app.get("/admin", response_class=HTMLResponse)
async def admin():
    """管理后台"""
    return _serve_html("admin.html")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=config.HOST,
        port=config.PORT,
        reload=config.DEBUG,
    )
