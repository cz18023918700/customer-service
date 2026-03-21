"""数据库模型 - SQLite 存储会话和消息记录"""

import sqlite3
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "customer_service.db"


@contextmanager
def get_db():
    """获取数据库连接（上下文管理器，自动关闭）"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """初始化数据库表"""
    with get_db() as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                channel TEXT DEFAULT 'wecom',
                status TEXT DEFAULT 'active',
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                conversation_id INTEGER NOT NULL,
                session_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                confidence REAL DEFAULT 0,
                need_human INTEGER DEFAULT 0,
                sources TEXT DEFAULT '',
                created_at REAL NOT NULL,
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            );

            CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                message_id INTEGER,
                rating INTEGER,
                comment TEXT DEFAULT '',
                created_at REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS faq_misses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                created_at REAL NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_id);
            CREATE INDEX IF NOT EXISTS idx_conv_updated ON conversations(updated_at);
            CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id);
            CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id);
            CREATE INDEX IF NOT EXISTS idx_msg_created ON messages(created_at);
            CREATE INDEX IF NOT EXISTS idx_fb_created ON feedback(created_at);
        """)
        # 安全加列（已有表不报错）
        for col_sql in [
            "ALTER TABLE messages ADD COLUMN elapsed_ms INTEGER DEFAULT 0",
            "ALTER TABLE conversations ADD COLUMN tags TEXT DEFAULT ''",
            "ALTER TABLE conversations ADD COLUMN assigned_to TEXT DEFAULT ''",
        ]:
            try:
                conn.execute(col_sql)
            except sqlite3.OperationalError:
                pass
        conn.commit()
        logger.info("数据库初始化完成")


def save_message(session_id: str, role: str, content: str,
                 confidence: float = 0, need_human: bool = False,
                 sources: str = "", channel: str = "web",
                 elapsed_ms: int = 0) -> int:
    """保存消息记录"""
    now = time.time()
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM conversations WHERE session_id = ? AND status = 'active'",
            (session_id,)
        ).fetchone()

        if row:
            conv_id = row["id"]
            conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conv_id))
        else:
            cursor = conn.execute(
                "INSERT INTO conversations (session_id, user_id, channel, status, created_at, updated_at) VALUES (?, ?, ?, 'active', ?, ?)",
                (session_id, session_id, channel, now, now)
            )
            conv_id = cursor.lastrowid

        cursor = conn.execute(
            "INSERT INTO messages (conversation_id, session_id, role, content, confidence, need_human, sources, created_at, elapsed_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (conv_id, session_id, role, content, confidence, int(need_human), sources, now, elapsed_ms)
        )
        conn.commit()
        return cursor.lastrowid


def get_conversation_stats() -> dict:
    """获取对话统计"""
    with get_db() as conn:
        total_conv = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        total_msg = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        human_transfers = conn.execute("SELECT COUNT(*) FROM messages WHERE need_human = 1").fetchone()[0]

        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        today_conv = conn.execute(
            "SELECT COUNT(*) FROM conversations WHERE created_at >= ?", (today_start,)
        ).fetchone()[0]
        today_msg = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE created_at >= ?", (today_start,)
        ).fetchone()[0]

        # FAQ vs LLM 统计
        faq_count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE role = 'assistant' AND sources = 'FAQ'"
        ).fetchone()[0]
        llm_count = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE role = 'assistant' AND sources != 'FAQ' AND sources != ''"
        ).fetchone()[0]

        return {
            "total_conversations": total_conv,
            "total_messages": total_msg,
            "human_transfers": human_transfers,
            "today_conversations": today_conv,
            "today_messages": today_msg,
            "faq_replies": faq_count,
            "llm_replies": llm_count,
        }


def get_recent_conversations(limit: int = 20) -> list[dict]:
    """获取最近的对话列表"""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT c.id, c.session_id, c.channel, c.status, c.created_at, c.updated_at,
                   COUNT(m.id) as msg_count,
                   MAX(CASE WHEN m.need_human = 1 THEN 1 ELSE 0 END) as has_human_transfer,
                   (SELECT content FROM messages WHERE conversation_id = c.id AND role = 'user' ORDER BY created_at LIMIT 1) as first_msg
            FROM conversations c
            LEFT JOIN messages m ON m.conversation_id = c.id
            GROUP BY c.id
            ORDER BY c.updated_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


