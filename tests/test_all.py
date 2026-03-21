"""静享时空客服系统 — 自动测试

运行: python -m pytest tests/test_all.py -v
或:   python tests/test_all.py
"""

import os
import sys

# 确保能导入项目模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import time
from models.db import init_db, save_message, get_conversation_stats, save_feedback, get_feedback_stats, get_db, export_messages_csv, get_conversation_messages
from knowledge.loader import load_knowledge_base, query_knowledge
from engine.faq import match_faq
from engine.chat import _sessions


# ============ FAQ 测试 ============

def test_faq_price_big():
    r = match_faq("大包厢多少钱")
    assert r is not None
    assert "欢唱大包厢" in r["reply"]
    assert "88" in r["reply"] or "238" in r["reply"]


def test_faq_price_mid():
    r = match_faq("中包厢怎么收费")
    assert r is not None
    assert "50元" in r["reply"]


def test_faq_price_small():
    r = match_faq("小包厢多少钱")
    assert r is not None
    assert "45元" in r["reply"]


def test_faq_price_tea():
    r = match_faq("茶室多少钱")
    assert r is not None
    assert "茶室" in r["reply"]


def test_faq_location():
    r = match_faq("你们在哪")
    assert r is not None
    assert "翰林府" in r["reply"]


def test_faq_booking():
    r = match_faq("怎么预约")
    assert r is not None
    assert "小程序" in r["reply"]


def test_faq_first_time():
    r = match_faq("第一次来怎么用")
    assert r is not None
    assert "开门" in r["reply"]


def test_faq_membership():
    r = match_faq("会员有什么优惠")
    assert r is not None
    assert "9.5折" in r["reply"]


def test_faq_hours():
    r = match_faq("几点关门")
    assert r is not None
    assert "24小时" in r["reply"]


def test_faq_overtime():
    r = match_faq("超时怎么算")
    assert r is not None
    assert "超时" in r["reply"] or "每小时" in r["reply"]


def test_faq_mic():
    r = match_faq("话筒没声音")
    assert r is not None
    assert "开关" in r["reply"] or "重启" in r["reply"]


def test_faq_refund():
    r = match_faq("怎么退款")
    assert r is not None
    assert "退款" in r["reply"]


def test_faq_bring():
    r = match_faq("可以自带吃的吗")
    assert r is not None
    assert "自带" in r["reply"]


def test_faq_parking():
    r = match_faq("有停车位吗")
    assert r is not None
    assert "停车" in r["reply"]


def test_faq_recommend():
    r = match_faq("5个人适合什么")
    assert r is not None
    assert "中包厢" in r["reply"] or "大包厢" in r["reply"]


def test_faq_weighted_match():
    """怎么预约小包厢 应该优先匹配小包厢价格（更精确）"""
    r = match_faq("小包厢多少钱怎么预约")
    assert r is not None
    assert "45元" in r["reply"]


def test_faq_no_match():
    r = match_faq("今天天气怎么样")
    assert r is None


# ============ RAG 测试 ============

def test_rag_load():
    count = load_knowledge_base(force_reload=True)
    assert count > 0


def test_rag_query_price():
    results = query_knowledge("包厢价格", top_k=3)
    assert len(results) > 0
    has_price = any("价格表" in r["source"] for r in results)
    assert has_price


def test_rag_query_membership():
    results = query_knowledge("会员权益", top_k=3)
    assert len(results) > 0


# ============ DB 测试 ============

def test_db_init():
    init_db()
    with get_db() as conn:
        tables = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        names = {r["name"] for r in tables}
        assert "conversations" in names
        assert "messages" in names
        assert "feedback" in names


def test_db_save_and_query():
    sid = f"test_{time.time()}"
    msg_id = save_message(sid, "user", "测试消息", channel="web")
    assert msg_id > 0

    msgs = get_conversation_messages(sid)
    assert len(msgs) == 1
    assert msgs[0]["content"] == "测试消息"


def test_db_feedback():
    sid = f"test_fb_{time.time()}"
    msg_id = save_message(sid, "assistant", "回复", channel="web")
    save_feedback(sid, msg_id, 1)
    stats = get_feedback_stats()
    assert stats["total"] > 0


def test_db_stats():
    stats = get_conversation_stats()
    assert "total_conversations" in stats
    assert "faq_replies" in stats
    assert "llm_replies" in stats


def test_db_export_csv():
    csv = export_messages_csv()
    assert "session_id" in csv
    lines = csv.strip().split("\n")
    assert len(lines) >= 1  # 至少有 header


# ============ 运行入口 ============

if __name__ == "__main__":
    import traceback

    # 初始化
    init_db()
    load_knowledge_base(force_reload=True)

    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    failed = 0

    for test_fn in tests:
        name = test_fn.__name__
        try:
            test_fn()
            print(f"  PASS  {name}")
            passed += 1
        except Exception as e:
            print(f"  FAIL  {name}: {e}")
            traceback.print_exc()
            failed += 1

    print(f"\n{'='*40}")
    print(f"  {passed} passed, {failed} failed, {passed+failed} total")
    if failed == 0:
        print("  ALL TESTS PASSED")
    else:
        sys.exit(1)
