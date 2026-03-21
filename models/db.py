"""数据库模型 - SQLite 存储会话和消息记录"""

import sqlite3
import logging
import time
from contextlib import contextmanager
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "customer_service.db"


@contextmanager
def get_db():
    """获取数据库连接（上下文管理器，自动关闭）"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """初始化数据库表"""
    with get_db() as conn:
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

            CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_id);
            CREATE INDEX IF NOT EXISTS idx_msg_session ON messages(session_id);
            CREATE INDEX IF NOT EXISTS idx_msg_conv ON messages(conversation_id);
        """)
        conn.commit()
        logger.info("数据库初始化完成")


def save_message(session_id: str, role: str, content: str,
                 confidence: float = 0, need_human: bool = False,
                 sources: str = "", channel: str = "web") -> int:
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
            "INSERT INTO messages (conversation_id, session_id, role, content, confidence, need_human, sources, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (conv_id, session_id, role, content, confidence, int(need_human), sources, now)
        )
        conn.commit()
        return cursor.lastrowid


def get_conversation_stats() -> dict:
    """获取对话统计"""
    with get_db() as conn:
        total_conv = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        total_msg = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        human_transfers = conn.execute("SELECT COUNT(*) FROM messages WHERE need_human = 1").fetchone()[0]

        today_start = int(time.time()) - (int(time.time()) % 86400)
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
            SELECT role, content, confidence, need_human, sources, created_at
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
    from datetime import datetime, timedelta
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
        from datetime import datetime
        ts = datetime.fromtimestamp(r["created_at"]).strftime("%Y-%m-%d %H:%M:%S")
        writer.writerow([r["session_id"], r["channel"], r["role"], r["content"],
                         r["confidence"], r["need_human"], r["sources"], ts])
    return output.getvalue()
