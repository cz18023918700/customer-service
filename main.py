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

import logging
import sys
import uuid
from pathlib import Path

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from config import config
from engine.chat import chat, get_session_history
from knowledge.loader import load_knowledge_base
from models.db import (
    init_db, save_message, get_conversation_stats, get_recent_conversations,
    get_conversation_messages, get_hot_questions, get_human_transfer_list,
    save_feedback, get_feedback_stats,
)
from wecom.callback import verify_callback, parse_message, send_text_reply, notify_human

# 日志配置
logging.basicConfig(
    level=logging.DEBUG if config.DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

app = FastAPI(title="静享时空 AI 客服", version="1.0.0")

# 静态文件
static_dir = Path(__file__).parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


@app.on_event("startup")
async def startup():
    """启动时初始化"""
    logger.info("正在初始化数据库...")
    init_db()
    logger.info("正在加载知识库...")
    count = load_knowledge_base()
    logger.info(f"知识库加载完成，共 {count} 条文档片段")
    logger.info(f"静享时空 AI 客服启动完成 | http://localhost:{config.PORT}")


# ============ Web 对话接口 ============

@app.post("/chat")
async def web_chat(request: Request):
    """Web 端对话"""
    data = await request.json()
    user_message = data.get("message", "").strip()
    session_id = data.get("session_id", str(uuid.uuid4()))

    if not user_message:
        return JSONResponse({"error": "消息不能为空"}, status_code=400)

    # 保存用户消息
    save_message(session_id, "user", user_message, channel="web")

    # 对话引擎处理
    result = chat(session_id, user_message)

    # 保存 AI 回复
    save_message(
        session_id, "assistant", result["reply"],
        confidence=result["confidence"],
        need_human=result["need_human"],
        sources=",".join(result["sources"]),
        channel="web",
    )

    return {
        "reply": result["reply"],
        "session_id": session_id,
        "need_human": result["need_human"],
        "confidence": result["confidence"],
        "sources": result["sources"],
        "suggestions": result.get("suggestions", []),
        "from_faq": result.get("from_faq", False),
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

    logger.info(f"收到企微消息 | 用户: {user_id} | 内容: {user_message}")

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
    data = await request.json()
    session_id = data.get("session_id", "")
    message = data.get("message", "")
    rating = data.get("rating", 0)  # 1=👍, -1=👎
    comment = data.get("comment", "")

    if not session_id or not rating:
        return JSONResponse({"error": "参数缺失"}, status_code=400)

    save_feedback(session_id, message, rating, comment)
    return {"message": "感谢反馈"}


@app.get("/api/stats")
async def api_stats():
    """对话统计（含满意度）"""
    stats = get_conversation_stats()
    stats["feedback"] = get_feedback_stats()
    return stats


@app.get("/api/history")
async def api_history(limit: int = Query(20)):
    """最近对话"""
    return get_recent_conversations(limit)


@app.get("/api/session/{session_id}")
async def api_session(session_id: str):
    """获取某个会话的完整对话记录（从 DB）"""
    messages = get_conversation_messages(session_id)
    return {"session_id": session_id, "messages": messages}


@app.get("/api/hot-questions")
async def api_hot_questions(limit: int = Query(10)):
    """高频问题统计"""
    return get_hot_questions(limit)


@app.get("/api/human-transfers")
async def api_human_transfers(limit: int = Query(20)):
    """需要人工介入的对话"""
    return get_human_transfer_list(limit)


@app.post("/api/reload-kb")
async def api_reload_kb():
    """重新加载知识库"""
    count = load_knowledge_base(force_reload=True)
    return {"message": f"知识库重新加载完成，共 {count} 条片段"}


@app.get("/api/kb/list")
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


@app.get("/api/kb/{filename}")
async def api_kb_read(filename: str):
    """读取知识库文档内容"""
    file_path = Path(config.KNOWLEDGE_DIR) / filename
    if not file_path.exists() or not file_path.suffix == ".md":
        return JSONResponse({"error": "文档不存在"}, status_code=404)
    content = file_path.read_text(encoding="utf-8")
    return {"filename": filename, "content": content}


@app.put("/api/kb/{filename}")
async def api_kb_update(filename: str, request: Request):
    """更新知识库文档"""
    file_path = Path(config.KNOWLEDGE_DIR) / filename
    if not file_path.exists() or not file_path.suffix == ".md":
        return JSONResponse({"error": "文档不存在"}, status_code=404)
    data = await request.json()
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
    html_path = Path(__file__).parent / "static" / "index.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "<h1>静享时空 AI 客服</h1><p>请创建 static/index.html</p>"


@app.get("/admin", response_class=HTMLResponse)
async def admin():
    """管理后台"""
    html_path = Path(__file__).parent / "static" / "admin.html"
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "<h1>管理后台未找到</h1>"


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "main:app",
        host=config.HOST,
        port=config.PORT,
        reload=config.DEBUG,
    )
