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


def send_text_reply(user_id: str, content: str) -> bool:
    """通过企微 API 主动发送文本消息给用户"""
    if not config.WECOM_CORP_ID or not config.WECOM_SECRET:
        logger.warning("企微配置缺失，跳过发送")
        return False

    try:
        # 先获取 access_token
        token_url = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
        params = {"corpid": config.WECOM_CORP_ID, "corpsecret": config.WECOM_SECRET}
        resp = httpx.get(token_url, params=params, timeout=10)
        token_data = resp.json()

        if token_data.get("errcode", 0) != 0:
            logger.error(f"获取 access_token 失败: {token_data}")
            return False

        access_token = token_data["access_token"]

        # 发送消息
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
    """通知人工客服需要介入

    TODO: 后续可对接企微群机器人 webhook 推送到管理员
    """
    logger.warning(f"需要人工介入 | 用户: {user_id} | 消息: {user_message}")
    # 预留：推送到企微群机器人
    # webhook_url = config.WECOM_NOTIFY_WEBHOOK
    # httpx.post(webhook_url, json={"msgtype": "text", "text": {"content": ...}}, timeout=10)
