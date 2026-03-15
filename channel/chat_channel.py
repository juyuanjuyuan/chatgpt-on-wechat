import os
import re
import threading
import time
from asyncio import CancelledError
from concurrent.futures import Future, ThreadPoolExecutor

from bridge.context import *
from bridge.reply import *
from channel.channel import Channel
from common.dequeue import Dequeue
from common import memory
from common.cowagent_runtime import MCPRuntimeClient, is_underage, is_photo_refusal, increase_refusal_and_check_stop
from plugins import *

try:
    from voice.audio_convert import any_to_wav
except Exception as e:
    pass

handler_pool = ThreadPoolExecutor(max_workers=8)  # 处理消息的线程池
runtime_client = MCPRuntimeClient()


# 抽象类, 它包含了与消息通道无关的通用处理逻辑
class ChatChannel(Channel):
    name = None  # 登录的用户名
    user_id = None  # 登录的用户id

    def __init__(self):
        # Instance-level attributes so each channel subclass has its own
        # independent session queue and lock. Previously these were class-level,
        # which caused contexts from one channel (e.g. Feishu) to be consumed
        # by another channel's consume() thread (e.g. Web), leading to errors
        # like "No request_id found in context".
        self.futures = {}
        self.sessions = {}
        self.lock = threading.Lock()
        # Message batching buffer: accumulates TEXT messages per session for
        # msg_batch_window_seconds before forwarding the merged message to the AI.
        self._msg_buffer = {}   # session_id -> {"contexts": [...], "timer": Timer}
        self._buffer_lock = threading.Lock()
        _thread = threading.Thread(target=self.consume)
        _thread.setDaemon(True)
        _thread.start()

    # 根据消息构造context，消息内容相关的触发项写在这里
    def _compose_context(self, ctype: ContextType, content, **kwargs):
        context = Context(ctype, content)
        context.kwargs = kwargs
        if "channel_type" not in context:
            context["channel_type"] = self.channel_type
        if "origin_ctype" not in context:
            context["origin_ctype"] = ctype
        # context首次传入时，receiver是None，根据类型设置receiver
        first_in = "receiver" not in context
        # 群名匹配过程，设置session_id和receiver
        if first_in:  # context首次传入时，receiver是None，根据类型设置receiver
            config = conf()
            cmsg = context["msg"]
            user_data = conf().get_user_data(cmsg.from_user_id)
            context["openai_api_key"] = user_data.get("openai_api_key")
            context["gpt_model"] = user_data.get("gpt_model")
            if context.get("isgroup", False):
                group_name = cmsg.other_user_nickname
                group_id = cmsg.other_user_id

                group_name_white_list = config.get("group_name_white_list", [])
                group_name_keyword_white_list = config.get("group_name_keyword_white_list", [])
                if any(
                    [
                        group_name in group_name_white_list,
                        "ALL_GROUP" in group_name_white_list,
                        check_contain(group_name, group_name_keyword_white_list),
                    ]
                ):
                    # Check global group_shared_session config first
                    group_shared_session = conf().get("group_shared_session", True)
                    if group_shared_session:
                        # All users in the group share the same session
                        session_id = group_id
                    else:
                        # Check group-specific whitelist (legacy behavior)
                        group_chat_in_one_session = conf().get("group_chat_in_one_session", [])
                        session_id = cmsg.actual_user_id
                        if any(
                            [
                                group_name in group_chat_in_one_session,
                                "ALL_GROUP" in group_chat_in_one_session,
                            ]
                        ):
                            session_id = group_id
                else:
                    logger.debug(f"No need reply, groupName not in whitelist, group_name={group_name}")
                    return None
                context["session_id"] = session_id
                context["receiver"] = group_id
            else:
                context["session_id"] = cmsg.other_user_id
                context["receiver"] = cmsg.other_user_id
            e_context = PluginManager().emit_event(EventContext(Event.ON_RECEIVE_MESSAGE, {"channel": self, "context": context}))
            context = e_context["context"]
            if e_context.is_pass() or context is None:
                return context
            if cmsg.from_user_id == self.user_id and not config.get("trigger_by_self", True):
                logger.debug("[chat_channel]self message skipped")
                return None

        # 消息内容匹配过程，并处理content
        if ctype == ContextType.TEXT:
            if first_in and "」\n- - - - - - -" in content:  # 初次匹配 过滤引用消息
                logger.debug(content)
                logger.debug("[chat_channel]reference query skipped")
                return None

            nick_name_black_list = conf().get("nick_name_black_list", [])
            if context.get("isgroup", False):  # 群聊
                # 校验关键字
                match_prefix = check_prefix(content, conf().get("group_chat_prefix"))
                match_contain = check_contain(content, conf().get("group_chat_keyword"))
                flag = False
                if context["msg"].to_user_id != context["msg"].actual_user_id:
                    if match_prefix is not None or match_contain is not None:
                        flag = True
                        if match_prefix:
                            content = content.replace(match_prefix, "", 1).strip()
                    if context["msg"].is_at:
                        nick_name = context["msg"].actual_user_nickname
                        if nick_name and nick_name in nick_name_black_list:
                            # 黑名单过滤
                            logger.warning(f"[chat_channel] Nickname {nick_name} in In BlackList, ignore")
                            return None

                        logger.info("[chat_channel]receive group at")
                        if not conf().get("group_at_off", False):
                            flag = True
                        self.name = self.name if self.name is not None else ""  # 部分渠道self.name可能没有赋值
                        pattern = f"@{re.escape(self.name)}(\u2005|\u0020)"
                        subtract_res = re.sub(pattern, r"", content)
                        if isinstance(context["msg"].at_list, list):
                            for at in context["msg"].at_list:
                                pattern = f"@{re.escape(at)}(\u2005|\u0020)"
                                subtract_res = re.sub(pattern, r"", subtract_res)
                        if subtract_res == content and context["msg"].self_display_name:
                            # 前缀移除后没有变化，使用群昵称再次移除
                            pattern = f"@{re.escape(context['msg'].self_display_name)}(\u2005|\u0020)"
                            subtract_res = re.sub(pattern, r"", content)
                        content = subtract_res
                if not flag:
                    if context["origin_ctype"] == ContextType.VOICE:
                        logger.info("[chat_channel]receive group voice, but checkprefix didn't match")
                    return None
            else:  # 单聊
                nick_name = context["msg"].from_user_nickname
                if nick_name and nick_name in nick_name_black_list:
                    # 黑名单过滤
                    logger.warning(f"[chat_channel] Nickname '{nick_name}' in In BlackList, ignore")
                    return None

                match_prefix = check_prefix(content, conf().get("single_chat_prefix", [""]))
                if match_prefix is not None:  # 判断如果匹配到自定义前缀，则返回过滤掉前缀+空格后的内容
                    content = content.replace(match_prefix, "", 1).strip()
                elif context["origin_ctype"] == ContextType.VOICE:  # 如果源消息是私聊的语音消息，允许不匹配前缀，放宽条件
                    pass
                else:
                    logger.info("[chat_channel]receive single chat msg, but checkprefix didn't match")
                    return None
            content = content.strip()
            img_match_prefix = check_prefix(content, conf().get("image_create_prefix",[""]))
            if img_match_prefix:
                content = content.replace(img_match_prefix, "", 1)
                context.type = ContextType.IMAGE_CREATE
            else:
                context.type = ContextType.TEXT
            context.content = content.strip()
            if "desire_rtype" not in context and conf().get("always_reply_voice") and ReplyType.VOICE not in self.NOT_SUPPORT_REPLYTYPE:
                context["desire_rtype"] = ReplyType.VOICE
        elif context.type == ContextType.VOICE:
            if "desire_rtype" not in context and conf().get("voice_reply_voice") and ReplyType.VOICE not in self.NOT_SUPPORT_REPLYTYPE:
                context["desire_rtype"] = ReplyType.VOICE
        return context

    def _handle(self, context: Context):
        if context is None or not context.content:
            return
        cmsg = context.get("msg")
        external_id = None
        session_key = context.get("session_id")
        channel_type = context.get("channel_type", "unknown")
        if cmsg:
            external_id = getattr(cmsg, "other_user_id", None)
            runtime_client.upsert_candidate(external_id=external_id, nickname=getattr(cmsg, "from_user_nickname", None))
            msg_type = str(context.type).split(".")[-1].lower()
            _non_text_labels = {ContextType.IMAGE: "[图片]", ContextType.VOICE: "[语音]", ContextType.VIDEO: "[视频]", ContextType.FILE: "[文件]"}
            msg_content = _non_text_labels.get(context.type, str(context.content))
            runtime_client.log_message(
                external_id=external_id,
                session_key=session_key,
                channel=channel_type,
                sender="user",
                message_type=msg_type,
                content=msg_content,
            )
            if context.type == ContextType.TEXT and is_underage(str(context.content)):
                runtime_client.add_event(external_id, session_key, "underage_stop")
                runtime_client._post("/candidates/upsert", {"external_id": external_id, "status": "underage_terminated"})
                self._send_reply(context, Reply(ReplyType.TEXT, "抱歉宝子，Welike 招募仅面向年满 18 岁的应聘者，先不继续流程啦。"))
                runtime_client.log_message(external_id, session_key, channel_type, "assistant", "text", "抱歉宝子，Welike 招募仅面向年满 18 岁的应聘者，先不继续流程啦。")
                return
            if context.type == ContextType.TEXT and is_photo_refusal(str(context.content)):
                runtime_client.add_event(external_id, session_key, "photo_refused_n", {"content": str(context.content)})
                if increase_refusal_and_check_stop(str(session_key)):
                    runtime_client._post("/candidates/upsert", {"external_id": external_id, "status": "pending_photo"})
                    stop_msg = "了解啦宝子，不强求你发照片～你后续如果愿意再发我，随时找我就行。"
                    self._send_reply(context, Reply(ReplyType.TEXT, stop_msg))
                    runtime_client.log_message(external_id, session_key, channel_type, "assistant", "text", stop_msg)
                    return

        logger.debug("[chat_channel] handling context: {}".format(context))
        # reply的构建步骤
        reply = self._generate_reply(context)

        logger.debug("[chat_channel] decorating reply: {}".format(reply))

        # reply的包装步骤
        if reply and reply.content:
            # 检测 AI 发出的对话结束信号，直接丢弃，不发送给用户
            # 用 splitlines 兼容模型在 /end_conversation 前后带少量多余文本的情况
            _reply_lines = [l.strip() for l in str(reply.content).splitlines() if l.strip()]
            if _reply_lines == ["/end_conversation"]:
                logger.info("[chat_channel] AI signaled end_conversation, suppressing reply to channel")
                if external_id:
                    # 记录为事件而非消息，避免被 _restore_history 当作普通 assistant 消息还原进上下文
                    runtime_client.add_event(external_id, session_key, "end_conversation")
                return

            reply = self._decorate_reply(context, reply)

            # reply的发送步骤
            self._send_reply(context, reply)
            if external_id:
                runtime_client.log_message(external_id, session_key, channel_type, "assistant", str(reply.type).split(".")[-1].lower(), str(reply.content))

    def _generate_reply(self, context: Context, reply: Reply = Reply()) -> Reply:
        e_context = PluginManager().emit_event(
            EventContext(
                Event.ON_HANDLE_CONTEXT,
                {"channel": self, "context": context, "reply": reply},
            )
        )
        reply = e_context["reply"]
        if not e_context.is_pass():
            logger.debug("[chat_channel] type={}, content={}".format(context.type, context.content))
            if context.type == ContextType.TEXT or context.type == ContextType.IMAGE_CREATE:  # 文字和图片消息
                context["channel"] = e_context["channel"]
                reply = super().build_reply_content(context.content, context)
            elif context.type == ContextType.VOICE:  # 语音消息
                cmsg = context["msg"]
                cmsg.prepare()
                file_path = context.content
                wav_path = os.path.splitext(file_path)[0] + ".wav"
                try:
                    any_to_wav(file_path, wav_path)
                except Exception as e:  # 转换失败，直接使用mp3，对于某些api，mp3也可以识别
                    logger.warning("[chat_channel]any to wav error, use raw path. " + str(e))
                    wav_path = file_path
                # 语音识别
                reply = super().build_voice_to_text(wav_path)
                # 删除临时文件
                try:
                    os.remove(file_path)
                    if wav_path != file_path:
                        os.remove(wav_path)
                except Exception as e:
                    pass
                    # logger.warning("[chat_channel]delete temp file error: " + str(e))

                if reply.type == ReplyType.TEXT:
                    new_context = self._compose_context(ContextType.TEXT, reply.content, **context.kwargs)
                    if new_context:
                        reply = self._generate_reply(new_context)
                    else:
                        return
            elif context.type == ContextType.IMAGE:  # 图片消息，当前仅做下载保存到本地的逻辑
                memory.USER_IMAGE_CACHE[context["session_id"]] = {
                    "path": context.content,
                    "msg": context.get("msg")
                }
                cmsg = context.get("msg")
                external_id = getattr(cmsg, "other_user_id", None) if cmsg else None
                channel_type = context.get("channel_type", "unknown")
                if external_id:
                    runtime_client.add_event(external_id, context.get("session_id"), "photo_received")
                    runtime_client.upload_photo(external_id, context.get("session_id"), channel_type, context.content)
                    return Reply(ReplyType.TEXT, "收到啦宝子～我这边提交审核，1–3 天审核出结果通知你～")
            elif context.type == ContextType.SHARING:  # 分享信息，当前无默认逻辑
                pass
            elif context.type == ContextType.FUNCTION or context.type == ContextType.FILE:  # 文件消息及函数调用等，当前无默认逻辑
                pass
            else:
                logger.warning("[chat_channel] unknown context type: {}".format(context.type))
                return
        return reply

    def _decorate_reply(self, context: Context, reply: Reply) -> Reply:
        if reply and reply.type:
            e_context = PluginManager().emit_event(
                EventContext(
                    Event.ON_DECORATE_REPLY,
                    {"channel": self, "context": context, "reply": reply},
                )
            )
            reply = e_context["reply"]
            desire_rtype = context.get("desire_rtype")
            if not e_context.is_pass() and reply and reply.type:
                if reply.type in self.NOT_SUPPORT_REPLYTYPE:
                    logger.error("[chat_channel]reply type not support: " + str(reply.type))
                    reply.type = ReplyType.ERROR
                    reply.content = "不支持发送的消息类型: " + str(reply.type)

                if reply.type == ReplyType.TEXT:
                    reply_text = reply.content
                    if desire_rtype == ReplyType.VOICE and ReplyType.VOICE not in self.NOT_SUPPORT_REPLYTYPE:
                        reply = super().build_text_to_voice(reply.content)
                        return self._decorate_reply(context, reply)
                    if context.get("isgroup", False):
                        if not context.get("no_need_at", False):
                            reply_text = "@" + context["msg"].actual_user_nickname + "\n" + reply_text.strip()
                        reply_text = conf().get("group_chat_reply_prefix", "") + reply_text + conf().get("group_chat_reply_suffix", "")
                    else:
                        reply_text = conf().get("single_chat_reply_prefix", "") + reply_text + conf().get("single_chat_reply_suffix", "")
                    reply.content = reply_text
                elif reply.type == ReplyType.ERROR or reply.type == ReplyType.INFO:
                    reply.content = "[" + str(reply.type) + "]\n" + reply.content
                elif reply.type == ReplyType.IMAGE_URL or reply.type == ReplyType.VOICE or reply.type == ReplyType.IMAGE or reply.type == ReplyType.FILE or reply.type == ReplyType.VIDEO or reply.type == ReplyType.VIDEO_URL:
                    pass
                else:
                    logger.error("[chat_channel] unknown reply type: {}".format(reply.type))
                    return
            if desire_rtype and desire_rtype != reply.type and reply.type not in [ReplyType.ERROR, ReplyType.INFO]:
                logger.warning("[chat_channel] desire_rtype: {}, but reply type: {}".format(context.get("desire_rtype"), reply.type))
            return reply

    def _send_reply(self, context: Context, reply: Reply):
        if reply and reply.type:
            e_context = PluginManager().emit_event(
                EventContext(
                    Event.ON_SEND_REPLY,
                    {"channel": self, "context": context, "reply": reply},
                )
            )
            reply = e_context["reply"]
            if not e_context.is_pass() and reply and reply.type:
                logger.debug("[chat_channel] sending reply: {}, context: {}".format(reply, context))

                # 文本回复：按空行分段，模拟人类一段一段发送；每段之间等待 5 秒。
                if reply.type == ReplyType.TEXT:
                    self._send_text_segments(reply, context)
                # 如果是图片回复但带有文本内容，先发一段文本再发图片
                elif reply.type == ReplyType.IMAGE_URL and hasattr(reply, "text_content") and reply.text_content:
                    text_reply = Reply(ReplyType.TEXT, reply.text_content)
                    self._send_text_segments(text_reply, context)
                    time.sleep(0.3)
                    self._send(reply, context)
                else:
                    self._send(reply, context)

    def _send_text_segments(self, reply: Reply, context: Context, gap_seconds: int = 5):
        """
        将一条长文本按“空行”拆成多段，模拟人类一段一段发送。
        规则：
        - 连续出现的空行视为一处分段点
        - 每段非空文本通过 _extract_and_send_images 发送（保持图片提取逻辑）
        - 段与段之间 sleep gap_seconds 秒
        """
        content = str(reply.content or "")
        # 使用空行作为分隔符：任意包含非空白字符之间的空行都视作分段
        raw_parts = re.split(r"\n\s*\n+", content)
        parts = [p.strip() for p in raw_parts if p and p.strip()]

        if not parts:
            # 没有有效内容时，退化为原始发送逻辑
            self._extract_and_send_images(reply, context)
            return

        for idx, part in enumerate(parts):
            seg_reply = Reply(ReplyType.TEXT, part)
            self._extract_and_send_images(seg_reply, context)
            # 段与段之间等待 gap_seconds 秒（最后一段之后不再等待）
            if idx != len(parts) - 1:
                time.sleep(gap_seconds)

    def _extract_and_send_images(self, reply: Reply, context: Context):
        """
        从文本回复中提取图片/视频URL并单独发送
        支持格式：[图片: /path/to/image.png], [视频: /path/to/video.mp4], ![](url), <img src="url">
        最多发送5个媒体文件
        """
        content = reply.content
        media_items = []  # [(url, type), ...]
        
        # 正则提取各种格式的媒体URL
        patterns = [
            (r'\[图片:\s*([^\]]+)\]', 'image'),   # [图片: /path/to/image.png]
            (r'\[视频:\s*([^\]]+)\]', 'video'),   # [视频: /path/to/video.mp4]
            (r'!\[.*?\]\(([^\)]+)\)', 'image'),   # ![alt](url) - 默认图片
            (r'<img[^>]+src=["\']([^"\']+)["\']', 'image'),  # <img src="url">
            (r'<video[^>]+src=["\']([^"\']+)["\']', 'video'),  # <video src="url">
            (r'https?://[^\s]+\.(?:jpg|jpeg|png|gif|webp)', 'image'),  # 直接的图片URL
            (r'https?://[^\s]+\.(?:mp4|avi|mov|wmv|flv)', 'video'),  # 直接的视频URL
        ]
        
        for pattern, media_type in patterns:
            matches = re.findall(pattern, content, re.IGNORECASE)
            for match in matches:
                media_items.append((match, media_type))
        
        # 去重（保持顺序）并限制最多5个
        seen = set()
        unique_items = []
        for url, mtype in media_items:
            if url not in seen:
                seen.add(url)
                unique_items.append((url, mtype))
        media_items = unique_items[:5]
        
        if media_items:
            logger.info(f"[chat_channel] Extracted {len(media_items)} media item(s) from reply")
            
            # 先发送文本（保持原文本不变）
            logger.info(f"[chat_channel] Sending text content before media: {reply.content[:100]}...")
            self._send(reply, context)
            logger.info(f"[chat_channel] Text sent, now sending {len(media_items)} media item(s)")
            
            # 然后逐个发送媒体文件
            for i, (url, media_type) in enumerate(media_items):
                try:
                    # 判断是本地文件还是URL
                    if url.startswith(('http://', 'https://')):
                        # 网络资源
                        if media_type == 'video':
                            # 视频使用 FILE 类型发送
                            media_reply = Reply(ReplyType.FILE, url)
                            media_reply.file_name = os.path.basename(url)
                        else:
                            # 图片使用 IMAGE_URL 类型
                            media_reply = Reply(ReplyType.IMAGE_URL, url)
                    elif os.path.exists(url):
                        # 本地文件
                        if media_type == 'video':
                            # 视频使用 FILE 类型，转换为 file:// URL
                            media_reply = Reply(ReplyType.FILE, f"file://{url}")
                            media_reply.file_name = os.path.basename(url)
                        else:
                            # 图片使用 IMAGE_URL 类型，转换为 file:// URL
                            media_reply = Reply(ReplyType.IMAGE_URL, f"file://{url}")
                    else:
                        logger.warning(f"[chat_channel] Media file not found or invalid URL: {url}")
                        continue
                    
                    # 发送媒体文件（添加小延迟避免频率限制）
                    if i > 0:
                        time.sleep(0.5)
                    self._send(media_reply, context)
                    logger.info(f"[chat_channel] Sent {media_type} {i+1}/{len(media_items)}: {url[:50]}...")
                    
                except Exception as e:
                    logger.error(f"[chat_channel] Failed to send {media_type} {url}: {e}")
        else:
            # 没有媒体文件，正常发送文本
                self._send(reply, context)

    def _send(self, reply: Reply, context: Context, retry_cnt=0):
        try:
            self.send(reply, context)
        except Exception as e:
            logger.error("[chat_channel] sendMsg error: {}".format(str(e)))
            if isinstance(e, NotImplementedError):
                return
            logger.exception(e)
            if retry_cnt < 2:
                time.sleep(3 + 3 * retry_cnt)
                self._send(reply, context, retry_cnt + 1)

    def _success_callback(self, session_id, **kwargs):  # 线程正常结束时的回调函数
        logger.debug("Worker return success, session_id = {}".format(session_id))

    def _fail_callback(self, session_id, exception, **kwargs):  # 线程异常结束时的回调函数
        logger.exception("Worker return exception: {}".format(exception))

    def _thread_pool_callback(self, session_id, **kwargs):
        def func(worker: Future):
            try:
                worker_exception = worker.exception()
                if worker_exception:
                    self._fail_callback(session_id, exception=worker_exception, **kwargs)
                else:
                    self._success_callback(session_id, **kwargs)
            except CancelledError as e:
                logger.info("Worker cancelled, session_id = {}".format(session_id))
            except Exception as e:
                logger.exception("Worker raise exception: {}".format(e))
            with self.lock:
                self.sessions[session_id][1].release()

        return func

    def produce(self, context: Context):
        session_id = context["session_id"]

        # Buffer TEXT messages (except admin commands) for humanized batching.
        # All messages arriving within msg_batch_window_seconds of the first one
        # are merged into a single context before being forwarded to the AI.
        if context.type == ContextType.TEXT and not context.content.startswith("#"):
            batch_window = conf().get("msg_batch_window_seconds", 30)
            if batch_window > 0:
                with self._buffer_lock:
                    if session_id in self._msg_buffer:
                        # Timer already running; just append to the existing batch.
                        self._msg_buffer[session_id]["contexts"].append(context)
                        logger.debug(
                            "[chat_channel] buffered text msg for session %s (total: %d)",
                            session_id, len(self._msg_buffer[session_id]["contexts"]),
                        )
                    else:
                        # First message in this batch: start the countdown timer.
                        timer = threading.Timer(
                            batch_window, self._flush_buffer, args=[session_id]
                        )
                        timer.daemon = True
                        self._msg_buffer[session_id] = {
                            "contexts": [context],
                            "timer": timer,
                        }
                        timer.start()
                        logger.debug(
                            "[chat_channel] started %ds batch window for session %s",
                            batch_window, session_id,
                        )
                return  # Wait for the timer to flush; do not enqueue yet.

        # Non-TEXT messages (images, voice, etc.) and admin "#" commands bypass
        # the buffer and go straight into the processing queue.
        self._enqueue_context(context)

    def _enqueue_context(self, context: Context):
        """Put a context directly onto the per-session processing queue."""
        session_id = context["session_id"]
        with self.lock:
            if session_id not in self.sessions:
                self.sessions[session_id] = [
                    Dequeue(),
                    threading.BoundedSemaphore(conf().get("concurrency_in_session", 4)),
                ]
            if context.type == ContextType.TEXT and context.content.startswith("#"):
                self.sessions[session_id][0].putleft(context)  # 优先处理管理命令
            else:
                self.sessions[session_id][0].put(context)

    def _flush_buffer(self, session_id: str):
        """Timer callback: merge all buffered TEXT messages and enqueue them."""
        with self._buffer_lock:
            if session_id not in self._msg_buffer:
                return
            entry = self._msg_buffer.pop(session_id)

        contexts = entry["contexts"]
        if not contexts:
            return

        if len(contexts) == 1:
            merged_context = contexts[0]
            logger.debug(
                "[chat_channel] flushing 1 buffered message for session %s", session_id
            )
        else:
            # Combine the text of all messages with newline separators, then
            # carry the first message's metadata (receiver, msg, session_id, …)
            # so the rest of the pipeline works unchanged.
            combined_content = "\n".join(c.content for c in contexts)
            merged_context = contexts[0]
            merged_context.content = combined_content
            logger.info(
                "[chat_channel] flushing %d buffered messages for session %s "
                "(merged len=%d chars)",
                len(contexts), session_id, len(combined_content),
            )

        self._enqueue_context(merged_context)

    # 消费者函数，单独线程，用于从消息队列中取出消息并处理
    def consume(self):
        while True:
            with self.lock:
                session_ids = list(self.sessions.keys())
            for session_id in session_ids:
                with self.lock:
                    context_queue, semaphore = self.sessions[session_id]
                if semaphore.acquire(blocking=False):  # 等线程处理完毕才能删除
                    if not context_queue.empty():
                        context = context_queue.get()
                        logger.debug("[chat_channel] consume context: {}".format(context))
                        future: Future = handler_pool.submit(self._handle, context)
                        future.add_done_callback(self._thread_pool_callback(session_id, context=context))
                        with self.lock:
                            if session_id not in self.futures:
                                self.futures[session_id] = []
                            self.futures[session_id].append(future)
                    elif semaphore._initial_value == semaphore._value + 1:  # 除了当前，没有任务再申请到信号量，说明所有任务都处理完毕
                        with self.lock:
                            self.futures[session_id] = [t for t in self.futures[session_id] if not t.done()]
                            assert len(self.futures[session_id]) == 0, "thread pool error"
                            del self.sessions[session_id]
                    else:
                        semaphore.release()
            time.sleep(0.2)

    # 取消session_id对应的所有任务，只能取消排队的消息和已提交线程池但未执行的任务
    def cancel_session(self, session_id):
        # Cancel any pending batch-buffer timer for this session first.
        with self._buffer_lock:
            if session_id in self._msg_buffer:
                self._msg_buffer[session_id]["timer"].cancel()
                del self._msg_buffer[session_id]
        with self.lock:
            if session_id in self.sessions:
                for future in self.futures[session_id]:
                    future.cancel()
                cnt = self.sessions[session_id][0].qsize()
                if cnt > 0:
                    logger.info("Cancel {} messages in session {}".format(cnt, session_id))
                self.sessions[session_id][0] = Dequeue()

    def cancel_all_session(self):
        # Cancel all pending batch-buffer timers first.
        with self._buffer_lock:
            for entry in self._msg_buffer.values():
                entry["timer"].cancel()
            self._msg_buffer.clear()
        with self.lock:
            for session_id in self.sessions:
                for future in self.futures[session_id]:
                    future.cancel()
                cnt = self.sessions[session_id][0].qsize()
                if cnt > 0:
                    logger.info("Cancel {} messages in session {}".format(cnt, session_id))
                self.sessions[session_id][0] = Dequeue()


def check_prefix(content, prefix_list):
    if not prefix_list:
        return None
    for prefix in prefix_list:
        if content.startswith(prefix):
            return prefix
    return None


def check_contain(content, keyword_list):
    if not keyword_list:
        return None
    for ky in keyword_list:
        if content.find(ky) != -1:
            return True
    return None