def get_conversation_messages(session_id: str) -> list[dict]:
    """获取某个会话的所有消息（从 DB）"""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT role, content, confidence, need_human, sources, created_at, elapsed_ms
            FROM messages
            WHERE session_id = ?
            ORDER BY created_at ASC
        """, (session_id,)).fetchall()
        return [dict(r) for r in rows]


def get_hot_questions(limit: int = 10) -> list[dict]:
    """统计高频问题"""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT content, COUNT(*) as cnt
            FROM messages
            WHERE role = 'user'
            GROUP BY content
            ORDER BY cnt DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


def save_feedback(session_id: str, message_id: int, rating: int, comment: str = "") -> int:
    """保存满意度反馈 (rating: 1=👍, -1=👎)"""
    now = time.time()
    with get_db() as conn:
        cursor = conn.execute(
            "INSERT INTO feedback (session_id, message_id, rating, comment, created_at) VALUES (?, ?, ?, ?, ?)",
            (session_id, message_id, rating, comment, now)
        )
        conn.commit()
        return cursor.lastrowid


def get_feedback_stats() -> dict:
    """获取反馈统计"""
    with get_db() as conn:
        total = conn.execute("SELECT COUNT(*) FROM feedback").fetchone()[0]
        positive = conn.execute("SELECT COUNT(*) FROM feedback WHERE rating > 0").fetchone()[0]
        negative = conn.execute("SELECT COUNT(*) FROM feedback WHERE rating < 0").fetchone()[0]
        return {
            "total": total,
            "positive": positive,
            "negative": negative,
            "satisfaction_rate": round(positive / total * 100, 1) if total > 0 else 0,
        }


def get_human_transfer_list(limit: int = 20) -> list[dict]:
    """获取需要人工介入的对话"""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT m.session_id, m.content as user_msg, m.created_at,
                   (SELECT content FROM messages WHERE session_id = m.session_id AND role = 'assistant'
                    ORDER BY created_at DESC LIMIT 1) as ai_reply
            FROM messages m
            WHERE m.need_human = 1 AND m.role = 'assistant'
            ORDER BY m.created_at DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


def get_daily_trend(days: int = 7) -> list[dict]:
    """获取最近 N 天的每日趋势数据"""
    with get_db() as conn:
        result = []
        for i in range(days - 1, -1, -1):
            day = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=i)
            day_start = day.timestamp()
            day_end = day_start + 86400

            convs = conn.execute(
                "SELECT COUNT(*) FROM conversations WHERE created_at >= ? AND created_at < ?",
                (day_start, day_end)
            ).fetchone()[0]
            msgs = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE created_at >= ? AND created_at < ?",
                (day_start, day_end)
            ).fetchone()[0]
            human = conn.execute(
                "SELECT COUNT(*) FROM messages WHERE need_human = 1 AND created_at >= ? AND created_at < ?",
                (day_start, day_end)
            ).fetchone()[0]
            fb_pos = conn.execute(
                "SELECT COUNT(*) FROM feedback WHERE rating > 0 AND created_at >= ? AND created_at < ?",
                (day_start, day_end)
            ).fetchone()[0]
            fb_neg = conn.execute(
                "SELECT COUNT(*) FROM feedback WHERE rating < 0 AND created_at >= ? AND created_at < ?",
                (day_start, day_end)
            ).fetchone()[0]

            result.append({
                "date": day.strftime("%m-%d"),
                "conversations": convs,
                "messages": msgs,
                "human_transfers": human,
                "feedback_positive": fb_pos,
                "feedback_negative": fb_neg,
            })
        return result


def export_messages_csv() -> str:
    """导出所有对话为 CSV 格式字符串"""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT m.session_id, c.channel, m.role, m.content, m.confidence,
                   m.need_human, m.sources, m.created_at
            FROM messages m
            JOIN conversations c ON c.id = m.conversation_id
            ORDER BY m.created_at ASC
        """).fetchall()

    import csv
    import io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["session_id", "channel", "role", "content", "confidence", "need_human", "sources", "timestamp"])
    for r in rows:
        ts = datetime.fromtimestamp(r["created_at"]).strftime("%Y-%m-%d %H:%M:%S")
        writer.writerow([r["session_id"], r["channel"], r["role"], r["content"],
                         r["confidence"], r["need_human"], r["sources"], ts])
    return output.getvalue()


def save_faq_miss(question: str) -> None:
    """记录 FAQ 未命中的问题"""
    with get_db() as conn:
        conn.execute(
            "INSERT INTO faq_misses (question, created_at) VALUES (?, ?)",
            (question, time.time())
        )
        conn.commit()


def get_faq_misses(limit: int = 20) -> list[dict]:
    """获取 FAQ 未命中的高频问题（用于发现需要新增的规则）"""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT question, COUNT(*) as cnt
            FROM faq_misses
            GROUP BY question
            ORDER BY cnt DESC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


