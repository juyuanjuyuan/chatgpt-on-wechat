# -*- coding=utf-8 -*-
import io
import json
import os
import sys
import threading
import time
import xml.etree.ElementTree as ET

import requests
import web
from wechatpy.enterprise import create_reply, parse_message
from wechatpy.enterprise.crypto import WeChatCrypto
from wechatpy.enterprise.exceptions import InvalidCorpIdException
from wechatpy.exceptions import InvalidSignatureException, WeChatClientException

from bridge.context import Context
from bridge.reply import Reply, ReplyType
from channel.chat_channel import ChatChannel
from channel.wechatcom.wechatcomapp_client import WechatComAppClient
from channel.wechatcom.wechatcomapp_message import WechatComAppMessage
from common.expired_dict import ExpiredDict
from common.log import logger
from common.singleton import singleton
from common.utils import compress_imgfile, fsize, split_string_by_utf8_length, convert_webp_to_png, remove_markdown_symbol
from config import conf, subscribe_msg, get_appdata_dir
from voice.audio_convert import any_to_amr, split_audio

MAX_UTF8_LEN = 2048


@singleton
class WechatComAppChannel(ChatChannel):
    NOT_SUPPORT_REPLYTYPE = []

    def __init__(self):
        super().__init__()
        self.corp_id = conf().get("wechatcom_corp_id")
        self.secret = conf().get("wechatcomapp_secret")
        self.agent_id = conf().get("wechatcomapp_agent_id")
        self.token = conf().get("wechatcomapp_token")
        self.aes_key = conf().get("wechatcomapp_aes_key")
        # 微信客服相关配置（可选）
        self.kf_secret = conf().get("wechatcom_kf_secret")
        self.kf_open_kfid = conf().get("wechatcom_kf_open_kfid")
        self._kf_access_token = None
        self._kf_access_token_expires_at = 0
        # cursor persisted to disk so server restarts do not replay history
        self._kf_cursor_file = os.path.join(get_appdata_dir(), "kf_sync_cursor.json")
        self._kf_sync_cursor = self._load_kf_cursor()
        # dedup by msgid for 24h to avoid processing the same message twice
        self._processed_msgids = ExpiredDict(86400)
        # If no cursor exists for our open_kfid, record startup time so we can
        # skip ALL historical messages on the first sync (prevents replaying the
        # full WeChat KF history when there is no saved cursor position).
        has_cursor = bool(self.kf_open_kfid and self._kf_sync_cursor.get(self.kf_open_kfid))
        self._startup_time = 0 if has_cursor else int(time.time())
        if not has_cursor:
            logger.info(
                "[wechatcom] No saved cursor for open_kfid=%s – historical messages will be skipped on first sync",
                self.kf_open_kfid,
            )
        # Lock so concurrent webhook deliveries of kf_msg_or_event don't race
        self._kf_sync_lock = threading.Lock()
        # Flag: a new event arrived while a sync was in progress; re-sync after.
        self._kf_sync_pending = False
        self._http_server = None
        logger.info(
            "[wechatcom] Initializing WeCom app channel, corp_id: {}, agent_id: {}".format(self.corp_id, self.agent_id)
        )
        self.crypto = WeChatCrypto(self.token, self.aes_key, self.corp_id)
        self.client = WechatComAppClient(self.corp_id, self.secret)

    def _load_kf_cursor(self):
        """Load persisted cursor dict from disk; return empty dict on failure."""
        try:
            if os.path.exists(self._kf_cursor_file):
                with open(self._kf_cursor_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    logger.info("[wechatcom] Loaded kf cursor from disk: %s", data)
                    return data
        except Exception as e:
            logger.warning("[wechatcom] Failed to load kf cursor file: %s", e)
        return {}

    def _save_kf_cursor(self):
        """Persist cursor dict to disk so restarts resume from the right position."""
        try:
            tmp = self._kf_cursor_file + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._kf_sync_cursor, f)
            os.replace(tmp, self._kf_cursor_file)
        except Exception as e:
            logger.warning("[wechatcom] Failed to save kf cursor file: %s", e)

    def _get_kf_access_token(self):
        """
        使用企业微信「微信客服」相关的 secret 获取 access_token，并在内存中做简单缓存。
        - 优先使用 wechatcom_kf_secret
        - 若未显式配置，则回退使用当前自建应用的 wechatcomapp_secret
        """
        if not self.corp_id:
            return None
        secret = self.kf_secret or self.secret
        if not secret:
            return None
        now = time.time()
        if self._kf_access_token and self._kf_access_token_expires_at - now > 60:
            return self._kf_access_token
        url = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"
        try:
            resp = requests.get(url, params={"corpid": self.corp_id, "corpsecret": secret}, timeout=5)
            data = resp.json()
        except Exception as e:
            logger.error(f"[wechatcom] get wecom kf access_token failed: {e}")
            return None
        if data.get("errcode") != 0:
            logger.error("[wechatcom] get wecom kf access_token error: %s", data)
            return None
        self._kf_access_token = data.get("access_token")
        expires_in = data.get("expires_in", 7200)
        self._kf_access_token_expires_at = now + int(expires_in)
        return self._kf_access_token

    def _send_kf_text(self, touser: str, text: str) -> bool:
        """
        通过企业微信「微信客服」接口发送文本消息。
        成功返回 True，失败返回 False。
        """
        if not self.kf_open_kfid:
            return False
        access_token = self._get_kf_access_token()
        if not access_token:
            return False
        url = f"https://qyapi.weixin.qq.com/cgi-bin/kf/send_msg?access_token={access_token}"
        payload = {
            "touser": touser,
            "open_kfid": self.kf_open_kfid,
            "msgtype": "text",
            "text": {"content": text},
        }
        for attempt in range(3):
            try:
                resp = requests.post(url, json=payload, timeout=5)
                data = resp.json()
            except Exception as e:
                logger.error(f"[wechatcom] kf/send_msg request failed: {e}")
                return False
            errcode = data.get("errcode", 0)
            if errcode == 0:
                logger.info("[wechatcom] kf/send_msg success, touser=%s, msgid=%s", touser, data.get("msgid"))
                return True
            if errcode == 95001:
                # WeChat rate-limit: back off and retry up to 2 more times
                wait = 2 * (attempt + 1)
                logger.warning(
                    "[wechatcom] kf/send_msg 95001 rate-limit (attempt %d/3), retrying in %ds …",
                    attempt + 1, wait,
                )
                time.sleep(wait)
                continue
            logger.error("[wechatcom] kf/send_msg error: %s", data)
            return False
        logger.error("[wechatcom] kf/send_msg failed after 3 attempts (95001 rate-limit)")
        return False

    def _kf_sync_msg(self, token: str, open_kfid: str):
        """
        Pull messages from WeChat Customer Service sync_msg API after a
        kf_msg_or_event notification.

        Fix: cursor is saved to disk after every successful sync_msg call so
        server restarts resume from the correct position and do NOT replay ALL
        historical messages (root cause of the message-flood bug: errcode 95001).
        Messages are also deduplicated by msgid (24h TTL) so that even if the
        same cursor is used twice the message is only processed once.
        Loop until has_more==0 to fully consume a burst of messages per event.

        A non-blocking lock guards against concurrent invocations.  If a new
        event arrives while a sync is already running, we set a pending flag
        instead of silently dropping — the running sync will do one extra pass
        after it finishes so no incoming messages are missed.
        """
        if not self._kf_sync_lock.acquire(blocking=False):
            self._kf_sync_pending = True
            logger.info("[wechatcom] kf_sync_msg already in progress, will re-sync after current completes")
            return
        try:
            while True:
                self._kf_sync_pending = False
                self._kf_sync_msg_inner(token, open_kfid)
                if not self._kf_sync_pending:
                    break
                logger.info("[wechatcom] kf_sync_msg: re-syncing because a new event arrived during previous sync")
        finally:
            self._kf_sync_lock.release()

    def _kf_sync_msg_inner(self, token: str, open_kfid: str):
        access_token = self._get_kf_access_token()
        if not access_token:
            logger.warning("[wechatcom] kf sync_msg: no access_token, skip")
            return

        url = f"https://qyapi.weixin.qq.com/cgi-bin/kf/sync_msg?access_token={access_token}"
        cursor = self._kf_sync_cursor.get(open_kfid, "")
        total_processed = 0

        while True:
            payload = {"token": token, "open_kfid": open_kfid, "limit": 100}
            if cursor:
                payload["cursor"] = cursor

            try:
                resp = requests.post(url, json=payload, timeout=10)
                data = resp.json()
            except Exception as e:
                logger.error("[wechatcom] kf/sync_msg request failed: %s", e)
                break

            if data.get("errcode") != 0:
                logger.error("[wechatcom] kf/sync_msg error: %s", data)
                break

            next_cursor = data.get("next_cursor") or ""
            has_more = data.get("has_more", 0)
            msg_list = data.get("msg_list") or []

            logger.info(
                "[wechatcom] kf/sync_msg got %d messages (cursor=%s, has_more=%s)",
                len(msg_list), bool(cursor), has_more,
            )

            # Always persist the new cursor even if msg_list is empty
            if next_cursor and next_cursor != cursor:
                self._kf_sync_cursor[open_kfid] = next_cursor
                self._save_kf_cursor()
                cursor = next_cursor

            for item in msg_list:
                # origin 3 = message sent by external WeChat user
                if item.get("origin") != 3:
                    continue

                external_userid = item.get("external_userid")
                msgtype = item.get("msgtype")
                msgid = item.get("msgid") or ""
                send_time = item.get("send_time") or 0

                if not external_userid:
                    continue

                # Skip historical messages that predate this server startup.
                # This prevents replaying the entire WeChat KF history when
                # the server starts with no saved cursor for this open_kfid.
                if self._startup_time and send_time and send_time < self._startup_time:
                    logger.info(
                        "[wechatcom] kf sync_msg skip pre-startup message msgid=%s send_time=%s startup=%s",
                        msgid, send_time, self._startup_time,
                    )
                    if msgid:
                        self._processed_msgids[msgid] = True
                    continue

                # Dedup: skip messages we have already queued
                if msgid and msgid in self._processed_msgids:
                    logger.info(
                        "[wechatcom] kf sync_msg skip duplicate msgid=%s external_userid=%s",
                        msgid, external_userid,
                    )
                    continue

                # ---- Build message object by type -------------------------
                wechatcom_msg = None

                if msgtype == "text":
                    text_obj = item.get("text") or {}
                    content = text_obj.get("content") or ""
                    if not content:
                        logger.debug("[wechatcom] kf sync_msg skip empty text msgid=%s", msgid)
                        if msgid:
                            self._processed_msgids[msgid] = True
                        continue

                    logger.info(
                        "[wechatcom] kf sync_msg processing text from external_userid=%s msgid=%s",
                        external_userid, msgid,
                    )

                    class _KfTextMsg:
                        type = "text"

                    kf_msg = _KfTextMsg()
                    kf_msg.content = content
                    kf_msg.source = external_userid
                    kf_msg.target = item.get("open_kfid") or open_kfid
                    kf_msg.id = msgid
                    kf_msg.time = send_time

                    try:
                        wechatcom_msg = WechatComAppMessage(kf_msg, client=self.client)
                    except NotImplementedError as e:
                        logger.info("[wechatcom] kf sync_msg text skip: %s", e)
                        if msgid:
                            self._processed_msgids[msgid] = True
                        continue

                elif msgtype == "image":
                    image_obj = item.get("image") or {}
                    media_id = image_obj.get("media_id") or ""
                    if not media_id:
                        logger.debug("[wechatcom] kf sync_msg skip image with no media_id msgid=%s", msgid)
                        if msgid:
                            self._processed_msgids[msgid] = True
                        continue

                    # Download image via KF access token before queuing
                    access_token = self._get_kf_access_token()
                    if not access_token:
                        logger.warning("[wechatcom] kf sync_msg cannot download image: no access_token")
                        if msgid:
                            self._processed_msgids[msgid] = True
                        continue

                    from common.tmp_dir import TmpDir
                    local_path = os.path.join(TmpDir().path(), media_id + ".png")
                    try:
                        dl_url = (
                            f"https://qyapi.weixin.qq.com/cgi-bin/media/get"
                            f"?access_token={access_token}&media_id={media_id}"
                        )
                        img_resp = requests.get(dl_url, timeout=20)
                        ctype = img_resp.headers.get("Content-Type", "")
                        if img_resp.status_code == 200 and "image" in ctype:
                            with open(local_path, "wb") as f:
                                f.write(img_resp.content)
                            logger.info(
                                "[wechatcom] kf sync_msg downloaded image media_id=%s -> %s",
                                media_id, local_path,
                            )
                        else:
                            logger.warning(
                                "[wechatcom] kf sync_msg image download failed: status=%s content_type=%s",
                                img_resp.status_code, ctype,
                            )
                            if msgid:
                                self._processed_msgids[msgid] = True
                            continue
                    except Exception as e:
                        logger.error("[wechatcom] kf sync_msg image download error: %s", e)
                        if msgid:
                            self._processed_msgids[msgid] = True
                        continue

                    logger.info(
                        "[wechatcom] kf sync_msg processing image from external_userid=%s msgid=%s",
                        external_userid, msgid,
                    )

                    class _KfImageMsg:
                        type = "image"

                    kf_img = _KfImageMsg()
                    kf_img.source = external_userid
                    kf_img.target = item.get("open_kfid") or open_kfid
                    kf_img.id = msgid
                    kf_img.time = send_time
                    kf_img.media_id = media_id

                    try:
                        wechatcom_msg = WechatComAppMessage(kf_img, client=self.client)
                        # Image is already downloaded; skip the lazy prepare download
                        wechatcom_msg.content = local_path
                        wechatcom_msg._prepare_fn = lambda: None
                    except NotImplementedError as e:
                        logger.info("[wechatcom] kf sync_msg image skip: %s", e)
                        if msgid:
                            self._processed_msgids[msgid] = True
                        continue

                else:
                    logger.debug("[wechatcom] kf sync_msg skip unsupported msgtype=%s msgid=%s", msgtype, msgid)
                    if msgid:
                        self._processed_msgids[msgid] = True
                    continue
                # -----------------------------------------------------------

                wechatcom_msg.external_userid = external_userid
                context = self._compose_context(
                    wechatcom_msg.ctype,
                    wechatcom_msg.content,
                    isgroup=False,
                    msg=wechatcom_msg,
                )
                if context:
                    self.produce(context)
                    total_processed += 1

                # Mark as processed after successfully queuing
                if msgid:
                    self._processed_msgids[msgid] = True

            # Stop looping when WeChat says there are no more messages
            if not has_more or not next_cursor:
                break

        if total_processed:
            logger.info("[wechatcom] kf sync_msg: queued %d new messages for processing", total_processed)

    def startup(self):
        # start message listener
        urls = ("/wxcomapp/?", "channel.wechatcom.wechatcomapp_channel.Query")
        app = web.application(urls, globals(), autoreload=False)
        port = conf().get("wechatcomapp_port", 9898)
        logger.info("[wechatcom] ✅ WeCom app channel started successfully")
        logger.info("[wechatcom] 📡 Listening on http://0.0.0.0:{}/wxcomapp/".format(port))
        logger.info("[wechatcom] 🤖 Ready to receive messages")
        # Build WSGI app with middleware (same as runsimple but without print)
        func = web.httpserver.StaticMiddleware(app.wsgifunc())
        func = web.httpserver.LogMiddleware(func)
        server = web.httpserver.WSGIServer(("0.0.0.0", port), func)
        self._http_server = server
        try:
            server.start()
        except (KeyboardInterrupt, SystemExit):
            server.stop()

    def stop(self):
        if self._http_server:
            try:
                self._http_server.stop()
                logger.info("[wechatcom] HTTP server stopped")
            except Exception as e:
                logger.warning(f"[wechatcom] Error stopping HTTP server: {e}")
            self._http_server = None

    def send(self, reply: Reply, context: Context):
        receiver = context["receiver"]
        if reply.type in [ReplyType.TEXT, ReplyType.ERROR, ReplyType.INFO]:
            reply_text = remove_markdown_symbol(reply.content)
            texts = split_string_by_utf8_length(reply_text, MAX_UTF8_LEN)
            if len(texts) > 1:
                logger.info("[wechatcom] text too long, split into {} parts".format(len(texts)))
            cmsg = context.get("msg")
            # Primary: get external_userid from the message object (real user messages)
            # Fallback: read from context directly (synthetic contexts, e.g. followup scheduler)
            ext_uid = (
                getattr(cmsg, "external_userid", None)
                if cmsg else None
            ) or context.get("external_userid")
            for i, text in enumerate(texts):
                if ext_uid:
                    # 外部微信用户（来自微信客服），只能用 kf/send_msg，不能用内部消息接口
                    if self.kf_open_kfid:
                        self._send_kf_text(ext_uid, text)
                    else:
                        logger.warning("[wechatcom] kf_open_kfid not configured, cannot reply to external user %s", ext_uid)
                else:
                    # 企业内部成员，走自建应用内部消息接口
                    self.client.message.send_text(self.agent_id, receiver, text)
                if i != len(texts) - 1:
                    time.sleep(0.5)
            logger.info("[wechatcom] Do send text to {}: {}".format(receiver, reply_text))
        elif reply.type == ReplyType.VOICE:
            try:
                media_ids = []
                file_path = reply.content
                amr_file = os.path.splitext(file_path)[0] + ".amr"
                any_to_amr(file_path, amr_file)
                duration, files = split_audio(amr_file, 60 * 1000)
                if len(files) > 1:
                    logger.info("[wechatcom] voice too long {}s > 60s , split into {} parts".format(duration / 1000.0, len(files)))
                for path in files:
                    response = self.client.media.upload("voice", open(path, "rb"))
                    logger.debug("[wechatcom] upload voice response: {}".format(response))
                    media_ids.append(response["media_id"])
            except ImportError as e:
                logger.error("[wechatcom] voice conversion failed: {}".format(e))
                logger.error("[wechatcom] please install pydub: pip install pydub")
                return
            except WeChatClientException as e:
                logger.error("[wechatcom] upload voice failed: {}".format(e))
                return
            try:
                os.remove(file_path)
                if amr_file != file_path:
                    os.remove(amr_file)
            except Exception:
                pass
            for media_id in media_ids:
                self.client.message.send_voice(self.agent_id, receiver, media_id)
                time.sleep(1)
            logger.info("[wechatcom] sendVoice={}, receiver={}".format(reply.content, receiver))
        elif reply.type == ReplyType.IMAGE_URL:  # 从网络下载图片
            img_url = reply.content
            pic_res = requests.get(img_url, stream=True)
            image_storage = io.BytesIO()
            for block in pic_res.iter_content(1024):
                image_storage.write(block)
            sz = fsize(image_storage)
            if sz >= 10 * 1024 * 1024:
                logger.info("[wechatcom] image too large, ready to compress, sz={}".format(sz))
                image_storage = compress_imgfile(image_storage, 10 * 1024 * 1024 - 1)
                logger.info("[wechatcom] image compressed, sz={}".format(fsize(image_storage)))
            image_storage.seek(0)
            if ".webp" in img_url:
                try:
                    image_storage = convert_webp_to_png(image_storage)
                except Exception as e:
                    logger.error(f"Failed to convert image: {e}")
                    return
            try:
                response = self.client.media.upload("image", image_storage)
                logger.debug("[wechatcom] upload image response: {}".format(response))
            except WeChatClientException as e:
                logger.error("[wechatcom] upload image failed: {}".format(e))
                return

            self.client.message.send_image(self.agent_id, receiver, response["media_id"])
            logger.info("[wechatcom] sendImage url={}, receiver={}".format(img_url, receiver))
        elif reply.type == ReplyType.IMAGE:  # 从文件读取图片
            image_storage = reply.content
            sz = fsize(image_storage)
            if sz >= 10 * 1024 * 1024:
                logger.info("[wechatcom] image too large, ready to compress, sz={}".format(sz))
                image_storage = compress_imgfile(image_storage, 10 * 1024 * 1024 - 1)
                logger.info("[wechatcom] image compressed, sz={}".format(fsize(image_storage)))
            image_storage.seek(0)
            try:
                response = self.client.media.upload("image", image_storage)
                logger.debug("[wechatcom] upload image response: {}".format(response))
            except WeChatClientException as e:
                logger.error("[wechatcom] upload image failed: {}".format(e))
                return
            self.client.message.send_image(self.agent_id, receiver, response["media_id"])
            logger.info("[wechatcom] sendImage, receiver={}".format(receiver))


