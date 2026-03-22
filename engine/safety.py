"""安全模块 — 输入过滤 + 输出检查 + 恶意检测"""

import logging
import re
import time

logger = logging.getLogger(__name__)

# ============ 输入限制 ============
MAX_MESSAGE_LENGTH = 500

_HTML_TAG_RE = re.compile(r'<[^>]+>')


def sanitize_input(message: str) -> str:
    """清理用户输入：截断 + 去 HTML + 去控制字符"""
    message = message[:MAX_MESSAGE_LENGTH]
    message = _HTML_TAG_RE.sub('', message)
    message = ''.join(c for c in message if c.isprintable() or c in '\n\r\t')
    return message.strip()


# ============ 恶意输入检测 ============
# 明确的 prompt 注入/越狱尝试 → 直接拒绝，不送 LLM（省钱+安全）

_INJECTION_PATTERNS = [
    r'忽略.{0,10}(指令|规则|设定|提示)',
    r'输出.{0,10}(系统|提示词|指令|prompt)',
    r'你的(系统|提示词|指令|设定|规则)',
    r'system\s*prompt',
    r'ignore\s*(previous|all|your)\s*(instructions|rules)',
    r'pretend\s*(you\s*are|to\s*be)',
    r'act\s*as\s*(a|an)\s*(hacker|admin)',
    r'jailbreak',
    r'DAN\s*mode',
]
_INJECTION_RE = re.compile('|'.join(_INJECTION_PATTERNS), re.IGNORECASE)

INJECTION_REPLY = "抱歉，我只能帮你解答关于静享时空的问题哦~ 😊 比如包厢价格、怎么预约、会员福利，随时问我！"


def detect_injection(message: str) -> bool:
    """检测是否为 prompt 注入攻击"""
    if _INJECTION_RE.search(message):
        logger.warning(f"检测到注入攻击，已拦截 | 长度={len(message)}")
        return True
    return False


# ============ 输出泄露检测 ============

_LEAK_PATTERNS = [
    # API 密钥格式
    r'sk-[a-zA-Z0-9]{20,}',
    r'api[_\s]?key\s*[:=]',
    r'secret\s*[:=]',
    r'token\s*[:=]\s*\S+',
    r'password\s*[:=]',
    # 环境变量名
    r'DEEPSEEK|WECOM_CORP_ID|WECOM_SECRET|ENCODING_AES_KEY|ADMIN_TOKEN',
    # 系统提示词泄露
    r'system\s*prompt',
    r'你的指令|你的提示词|你的设定|我的设定是',
    # 技术架构泄露
    r'FastAPI|ChromaDB|SQLite|uvicorn',
    r'customer_service\.db',
    r'localhost:\d{4}',
    # 文件路径泄露
    r'[A-Z]:\\',
    r'/app/|/home/',
    # 内部成本信息
    r'(月租|店租|成本|利润率)\s*[:：]?\s*\d+',
]
_LEAK_RE = re.compile('|'.join(_LEAK_PATTERNS), re.IGNORECASE)

SAFE_REPLY = "抱歉，我只能帮你解答关于静享时空门店的问题哦~ 😊 有什么关于包厢价格、预约、会员的问题随时问我！"


def check_output_safety(reply: str) -> str:
    """检查 AI 输出是否包含敏感信息"""
    if _LEAK_RE.search(reply):
        logger.warning(f"输出安全检查触发 | 长度={len(reply)}")
        return SAFE_REPLY
    return reply


# ============ IP 级限流 ============

_ip_requests: dict[str, list[float]] = {}
_ip_bans: dict[str, float] = {}  # {ip: ban_until}
_ip_cleanup_ts = 0.0

IP_RATE_LIMIT = 30      # 每分钟最多请求数
IP_BAN_THRESHOLD = 100   # 1 分钟内超过这个数直接封
IP_BAN_DURATION = 600    # 封禁 10 分钟


def check_ip_rate(ip: str) -> tuple[bool, str]:
    """IP 级限流，返回 (是否允许, 原因)"""
    global _ip_cleanup_ts
    now = time.time()

    # 定期清理
    if now - _ip_cleanup_ts > 300:
        _ip_cleanup_ts = now
        stale = [k for k, v in _ip_requests.items() if not v or now - v[-1] > 60]
        for k in stale:
            del _ip_requests[k]
        expired_bans = [k for k, v in _ip_bans.items() if v < now]
        for k in expired_bans:
            del _ip_bans[k]

    # 检查封禁
    if ip in _ip_bans:
        if now < _ip_bans[ip]:
            return False, "IP 被临时封禁"
        else:
            del _ip_bans[ip]

    # 记录请求
    if ip not in _ip_requests:
        _ip_requests[ip] = []
    _ip_requests[ip] = [t for t in _ip_requests[ip] if now - t < 60]
    _ip_requests[ip].append(now)

    count = len(_ip_requests[ip])

    # 超阈值封禁
    if count > IP_BAN_THRESHOLD:
        _ip_bans[ip] = now + IP_BAN_DURATION
        logger.warning(f"IP {ip[:15]} 请求异常({count}/min)，封禁 {IP_BAN_DURATION}s")
        return False, "请求异常，已被临时限制"

    if count > IP_RATE_LIMIT:
        return False, "请求太频繁"

    return True, ""