def search_conversations(keyword: str, limit: int = 20) -> list[dict]:
    """按关键词搜索对话"""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT DISTINCT c.session_id, c.channel, c.created_at, c.updated_at,
                   (SELECT COUNT(*) FROM messages WHERE conversation_id = c.id) as msg_count,
                   m.content as matched_content
            FROM messages m
            JOIN conversations c ON c.id = m.conversation_id
            WHERE m.content LIKE ?
            ORDER BY m.created_at DESC
            LIMIT ?
        """, (f"%{keyword}%", limit)).fetchall()
        return [dict(r) for r in rows]


def get_response_time_stats() -> dict:
    """获取响应时间统计"""
    with get_db() as conn:
        row = conn.execute("""
            SELECT AVG(elapsed_ms) as avg_ms,
                   MIN(elapsed_ms) as min_ms,
                   MAX(elapsed_ms) as max_ms,
                   COUNT(*) as total
            FROM messages
            WHERE role = 'assistant' AND elapsed_ms > 0
        """).fetchone()
        if row and row["total"] > 0:
            return {
                "avg_ms": round(row["avg_ms"], 0),
                "min_ms": row["min_ms"],
                "max_ms": row["max_ms"],
                "total": row["total"],
            }
        return {"avg_ms": 0, "min_ms": 0, "max_ms": 0, "total": 0}


def get_peak_hours() -> list[dict]:
    """统计每小时的对话量（发现高峰时段）"""
    from datetime import datetime
    with get_db() as conn:
        rows = conn.execute("""
            SELECT CAST(((created_at + 28800) % 86400) / 3600 AS INTEGER) as hour,
                   COUNT(*) as cnt
            FROM conversations
            GROUP BY hour
            ORDER BY hour
        """).fetchall()
        # 补齐 24 小时
        hour_map = {r["hour"]: r["cnt"] for r in rows}
        return [{"hour": h, "count": hour_map.get(h, 0)} for h in range(24)]


def get_active_sessions_count() -> int:
    """获取活跃会话数（最近30分钟有消息的）"""
    cutoff = time.time() - 1800
    with get_db() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM conversations WHERE status = 'active' AND updated_at >= ?",
            (cutoff,)
        ).fetchone()[0]


def get_pending_human_count() -> int:
    """获取未处理的转人工数量"""
    with get_db() as conn:
        return conn.execute("""
            SELECT COUNT(DISTINCT m.session_id) FROM messages m
            JOIN conversations c ON c.session_id = m.session_id
            WHERE m.need_human = 1 AND c.status = 'active'
            AND m.session_id NOT IN (
                SELECT session_id FROM messages WHERE role = 'human'
            )
        """).fetchone()[0]


def close_stale_conversations(hours: int = 2) -> int:
    """关闭超过 N 小时无活动的会话"""
    cutoff = time.time() - hours * 3600
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE conversations SET status = 'closed' WHERE status = 'active' AND updated_at < ?",
            (cutoff,)
        )
        conn.commit()
        return cursor.rowcount


def get_human_queue() -> list[dict]:
    """获取待人工处理的对话队列"""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT DISTINCT c.session_id, c.channel, c.tags, c.updated_at,
                   (SELECT content FROM messages WHERE session_id = c.session_id AND role = 'user'
                    ORDER BY created_at DESC LIMIT 1) as last_user_msg,
                   (SELECT content FROM messages WHERE session_id = c.session_id AND role = 'assistant'
                    ORDER BY created_at DESC LIMIT 1) as last_ai_reply,
                   (SELECT COUNT(*) FROM messages WHERE session_id = c.session_id) as msg_count
            FROM conversations c
            JOIN messages m ON m.session_id = c.session_id
            WHERE m.need_human = 1 AND c.status = 'active'
            AND c.session_id NOT IN (
                SELECT session_id FROM messages WHERE role = 'human'
            )
            ORDER BY c.updated_at DESC
        """).fetchall()
        return [dict(r) for r in rows]


def save_human_reply(session_id: str, content: str, operator: str = "店长") -> int:
    """保存人工客服回复"""
    now = time.time()
    with get_db() as conn:
        row = conn.execute(
            "SELECT id FROM conversations WHERE session_id = ? AND status = 'active'",
            (session_id,)
        ).fetchone()
        if not row:
            return 0

        conv_id = row["id"]
        conn.execute("UPDATE conversations SET updated_at = ? WHERE id = ?", (now, conv_id))

        cursor = conn.execute(
            "INSERT INTO messages (conversation_id, session_id, role, content, confidence, need_human, sources, created_at, elapsed_ms) VALUES (?, ?, 'human', ?, 1.0, 0, ?, ?, 0)",
            (conv_id, session_id, content, f"人工:{operator}", now)
        )
        conn.commit()
        return cursor.lastrowid


def tag_conversation(session_id: str, tags: str) -> bool:
    """给对话打标签"""
    with get_db() as conn:
        cursor = conn.execute(
            "UPDATE conversations SET tags = ? WHERE session_id = ?",
            (tags, session_id)
        )
        conn.commit()
        return cursor.rowcount > 0


def auto_tag_conversation(session_id: str) -> str:
    """根据对话内容自动打标签"""
    msgs = get_conversation_messages(session_id)
    user_texts = " ".join(m["content"] for m in msgs if m["role"] == "user")

    tag_rules = {
        "价格咨询": ["多少钱", "价格", "收费", "费用"],
        "预约": ["预约", "订", "下单"],
        "会员": ["会员", "折扣", "优惠", "积分"],
        "设备故障": ["故障", "坏了", "没声音", "不工作", "不制冷"],
        "投诉": ["投诉", "差评", "不满意", "太差"],
        "退款": ["退款", "退钱"],
        "新手": ["第一次", "怎么用", "怎么进"],
        "位置": ["在哪", "地址", "怎么去"],
    }

    matched = []
    for tag, keywords in tag_rules.items():
        if any(kw in user_texts for kw in keywords):
            matched.append(tag)

    tags = ",".join(matched[:3]) if matched else "其他"
    tag_conversation(session_id, tags)
    return tags
