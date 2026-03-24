"""FAQ 快速回复 - 高频问题直接命中，不走 LLM

匹配成功 → 秒回，延迟 <10ms，零 API 成本
匹配失败 → 返回 None，走正常 RAG+LLM 流程
"""

import logging

from engine.constants import get_time_period

logger = logging.getLogger(__name__)


def _price_by_period() -> dict:
    """当前时段的价格"""
    period = get_time_period()
    if period in ("上午", "下午"):
        return {
            "period": period,
            "big_666": "88元/场",
            "big_999": "98元/场",
            "mid": "前2小时50元，之后每小时8元",
            "small": "前2小时45元，之后每小时8元",
        }
    else:
        return {
            "period": "晚上",
            "big_666": "238元/场",
            "big_999": "268元/场",
            "mid": "前2小时50元，之后每小时8元",
            "small": "前2小时45元，之后每小时8元",
        }


# FAQ 规则表：(匹配模式, 回复生成函数, 推荐追问)
FAQ_RULES: list[tuple[list[str], callable, list[str]]] = []


def _register(keywords: list[str], suggestions: list[str] | None = None):
    """装饰器：注册 FAQ 规则"""
    def decorator(func):
        FAQ_RULES.append((keywords, func, suggestions or []))
        return func
    return decorator


@_register(
    ["大包厢", "多少钱"],
    ["有什么会员折扣？", "3个人适合什么包厢？", "怎么预约？"],
)
def _price_big(msg):
    p = _price_by_period()
    return (
        f"现在是{p['period']}时段，大包厢价格：\n"
        f"· 欢唱大包厢(666/888号)：{p['big_666']} 🎤\n"
        f"· 尊享大包厢(999号)：{p['big_999']} 🎤\n\n"
        f"会员还有折扣哦～普通9.5折/银卡9折/金卡8.5折\n\n"
        f"[img:/static/images/store.jpg]\n\n"
        f"在小程序上直接下单就行~"
    )


@_register(
    ["中包厢", "多少钱"],
    ["大包厢多少钱？", "有什么会员折扣？"],
)
def _price_mid(msg):
    return (
        "榻榻米中包厢(333/555号)：\n"
        "· 前2小时：50元\n"
        "· 超出部分：每小时8元\n\n"
        "适合3-5人小聚，会员还有折扣~\n"
        "在小程序上直接下单就行~"
    )


@_register(
    ["小包厢", "多少钱"],
    ["大包厢多少钱？", "有什么会员折扣？"],
)
def _price_small(msg):
    return (
        "私密小包厢(111/222号)：\n"
        "· 前2小时：45元\n"
        "· 超出部分：每小时8元\n\n"
        "适合2-3人/情侣，私密安静~\n"
        "在小程序上直接下单就行~"
    )


@_register(
    ["茶室", "多少钱"],
    ["包厢多少钱？", "店在哪里？"],
)
def _price_tea(msg):
    return (
        "恒大城11栋茶室：\n"
        "· 茶室一：前2小时45元，之后每小时5元\n"
        "· 茶室二：前2小时40元，之后每小时5元\n\n"
        "适合喝茶聊天、商务洽谈~"
    )


@_register(
    ["在哪", "地址", "怎么去", "位置", "导航"],
    ["怎么预约？", "大包厢多少钱？"],
)
def _location(msg):
    return (
        "我们有两家店：\n"
        "📍 翰林府店（KTV休闲馆）：潮州翰林府\n"
        "📍 恒大城11栋店（茶馆+粮油）：潮州恒大城11栋\n\n"
        "24小时营业！在小程序首页有导航按钮，点一下就能过来~"
    )


@_register(
    ["怎么预约", "怎么订", "如何预约", "怎么下单"],
    ["大包厢多少钱？", "第一次来怎么用？", "有什么会员折扣？"],
)
def _how_to_book(msg):
    return (
        "预约很简单：\n"
        "1️⃣ 微信扫码或搜索「静享时空」小程序\n"
        "2️⃣ 选择包厢和时间\n"
        "3️⃣ 微信支付下单\n"
        "4️⃣ 到店后在小程序点「开门」就行啦~\n\n"
        "[img:/static/images/miniprogram.jpg]\n\n"
        "有空房就能直接订，热门时段建议提前预约哦"
    )


@_register(
    ["第一次", "新手", "怎么用", "怎么进", "开门"],
    ["怎么预约？", "大包厢多少钱？", "有什么会员折扣？"],
)
def _first_time(msg):
    return (
        "第一次来完全不用紧张 😊\n\n"
        "1️⃣ 微信扫码进入小程序 → 选房下单\n"
        "2️⃣ 到店后点小程序里的「开门」\n"
        "3️⃣ 进去就能用啦，要喝什么到无人超市扫码买\n"
        "4️⃣ 走的时候带好东西，门自动锁\n\n"
        "[img:/static/images/miniprogram.jpg]\n\n"
        "全程自助，24小时都可以来~"
    )