class Query:
    def GET(self):
        channel = WechatComAppChannel()
        params = web.input()
        logger.info("[wechatcom] receive params: {}".format(params))
        try:
            signature = params.msg_signature
            timestamp = params.timestamp
            nonce = params.nonce
            echostr = params.echostr
            echostr = channel.crypto.check_signature(signature, timestamp, nonce, echostr)
        except InvalidSignatureException:
            raise web.Forbidden()
        return echostr

    def POST(self):
        channel = WechatComAppChannel()
        params = web.input()
        logger.info("[wechatcom] receive params: {}".format(params))
        try:
            signature = params.msg_signature
            timestamp = params.timestamp
            nonce = params.nonce
            message = channel.crypto.decrypt_message(web.data(), signature, timestamp, nonce)
        except (InvalidSignatureException, InvalidCorpIdException):
            raise web.Forbidden()

        # 先直接从原始 XML 读出 MsgType/Event，不依赖 wechatpy 对未知类型的解析
        try:
            root = ET.fromstring(message)
            raw_msgtype = (getattr(root.find("MsgType"), "text", "") or "").strip().lower()
            raw_event   = (getattr(root.find("Event"),   "text", "") or "").strip().lower()
        except Exception as e:
            logger.warning("[wechatcom] XML parse failed: %s", e)
            raw_msgtype, raw_event = "", ""

        logger.info("[wechatcom] raw xml MsgType=%s Event=%s", raw_msgtype, raw_event)

        # 微信客服专用事件：wechatpy 无法解析，需直接处理
        if raw_msgtype == "event" and raw_event == "kf_msg_or_event":
            token_el     = root.find("Token")
            open_kfid_el = root.find("OpenKfId")
            token     = (token_el.text     or "").strip() if token_el     is not None else ""
            open_kfid = (open_kfid_el.text or "").strip() if open_kfid_el is not None else ""
            logger.info("[wechatcom] kf_msg_or_event: token=%s open_kfid=%s", bool(token), open_kfid)
            if token and open_kfid:
                channel._kf_sync_msg(token, open_kfid)
            else:
                logger.warning("[wechatcom] kf_msg_or_event missing Token or OpenKfId")
            return "success"

        # 普通自建应用消息：交由 wechatpy 解析
        msg = parse_message(message)
        logger.debug("[wechatcom] receive message: {}, msg= {}".format(message, msg))
        if msg.type == "event":
            if msg.event == "subscribe":
                pass
        else:
            try:
                wechatcom_msg = WechatComAppMessage(msg, client=channel.client)
            except NotImplementedError as e:
                logger.info("[wechatcom] unsupported message type, skip: %s", e)
                return "success"
            wechatcom_msg.prepare()
            context = channel._compose_context(
                wechatcom_msg.ctype,
                wechatcom_msg.content,
                isgroup=False,
                msg=wechatcom_msg,
            )
            if context:
                channel.produce(context)
        return "success"
