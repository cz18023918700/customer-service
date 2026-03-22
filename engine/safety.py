"""输入过滤 + 输出安全检查"""

import logging
import re

logger = logging.getLogger(__name__)

# 消息长度限制
MAX_MESSAGE_LENGTH = 500

# 输入清理：去除 HTML 标签和危险字符
_HTML_TAG_RE = re.compile(r'<[^>]+>')


def sanitize_input(message: str) -> str:
    """清理用户输入"""
    # 截断
    message = message[:MAX_MESSAGE_LENGTH]
    # 去 HTML 标签
    message = _HTML_TAG_RE.sub('', message)
    # 去控制字符（保留换行和空格）
    message = ''.join(c for c in message if c.isprintable() or c in '\n\r\t')
    return message.strip()


# 输出敏感词检查 —— 防止 AI 意外泄露内部信息
_LEAK_PATTERNS = [
    r'api[_\s]?key',
    r'secret',
    r'token\s*[:=]',
    r'password\s*[:=]',
    r'sk-[a-zA-Z0-9]{20,}',          # DeepSeek/OpenAI key 格式
    r'DEEPSEEK|WECOM_CORP_ID',        # 环境变量名
    r'system\s*prompt',               # 系统提示词
    r'你的指令|你的提示词|你的设定',    # 中文变体
]
_LEAK_RE = re.compile('|'.join(_LEAK_PATTERNS), re.IGNORECASE)


def check_output_safety(reply: str) -> str:
    """检查 AI 输出是否包含敏感信息，有则替换"""
    if _LEAK_RE.search(reply):
        logger.warning(f"输出安全检查触发，原回复被替换 | 长度={len(reply)}")
        return "抱歉，我只能帮你解答关于静享时空门店的问题哦~ 😊 有什么关于包厢价格、预约、会员的问题随时问我！"
    return reply