@_register(
    ["会员", "折扣", "优惠", "打折"],
    ["大包厢多少钱？", "怎么成为金卡会员？"],
)
def _membership(msg):
    return (
        "会员体系：\n"
        "🥉 普通会员：免费注册，全单9.5折\n"
        "🥈 银卡会员：累计消费满1500元，全单9折+每月2瓶啤酒\n"
        "🥇 金卡会员：累计消费满3500元，全单8.5折+生日免费大包厢3小时\n\n"
        "在小程序注册就是普通会员，消费自动累计升级~"
    )


@_register(
    ["营业时间", "几点开门", "几点关门", "什么时候营业"],
    ["大包厢多少钱？", "怎么预约？"],
)
def _hours(msg):
    return "我们是 24小时营业 的哦，随时想来就来！全程自助，在小程序上下单开门就行~"


@_register(
    ["超时", "超出", "续费", "续时"],
    ["大包厢多少钱？", "怎么在小程序续费？"],
)
def _overtime(msg):
    return (
        "超时计费规则：\n"
        "· 大包厢：按场计费（一个时段为一场），超出按下个时段价格算\n"
        "· 中包厢/小包厢/雅座：超出部分每小时8元\n"
        "· 茶室：超出部分每小时5元\n\n"
        "建议在小程序上提前续费，这样不会中断体验~"
    )


@_register(
    ["话筒", "麦克风", "没声音", "故障", "坏了", "不工作"],
    ["空调怎么调？", "投影不亮怎么办？"],
)
def _equipment_mic(msg):
    return (
        "话筒没声音？先试这两步：\n"
        "1️⃣ 检查话筒底部开关是否打开\n"
        "2️⃣ 长按电源键3秒重启\n\n"
        "抽屉里有备用电池可以换。\n"
        "还不行的话告诉我，我帮你联系工作人员处理 🔧"
    )


@_register(
    ["空调", "制冷", "制热", "太冷", "太热"],
    ["话筒没声音怎么办？", "投影不亮怎么办？"],
)
def _equipment_ac(msg):
    return (
        "空调遥控器在墙上的收纳盒里 📦\n\n"
        "如果不制冷/制热：\n"
        "1️⃣ 检查遥控器有没有电\n"
        "2️⃣ 确认模式是否正确\n"
        "3️⃣ 等3-5分钟让空调启动\n\n"
        "还不行就告诉我，帮你联系处理~"
    )


@_register(
    ["退款", "退钱", "不想要了"],
    ["怎么预约？", "有什么会员折扣？"],
)
def _refund(msg):
    return (
        "退款说明：\n"
        "· 提前2小时以上取消：免费退款\n"
        "· 2小时内取消：可能收取一定费用\n\n"
        "具体情况我帮你转给工作人员确认，稍等一下哦~"
    )


@_register(
    ["带东西", "自带", "带吃的", "带酒", "带食物"],
    ["无人超市有什么？", "大包厢多少钱？"],
)
def _bring_stuff(msg):
    return (
        "可以自带食物和非酒精饮料 🍿\n"
        "酒水建议在店内无人超市买，价格很平价~\n"
        "走的时候请把垃圾带走或扔到垃圾桶，谢谢配合 😊"
    )


@_register(
    ["停车", "车位", "停车场"],
    ["店在哪里？", "怎么预约？"],
)
def _parking(msg):
    return "恒大城小区内有停车位，具体以小区物业安排为准。到了可以直接找小区停车场~"


@_register(
    ["几个人", "人适合", "人去", "人数", "容纳"],
    ["大包厢多少钱？", "怎么预约？"],
)
def _recommend_by_people(msg):
    return (
        "按人数推荐：\n"
        "· 1-3人 → 私密小包厢(111/222)或雅座，前2小时45元\n"
        "· 3-5人 → 榻榻米中包厢(333/555)，前2小时50元\n"
        "· 6-10人 → 欢唱大包厢(666/888)，可唱歌 🎤\n"
        "· 8-15人 → 尊享大包厢(999)，最大最豪华\n\n"
        "在小程序上选房下单就行~"
    )


@_register(
    ["价格表", "所有价格", "全部价格", "价目表"],
    ["怎么预约？", "有什么会员折扣？"],
)
def _price_table(msg):
    p = _price_by_period()
    return (
        f"当前{p['period']}时段价格：\n\n"
        f"🎤 欢唱大包厢(666/888)：{p['big_666']}\n"
        f"🎤 尊享大包厢(999)：{p['big_999']}\n"
        f"🛋️ 榻榻米中包厢(333/555)：{p['mid']}\n"
        f"🔒 私密小包厢(111/222)：{p['small']}\n"
        f"💺 雅座：前2小时45元，之后每小时8元\n\n"
        f"[img:/static/images/price.jpg]\n\n"
        f"会员享折扣：普通9.5折/银卡9折/金卡8.5折"
    )


@_register(
    ["提前预约", "可以预约吗", "能预约吗", "提前订"],
    ["怎么预约？", "大包厢多少钱？"],
)
def _can_book(msg):
    return "可以的！在小程序上随时可以预约，也可以当天临时下单。热门时段（晚上）建议提前预约哦~"


