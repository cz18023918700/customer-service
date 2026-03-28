"""微信客服轮询服务

定时拉取微信客服消息，调用 AI 对话引擎，发送回复。
不需要回调 URL，适合 ICP 备案未通过的场景。
"""

import json
import logging
import os
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv

# 加载 .env
load_dotenv(Path(__file__).parent / '.env')

CORP_ID = os.getenv('WECOM_CORP_ID', '')
SECRET = os.getenv('WECOM_SECRET', '')
CHAT_API = 'http://127.0.0.1:8900/chat'
POLL_INTERVAL = 3  # 秒
CURSOR_FILE = Path(__file__).parent / '.wecom_cursor'

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(
            str(Path(__file__).parent / 'logs' / 'wecom_poller.log'),
            encoding='utf-8',
        ),
    ],
)
logger = logging.getLogger('wecom_poller')

# access_token 缓存
_token_cache = {'token': '', 'expires_at': 0.0}


def get_access_token() -> str:
    now = time.time()
    if _token_cache['token'] and now < _token_cache['expires_at']:
        return _token_cache['token']

    resp = httpx.get(
        'https://qyapi.weixin.qq.com/cgi-bin/gettoken',
        params={'corpid': CORP_ID, 'corpsecret': SECRET},
        timeout=10,
    )
    data = resp.json()
    if data.get('errcode', 0) != 0:
        logger.error(f'获取 access_token 失败: {data}')
        return ''

    _token_cache['token'] = data['access_token']
    _token_cache['expires_at'] = now + data.get('expires_in', 7200) - 300
    logger.info('access_token 刷新成功')
    return _token_cache['token']


def sync_msg(token: str, cursor: str = '') -> dict:
    payload = {'cursor': cursor, 'limit': 100, 'voice_format': 0}
    if cursor:
        payload['cursor'] = cursor
    resp = httpx.post(
        f'https://qyapi.weixin.qq.com/cgi-bin/kf/sync_msg?access_token={token}',
        json=payload,
        timeout=10,
    )
    return resp.json()


def send_kf_msg(token: str, open_kfid: str, external_userid: str, content: str) -> dict:
    payload = {
        'touser': external_userid,
        'open_kfid': open_kfid,
        'msgtype': 'text',
        'text': {'content': content},
    }
    resp = httpx.post(
        f'https://qyapi.weixin.qq.com/cgi-bin/kf/send_msg?access_token={token}',
        json=payload,
        timeout=10,
    )
    return resp.json()


def get_ai_reply(user_id: str, message: str) -> str:
    try:
        resp = httpx.post(
            CHAT_API,
            json={'message': message, 'session_id': f'wecom_{user_id}'},
            timeout=60,
        )
        data = resp.json()
        return data.get('reply', '抱歉，系统繁忙，请稍后再试。')
    except Exception as e:
        logger.error(f'调用 AI 引擎失败: {e}')
        return '抱歉，系统繁忙，请稍后再试。'


def load_cursor() -> str:
    if CURSOR_FILE.exists():
        return CURSOR_FILE.read_text(encoding='utf-8').strip()
    return ''


def save_cursor(cursor: str):
    tmp = CURSOR_FILE.with_suffix('.tmp')
    tmp.write_text(cursor, encoding='utf-8')
    tmp.replace(CURSOR_FILE)


def main():
    logger.info('微信客服轮询服务启动')
    logger.info(f'Corp ID: {CORP_ID[:8]}***')
    logger.info(f'轮询间隔: {POLL_INTERVAL}s')

    if not CORP_ID or not SECRET:
        logger.error('WECOM_CORP_ID 或 WECOM_SECRET 未配置')
        return

    cursor = load_cursor()
    consecutive_errors = 0

    while True:
        try:
            token = get_access_token()
            if not token:
                time.sleep(30)
                continue

            result = sync_msg(token, cursor)
            errcode = result.get('errcode', 0)

            if errcode != 0:
                logger.warning(f'sync_msg 失败: {result}')
                consecutive_errors += 1
                if consecutive_errors > 10:
                    time.sleep(60)
                else:
                    time.sleep(POLL_INTERVAL)
                continue

            consecutive_errors = 0
            new_cursor = result.get('next_cursor', '')
            if new_cursor:
                cursor = new_cursor
                save_cursor(cursor)

            msg_list = result.get('msg_list', [])
            for msg in msg_list:
                # origin: 3=客户发送, 4=系统, 5=接待人员
                if msg.get('origin') != 3:
                    continue
                if msg.get('msgtype') != 'text':
                    continue

                external_userid = msg.get('external_userid', '')
                open_kfid = msg.get('open_kfid', '')
                content = msg.get('text', {}).get('content', '').strip()

                if not content or not external_userid or not open_kfid:
                    continue

                masked = external_userid[:6] + '***'
                logger.info(f'收到客户消息 | 用户: {masked} | 内容: {content[:50]}')

                # AI 回复
                reply = get_ai_reply(external_userid, content)
                logger.info(f'AI 回复 | 用户: {masked} | 长度: {len(reply)}')

                # 发送回复
                send_result = send_kf_msg(token, open_kfid, external_userid, reply)
                if send_result.get('errcode', 0) != 0:
                    logger.error(f'发送失败: {send_result}')
                else:
                    logger.info(f'回复已发送 | 用户: {masked}')

            if not msg_list:
                has_more = result.get('has_more', 0)
                if not has_more:
                    time.sleep(POLL_INTERVAL)
            # 有更多消息时不等待，立即拉取下一批

        except KeyboardInterrupt:
            logger.info('轮询服务停止')
            break
        except Exception as e:
            logger.error(f'轮询异常: {e}')
            consecutive_errors += 1
            time.sleep(min(POLL_INTERVAL * consecutive_errors, 60))


if __name__ == '__main__':
    main()
