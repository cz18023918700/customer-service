"""数据库模型 - SQLite 存储会话和消息记录"""

import sqlite3
import logging
import time
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent.parent / "customer_service.db"


def get_db() -> sqlite3.Connection:
    """获取数据库连接"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    """初始化数据库表"""
    conn = get_db()
    try:
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
    finally:
        conn.close()


def save_message(session_id: str, role: str, content: str,
                 confidence: float = 0, need_human: bool = False,
                 sources: str = "", channel: str = "web") -> int:
    """保存消息记录"""
    now = time.time()
    conn = get_db()
    try:
        # 查找或创建会话
        row = conn.execute(
            "SELECT id FROM conversations WHERE session_id = ? AND status = 'active'",
            (session_id,)
        ).fetchone()

        if row:
            conv_id = row["id"]
            conn.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (now, conv_id)
            )
        else:
            cursor = conn.execute(
                "INSERT INTO conversations (session_id, user_id, channel, status, created_at, updated_at) VALUES (?, ?, ?, 'active', ?, ?)",
                (session_id, session_id, channel, now, now)
            )
            conv_id = cursor.lastrowid

        # 保存消息
        cursor = conn.execute(
            "INSERT INTO messages (conversation_id, session_id, role, content, confidence, need_human, sources, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (conv_id, session_id, role, content, confidence, int(need_human), sources, now)
        )
        conn.commit()
        return cursor.lastrowid
    finally:
        conn.close()


def get_conversation_stats() -> dict:
    """获取对话统计"""
    conn = get_db()
    try:
        total_conv = conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        total_msg = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
        human_transfers = conn.execute("SELECT COUNT(*) FROM messages WHERE need_human = 1").fetchone()[0]

        # 今日数据
        today_start = int(time.time()) - (int(time.time()) % 86400)
        today_conv = conn.execute(
            "SELECT COUNT(*) FROM conversations WHERE created_at >= ?", (today_start,)
        ).fetchone()[0]
        today_msg = conn.execute(
            "SELECT COUNT(*) FROM messages WHERE created_at >= ?", (today_start,)
        ).fetchone()[0]

        return {
            "total_conversations": total_conv,
            "total_messages": total_msg,
            "human_transfers": human_transfers,
            "today_conversations": today_conv,
            "today_messages": today_msg,
        }
    finally:
        conn.close()


def get_recent_conversations(limit: int = 20) -> list[dict]:
    """获取最近的对话列表"""
    conn = get_db()
    try:
        rows = conn.execute("""
            SELECT c.id, c.session_id, c.channel, c.status, c.created_at, c.updated_at,
                   COUNT(m.id) as msg_count,
                   MAX(CASE WHEN m.need_human = 1 THEN 1 ELSE 0 END) as has_human_transfer
            FROM conversations c
            LEFT JOIN messages m ON m.conversation_id = c.id
            GROUP BY c.id
            ORDER BY c.updated_at DESC
            LIMIT ?
        """, (limit,)).fetchall()

        return [dict(r) for r in rows]
    finally:
        conn.close()