@_register(
    ["小程序下单", "小程序怎么用", "在哪下单"],
    ["第一次来怎么用？", "大包厢多少钱？"],
)
def _miniprogram_order(msg):
    return (
        "在微信搜索「静享时空」小程序就能下单啦~\n\n"
        "[img:/static/images/miniprogram.jpg]\n\n"
        "选好包厢和时间 → 微信支付 → 到店开门 → 开始嗨！"
    )


@_register(
    ["有空房", "有没有房", "还有包厢吗", "有房吗"],
    ["怎么预约？", "大包厢多少钱？"],
)
def _availability(msg):
    return "空房情况在小程序上实时显示哦~ 打开小程序就能看到各包厢的可预约时段，直接选时间下单就行！"


@_register(
    ["取消预约", "取消订单", "不去了", "退单"],
    ["怎么预约？", "退款怎么处理？"],
)
def _cancel_booking(msg):
    return (
        "取消预约：\n"
        "· 提前2小时以上取消：免费\n"
        "· 2小时内取消：可能收取一定费用\n\n"
        "在小程序「我的订单」里操作就行~"
    )


@_register(
    ["门怎么打开", "打不开门", "进不去"],
    ["话筒没声音怎么办？", "怎么预约？"],
)
def _door_open(msg):
    return (
        "开门方法：\n"
        "1️⃣ 确认订单已支付成功\n"
        "2️⃣ 打开小程序，点「开门」按钮\n"
        "3️⃣ 手机蓝牙和网络要打开\n\n"
        "还是打不开？试试重新点一次，或联系我们帮你处理~"
    )


@_register(
    ["付款", "支付方式", "怎么付钱", "微信支付"],
    ["怎么预约？", "有什么会员折扣？"],
)
def _payment(msg):
    return "通过小程序微信支付就行~ 选好包厢和时间后直接付款，到店开门即可使用。"


@_register(
    ["有wifi", "wifi密码", "无线网"],
    ["大包厢多少钱？", "怎么预约？"],
)
def _wifi(msg):
    return "店内有免费WiFi~ 到店后可以在墙上或桌面看到WiFi名称和密码。"


@_register(
    ["唱歌", "可以唱歌", "能唱歌吗", "KTV", "ktv", "K歌", "k歌"],
    ["大包厢多少钱？", "怎么预约？"],
)
def _singing(msg):
    return (
        "可以唱歌！🎤\n"
        "三个大包厢都配备了专业KTV点歌系统：\n"
        "· 欢唱大包厢(666/888号)：适合6-10人\n"
        "· 尊享大包厢(999号)：最大最豪华，适合8-15人\n\n"
        "中小包厢和茶室是休闲空间，没有KTV设备~"
    )


@_register(
    ["设备", "有什么", "包厢里面", "配置"],
    ["大包厢多少钱？", "怎么预约？"],
)
def _equipment(msg):
    return (
        "包厢配置：\n"
        "🎤 大包厢：KTV点歌系统+话筒+音响+沙发+茶几+灯光\n"
        "🛋️ 中包厢：榻榻米+桌游空间\n"
        "🔒 小包厢：私密安静空间\n\n"
        "店内还有无人超市，平价酒水零食随时买~"
    )


@_register(
    ["活动", "优惠活动", "有什么活动", "促销"],
    ["会员有什么优惠？", "大包厢多少钱？"],
)
def _promotions(msg):
    return "目前的优惠主要是会员折扣：普通9.5折、银卡9折、金卡8.5折。在小程序免费注册就是普通会员~ 更多活动关注我们的动态哦！"


@_register(
    ["生日", "派对", "聚会", "团建"],
    ["大包厢多少钱？", "怎么预约？"],
)
def _party(msg):
    return (
        "生日派对/聚会推荐大包厢！🎉\n"
        "· 6-10人选欢唱大包厢(666/888号)\n"
        "· 8-15人选尊享大包厢(999号)\n\n"
        "可以唱歌+店内无人超市买酒水零食，全程自助很方便~\n"
        "建议提前在小程序预约晚上时段！"
    )


def match_faq(user_message: str) -> dict | None:
    """加权匹配 FAQ — 选关键词覆盖率最高的规则

    Returns:
        {"reply": str, "suggestions": list[str]} 或 None
    """
    msg = user_message.lower().strip()

    best_match = None
    best_score = 0.0

    for keywords, handler, suggestions in FAQ_RULES:
        matched = sum(1 for kw in keywords if kw in msg)
        if matched == 0:
            continue
        # 覆盖率 = 命中数 / 关键词总数
        score = matched / len(keywords)
        if score > best_score:
            best_score = score
            best_match = (handler, suggestions)

    # 至少命中一个关键词才触发
    if best_match and best_score > 0:
        try:
            reply = best_match[0](msg)
            return {"reply": reply, "suggestions": best_match[1]}
        except Exception as e:
            logger.warning(f"FAQ handler 异常: {e}")
            return None

    return None
