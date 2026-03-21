"""配置管理 - 从 .env 读取所有配置"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 加载 .env
env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)


class Config:
    """全局配置"""

    # DeepSeek
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")

    # 企业微信
    WECOM_CORP_ID: str = os.getenv("WECOM_CORP_ID", "")
    WECOM_SECRET: str = os.getenv("WECOM_SECRET", "")
    WECOM_TOKEN: str = os.getenv("WECOM_TOKEN", "")
    WECOM_ENCODING_AES_KEY: str = os.getenv("WECOM_ENCODING_AES_KEY", "")
    WECOM_AGENT_ID: str = os.getenv("WECOM_AGENT_ID", "")

    # 服务
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8900"))
    DEBUG: bool = os.getenv("DEBUG", "false").lower() == "true"

    # 知识库
    CHROMA_PERSIST_DIR: str = os.getenv("CHROMA_PERSIST_DIR", "./chroma_db")
    KNOWLEDGE_DIR: str = str(Path(__file__).parent / "knowledge" / "docs")

    # RAG 参数
    RAG_TOP_K: int = 5
    RAG_SCORE_THRESHOLD: float = 0.3

    # 对话参数
    MAX_HISTORY_TURNS: int = 10
    TRANSFER_CONFIDENCE_THRESHOLD: float = 0.5

    # 转人工通知（企微群机器人 webhook URL）
    NOTIFY_WEBHOOK: str = os.getenv("NOTIFY_WEBHOOK", "")


config = Config()
