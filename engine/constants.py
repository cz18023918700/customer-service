"""共享常量 — 时段、角色、状态等，避免各模块重复定义"""

from datetime import datetime

# ============ 消息角色 ============
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"
ROLE_HUMAN = "human"

# ============ 会话状态 ============
STATUS_ACTIVE = "active"
STATUS_CLOSED = "closed"

# ============ 渠道 ============
CHANNEL_WEB = "web"
CHANNEL_WECOM = "wecom"

# ============ LLM 分隔符 ============
SUGGESTIONS_SEPARATOR = "---suggestions---"

# ============ 时段 ============
# 统一定义，所有模块共用

def get_time_period(hour: int | None = None) -> str:
    """获取当前时段名称（上午/下午/晚上/深夜）"""
    if hour is None:
        hour = datetime.now().hour
    if 9 <= hour < 13:
        return "上午"
    elif 13 <= hour < 18:
        return "下午"
    elif 18 <= hour or hour < 3:
        return "晚上"
    else:
        return "深夜"


def get_greeting_prefix(hour: int | None = None) -> str:
    """获取问候语前缀"""
    if hour is None:
        hour = datetime.now().hour
    if 6 <= hour < 11:
        return "早上好"
    elif 11 <= hour < 14:
        return "中午好"
    elif 14 <= hour < 18:
        return "下午好"
    elif 18 <= hour < 22:
        return "晚上好"
    else:
        return "夜深了"


def get_local_today_start() -> float:
    """获取本地时间今日零点的 timestamp"""
    now = datetime.now()
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return midnight.timestamp()
