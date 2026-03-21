"""企业微信回调处理

处理企微发来的消息，调用对话引擎，返回回复
"""

import logging

import httpx
import xmltodict

from config import config
from wecom.crypto import WXBizMsgCrypt

logger = logging.getLogger(__name__)

_crypt: WXBizMsgCrypt | None = None


def get_crypt() -> WXBizMsgCrypt | None:
    """获取加解密实例（懒加载）"""
    global _crypt
    if _crypt is None and config.WECOM_CORP_ID and config.WECOM_ENCODING_AES_KEY:
        _crypt = WXBizMsgCrypt(
            token=config.WECOM_TOKEN,
            encoding_aes_key=config.WECOM_ENCODING_AES_KEY,
            corp_id=config.WECOM_CORP_ID,
        )
    return _crypt


def verify_callback(msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
    """验证企微回调 URL（首次配置时调用）"""
    crypt = get_crypt()
    if not crypt:
        raise RuntimeError("企业微信配置缺失")
    return crypt.verify_url(msg_signature, timestamp, nonce, echostr)


def parse_message(msg_signature: str, timestamp: str, nonce: str, body: str) -> dict | None:
    """解析企微推送的消息

    Returns:
        {"from_user": str, "content": str, "msg_type": str, "msg_id": str} or None
    """
    crypt = get_crypt()
    if not crypt:
        logger.error("企业微信配置缺失，无法解析消息")
        return None

    try:
        # 从 XML body 中提取 Encrypt 字段
        xml_dict = xmltodict.parse(body)
        encrypted_msg = xml_dict["xml"]["Encrypt"]

        # 解密
        decrypted_xml = crypt.decrypt_msg(msg_signature, timestamp, nonce, encrypted_msg)
        msg = xmltodict.parse(decrypted_xml)["xml"]

        return {
            "from_user": msg.get("FromUserName", ""),
            "to_user": msg.get("ToUserName", ""),
            "content": msg.get("Content", ""),
            "msg_type": msg.get("MsgType", "text"),
            "msg_id": msg.get("MsgId", ""),
            "create_time": msg.get("CreateTime", ""),
        }
    except Exception as e:
        logger.error(f"解析企微消息失败: {e}")
        return None


# access_token 缓存（有效期 7200 秒，提前 5 分钟刷新）
_token_cache: dict = {"token": "", "expires_at": 0.0}


def _get_access_token() -> str:
    """获取企微 access_token（带 TTL 缓存）"""
    import time as _time
    now = _time.time()
    if _token_cache["token"] and now < _token_cache["expires_at"]:
        return _token_cache["token"]

    token_url = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
    params = {"corpid": config.WECOM_CORP_ID, "corpsecret": config.WECOM_SECRET}
    resp = httpx.get(token_url, params=params, timeout=10)
    data = resp.json()

    if data.get("errcode", 0) != 0:
        logger.error(f"获取 access_token 失败: {data}")
        return ""

    _token_cache["token"] = data["access_token"]
    _token_cache["expires_at"] = now + data.get("expires_in", 7200) - 300  # 提前5分钟刷新
    return _token_cache["token"]


def send_text_reply(user_id: str, content: str) -> bool:
    """通过企微 API 主动发送文本消息给用户"""
    if not config.WECOM_CORP_ID or not config.WECOM_SECRET:
        logger.warning("企微配置缺失，跳过发送")
        return False

    try:
        access_token = _get_access_token()
        if not access_token:
            return False

        send_url = f"https://qyapi.weixin.qq.com/cgi-bin/message/send?access_token={access_token}"
        payload = {
            "touser": user_id,
            "msgtype": "text",
            "agentid": int(config.WECOM_AGENT_ID) if config.WECOM_AGENT_ID else 0,
            "text": {"content": content},
        }
        resp = httpx.post(send_url, json=payload, timeout=10)
        result = resp.json()

        if result.get("errcode", 0) != 0:
            logger.error(f"发送消息失败: {result}")
            return False

        return True
    except Exception as e:
        logger.error(f"发送企微消息异常: {e}")
        return False


def notify_human(user_id: str, user_message: str, ai_reply: str) -> None:
    """通知人工客服需要介入 — 推送到企微群机器人 webhook"""
    masked = user_id[:4] + "***" if len(user_id) > 4 else "***"
    logger.warning(f"需要人工介入 | 用户: {masked} | 消息长度: {len(user_message)}")

    webhook_url = config.NOTIFY_WEBHOOK
    if not webhook_url:
        logger.info("未配置 NOTIFY_WEBHOOK，跳过推送")
        return

    try:
        text = (
            f"🔔 需要人工客服介入\n"
            f"用户: {user_id}\n"
            f"消息: {user_message}\n"
            f"AI回复: {ai_reply[:100]}{'...' if len(ai_reply) > 100 else ''}\n"
            f"请尽快处理"
        )
        resp = httpx.post(
            webhook_url,
            json={"msgtype": "text", "text": {"content": text}},
            timeout=10,
        )
        if resp.status_code == 200:
            logger.info(f"人工通知已推送 | 用户: {user_id}")
        else:
            logger.warning(f"Webhook 推送失败: {resp.status_code} {resp.text[:200]}")
    except Exception as e:
        logger.error(f"Webhook 推送异常: {e}")
