"""企业微信消息加解密

参考官方文档实现，用于 callback 验证和消息解密
"""

import base64
import hashlib
import logging
import os
import struct
import time

from Crypto.Cipher import AES

logger = logging.getLogger(__name__)


class WXBizMsgCrypt:
    """企业微信消息加解密类"""

    def __init__(self, token: str, encoding_aes_key: str, corp_id: str):
        self.token = token
        self.corp_id = corp_id
        self.aes_key = base64.b64decode(encoding_aes_key + "=")

    def verify_url(self, msg_signature: str, timestamp: str, nonce: str, echostr: str) -> str:
        """验证回调 URL（GET 请求）

        Returns:
            解密后的 echostr
        """
        signature = self._make_signature(timestamp, nonce, echostr)
        if signature != msg_signature:
            raise ValueError("签名验证失败")

        return self._decrypt(echostr)

    def decrypt_msg(self, msg_signature: str, timestamp: str, nonce: str, encrypted_msg: str) -> str:
        """解密接收到的消息

        Returns:
            解密后的 XML 消息体
        """
        signature = self._make_signature(timestamp, nonce, encrypted_msg)
        if signature != msg_signature:
            raise ValueError("消息签名验证失败")

        return self._decrypt(encrypted_msg)

    def encrypt_msg(self, reply_msg: str, nonce: str, timestamp: str = "") -> tuple[str, str, str, str]:
        """加密回复消息

        Returns:
            (encrypted_msg, signature, timestamp, nonce)
        """
        if not timestamp:
            timestamp = str(int(time.time()))

        encrypted = self._encrypt(reply_msg)
        signature = self._make_signature(timestamp, nonce, encrypted)

        return encrypted, signature, timestamp, nonce

    def _make_signature(self, timestamp: str, nonce: str, encrypted: str) -> str:
        """生成签名"""
        parts = sorted([self.token, timestamp, nonce, encrypted])
        raw = "".join(parts).encode("utf-8")
        return hashlib.sha1(raw).hexdigest()

    def _encrypt(self, text: str) -> str:
        """AES 加密"""
        text_bytes = text.encode("utf-8")
        # 16字节随机字符串 + 4字节内容长度(网络序) + 内容 + corp_id
        rand_bytes = os.urandom(16)
        content = rand_bytes + struct.pack("!I", len(text_bytes)) + text_bytes + self.corp_id.encode("utf-8")
        # PKCS7 填充
        pad_len = 32 - (len(content) % 32)
        content += bytes([pad_len]) * pad_len

        cipher = AES.new(self.aes_key, AES.MODE_CBC, iv=self.aes_key[:16])
        encrypted = cipher.encrypt(content)
        return base64.b64encode(encrypted).decode("utf-8")

    def _decrypt(self, encrypted_text: str) -> str:
        """AES 解密"""
        encrypted = base64.b64decode(encrypted_text)
        cipher = AES.new(self.aes_key, AES.MODE_CBC, iv=self.aes_key[:16])
        decrypted = cipher.decrypt(encrypted)

        # 去除 PKCS7 填充
        pad_len = decrypted[-1]
        content = decrypted[:-pad_len]

        # 解析: 16字节随机 + 4字节长度 + 内容 + corp_id
        msg_len = struct.unpack("!I", content[16:20])[0]
        msg = content[20:20 + msg_len].decode("utf-8")

        # 验证 corp_id
        from_corp_id = content[20 + msg_len:].decode("utf-8")
        if from_corp_id != self.corp_id:
            raise ValueError(f"CorpID 不匹配: {from_corp_id}")

        return msg
