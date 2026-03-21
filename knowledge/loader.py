"""知识库加载器 - 从 markdown 文件加载到 ChromaDB"""

import logging
from pathlib import Path

import chromadb

from config import config

logger = logging.getLogger(__name__)


def get_chroma_client() -> chromadb.ClientAPI:
    """获取 ChromaDB 客户端"""
    return chromadb.PersistentClient(path=config.CHROMA_PERSIST_DIR)


def get_collection(client: chromadb.ClientAPI) -> chromadb.Collection:
    """获取或创建知识库 collection"""
    return client.get_or_create_collection(
        name="jinxiang_kb",
        metadata={"hnsw:space": "cosine"},
    )


def split_document(text: str, doc_title: str = "", max_chunk_size: int = 800) -> list[str]:
    """按 ## 标题切分文档，保持每个主题完整

    策略：按二级标题(##)分段，每段带上文档标题作为上下文。
    这样价格表、房型介绍等结构化信息不会被拆散。
    """
    import re

    # 按 ## 标题分段
    sections = re.split(r'\n(?=## )', text)
    chunks = []

    # 提取文档一级标题
    title_prefix = ""
    if doc_title:
        title_prefix = f"[{doc_title}] "

    for section in sections:
        section = section.strip()
        if not section or len(section) < 10:
            continue

        # 加上文档标题前缀，帮助检索
        chunk = f"{title_prefix}{section}" if title_prefix else section

        # 如果段落太长，按 ### 三级标题再拆
        if len(chunk) > max_chunk_size:
            sub_sections = re.split(r'\n(?=### )', section)
            for sub in sub_sections:
                sub = sub.strip()
                if sub and len(sub) >= 10:
                    sub_chunk = f"{title_prefix}{sub}"
                    chunks.append(sub_chunk[:max_chunk_size])
        else:
            chunks.append(chunk)

    # 如果没有 ## 标题（纯文本），回退到段落切分
    if not chunks:
        paragraphs = text.split("\n\n")
        current = ""
        for para in paragraphs:
            para = para.strip()
            if not para:
                continue
            if len(current) + len(para) <= max_chunk_size:
                current = f"{current}\n{para}" if current else para
            else:
                if current:
                    chunks.append(f"{title_prefix}{current}")
                current = para
        if current:
            chunks.append(f"{title_prefix}{current}")

    return chunks


def load_knowledge_base(force_reload: bool = False) -> int:
    """加载知识库文档到 ChromaDB

    Returns:
        加载的文档片段数量
    """
    client = get_chroma_client()

    if force_reload:
        try:
            client.delete_collection("jinxiang_kb")
        except Exception:
            logger.warning("删除旧 collection 失败，可能不存在")

    collection = get_collection(client)

    # 如果已有数据且不强制重载，跳过
    if collection.count() > 0 and not force_reload:
        logger.info(f"知识库已有 {collection.count()} 条记录，跳过加载")
        return collection.count()

    docs_dir = Path(config.KNOWLEDGE_DIR)
    if not docs_dir.exists():
        logger.error(f"知识库目录不存在: {docs_dir}")
        return 0

    all_chunks = []
    all_ids = []
    all_metadatas = []

    for md_file in sorted(docs_dir.glob("*.md")):
        content = md_file.read_text(encoding="utf-8")
        doc_name = md_file.stem
        chunks = split_document(content, doc_title=doc_name)

        for i, chunk in enumerate(chunks):
            all_chunks.append(chunk)
            all_ids.append(f"{doc_name}_{i}")
            all_metadatas.append({"source": doc_name, "chunk_index": i})

    if not all_chunks:
        logger.warning("没有找到知识库文档")
        return 0

    # 批量添加到 ChromaDB（ChromaDB 内置 embedding）
    collection.add(
        documents=all_chunks,
        ids=all_ids,
        metadatas=all_metadatas,
    )

    logger.info(f"知识库加载完成: {len(all_chunks)} 条文档片段")
    return len(all_chunks)


def _keyword_search(collection: chromadb.Collection, question: str, top_k: int) -> list[dict]:
    """关键词匹配检索 - 弥补向量模型对中文的不足"""
    # 定义关键词到知识来源的映射
    keyword_source_map = {
        "价格": ["价格表"],
        "多少钱": ["价格表"],
        "收费": ["价格表"],
        "房型": ["房型介绍", "价格表"],
        "包厢": ["价格表", "房型介绍"],
        "大包": ["价格表", "房型介绍"],
        "中包": ["价格表", "房型介绍"],
        "小包": ["价格表", "房型介绍"],
        "茶室": ["价格表", "房型介绍"],
        "雅座": ["价格表", "房型介绍"],
        "会员": ["会员体系"],
        "积分": ["会员体系"],
        "折扣": ["会员体系", "价格表"],
        "预约": ["使用指南", "常见问题"],
        "怎么用": ["使用指南"],
        "开门": ["使用指南"],
        "小程序": ["使用指南"],
        "地址": ["位置交通"],
        "在哪": ["位置交通"],
        "怎么去": ["位置交通"],
        "故障": ["常见问题"],
        "话筒": ["常见问题"],
        "空调": ["常见问题"],
        "退款": ["常见问题"],
        "投诉": ["常见问题"],
    }

    matched_sources = set()
    for kw, sources in keyword_source_map.items():
        if kw in question:
            matched_sources.update(sources)

    if not matched_sources:
        return []

    docs = []
    for source in matched_sources:
        try:
            results = collection.get(
                where={"source": source},
            )
            if results and results["documents"]:
                for i, doc in enumerate(results["documents"]):
                    # 跳过太短的片段（标题等）
                    if len(doc) < 30:
                        continue
                    docs.append({
                        "content": doc,
                        "source": source,
                        "score": 0.9,  # 关键词命中给高分
                    })
        except Exception as e:
            logger.warning(f"关键词检索 {source} 失败: {e}")

    return docs[:top_k]


def query_knowledge(question: str, top_k: int = 0) -> list[dict]:
    """混合检索：关键词匹配 + 向量检索

    Returns:
        匹配的文档片段列表 [{"content": ..., "source": ..., "score": ...}]
    """
    if top_k <= 0:
        top_k = config.RAG_TOP_K

    client = get_chroma_client()
    collection = get_collection(client)

    if collection.count() == 0:
        logger.warning("知识库为空，请先加载文档")
        return []

    # 1. 关键词检索（优先，解决中文向量模型弱的问题）
    keyword_docs = _keyword_search(collection, question, top_k)

    # 2. 向量检索
    vector_docs = []
    results = collection.query(
        query_texts=[question],
        n_results=top_k,
    )
    if results and results["documents"]:
        for i, doc in enumerate(results["documents"][0]):
            distance = results["distances"][0][i] if results["distances"] else 1.0
            score = 1.0 - distance / 2.0
            source = results["metadatas"][0][i].get("source", "unknown") if results["metadatas"] else "unknown"
            if score >= config.RAG_SCORE_THRESHOLD:
                vector_docs.append({
                    "content": doc,
                    "source": source,
                    "score": round(score, 3),
                })

    # 3. 合并去重（关键词优先）
    seen_contents = set()
    merged = []

    for doc in keyword_docs + vector_docs:
        content_key = doc["content"][:100]  # 用前100字去重
        if content_key not in seen_contents:
            seen_contents.add(content_key)
            merged.append(doc)

    # 按分数排序，取 top_k
    merged.sort(key=lambda x: x["score"], reverse=True)
    return merged[:top_k]
