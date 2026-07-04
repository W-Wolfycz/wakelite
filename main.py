import hashlib
import json
import random
import re
import time
from datetime import datetime

from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.core.message.components import Plain

from .interest import Interest
from .sentiment import sentiment
from .similarity import Similarity


_CTX_CLEAN_RE = re.compile(r"<[^>]+>")


class WakeLitePlugin(Star):
    """轻量唤醒：人格名（按概率）+ 概率 + 答疑 + 无聊 + 兴趣 + 相关性。
    含群聊白名单准入、唤醒 CD、复读过滤、多 bot 分流。
    bot 历史来源：chat_memory（推荐）或 AstrBot 自带 conversation history。
    """

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        self.persona_name_prob = float(config.get("persona_name_prob", 0.5))
        self.prob = float(config.get("prob", 0.0))
        self.ask_threshold = float(config.get("ask_threshold", 0.5))
        self.bored_threshold = float(config.get("bored_threshold", 0.5))
        self.interest_threshold = float(config.get("interest_threshold", 0.5))
        self.similar_threshold = float(config.get("similar_threshold", 0.5))
        self.bot_msgs_maxlen = int(config.get("bot_msgs_maxlen", 5))
        # 配置单位为分钟，内部统一转秒
        self.bot_msgs_ttl = int(config.get("bot_msgs_ttl", 10)) * 60
        self.persona_name_cache_ttl = int(config.get("persona_name_cache_ttl", 1)) * 60
        self.wake_cd = float(config.get("wake_cd", 0.5))
        self.use_chat_memory = bool(config.get("use_chat_memory", True))
        self.whitelist_groups: set[str] = set(
            str(g) for g in config.get("whitelist_groups", [])
        )
        interest_words_str = config.get("interest_words", [])
        self.interest_words: list[list[str]] = [
            [w for w in s.split() if w] for s in interest_words_str
        ]
        # 多 bot 分流：bots 顺序列表 + 快速查位置 dict
        # 每项是 (platform_id, self_id) 元组，用户配置 bots list of {platform_id, self_id}
        bots_raw = config.get("bots", []) or []
        self.bots: list[tuple[str, str]] = []
        self.bots_index: dict[tuple[str, str], int] = {}
        for b in bots_raw:
            if not isinstance(b, dict):
                continue
            pid = str(b.get("platform_id", "")).strip()
            sid = str(b.get("self_id", "")).strip()
            if not (pid and sid):
                continue
            key = (pid, sid)
            if key in self.bots_index:
                continue  # 去重
            self.bots_index[key] = len(self.bots)
            self.bots.append(key)

        self.persona_mgr = context.persona_manager
        self.conv_mgr = context.conversation_manager
        self.similarity = Similarity()
        self.interest = Interest(self.interest_words)

        # umo -> (name, timestamp)
        self._persona_name_cache: dict[str, tuple[str, float]] = {}
        # uid -> last wake timestamp
        self._last_wake: dict[str, float] = {}

        logger.info(
            f"[WakeLite] 已加载：人格名={self.persona_name_prob}, "
            f"概率={self.prob}, 答疑={self.ask_threshold}, "
            f"无聊={self.bored_threshold}, 兴趣={self.interest_threshold}, "
            f"相关性={self.similar_threshold}, CD={self.wake_cd}s, "
            f"白名单群={len(self.whitelist_groups)}个, "
            f"兴趣关键词包={len(self.interest_words)}个, "
            f"分流bots={len(self.bots)}个, "
            f"使用chat_memory={self.use_chat_memory}"
        )

    # ===================== 人格名缓存 =====================

    async def _get_persona_name(self, umo: str) -> str | None:
        cached = self._persona_name_cache.get(umo)
        now = time.time()
        if cached and now - cached[1] < self.persona_name_cache_ttl:
            return cached[0]
        try:
            persona = await self.persona_mgr.get_default_persona_v3(umo)
        except Exception as e:
            logger.warning(f"[WakeLite] 获取人格失败 umo={umo}: {e}")
            return None
        name = persona.get("name") if persona else None
        if name:
            self._persona_name_cache[umo] = (name, now)
            logger.debug(f"[WakeLite] 人格名缓存更新 umo={umo} name={name}")
        return name

    # ===================== Bot 历史获取 =====================

    async def _get_bot_msgs(self, umo: str, uid: str) -> tuple[list[str], list[str]]:
        """获取本会话近期 bot 回复，按 tag 分流。

        返回 (reread_msgs, similarity_msgs)：
        - reread_msgs: 所有 assistant 消息（含 non_llm 命令回复、复读插件等），
          用于复读检测——避免用户/ bot 互相复读无限循环
        - similarity_msgs: 仅 tag='llm_success'（LLM 真实回复），
          用于相关性唤醒——避免 /help 这类模板回复污染 TF-IDF

        AstrBot history 兜底路径无 tag 字段，统一视为 llm_success（同时进两个 list）。
        每个分支独立 maxlen 截断 + TTL 过滤。
        """
        try:
            conv_id = await self.conv_mgr.get_curr_conversation_id(umo) or ""
        except Exception as e:
            logger.warning(f"[WakeLite] 获取 conversation_id 失败: {e}")
            return [], []
        if not conv_id:
            return [], []

        if self.use_chat_memory:
            records = await self._query_chat_memory(umo, conv_id, uid)
            if not records:
                # chat_memory 没数据时尝试 AstrBot 自带兜底
                records = await self._read_astrbot_history(umo, conv_id)
        else:
            records = await self._read_astrbot_history(umo, conv_id)

        now = time.time()
        reread_msgs: list[str] = []
        similarity_msgs: list[str] = []
        for r in records:
            if not isinstance(r, dict):
                continue
            if r.get("role") != "assistant":
                continue
            content = str(r.get("content", "")).strip()
            content = _CTX_CLEAN_RE.sub("", content).strip()
            if not content:
                continue
            # TTL 过滤（best-effort，仅 chat_memory 有 created_at）
            if self.bot_msgs_ttl > 0:
                ts = self._parse_created_at(r.get("created_at"))
                if ts is not None and now - ts > self.bot_msgs_ttl:
                    continue
            reread_msgs.append(content)
            # 兜底路径无 tag → 视为 LLM 回复（保留旧行为）
            if r.get("tag") == "llm_success" or "tag" not in r:
                similarity_msgs.append(content)

        if self.bot_msgs_maxlen <= 0:
            return [], []
        if len(reread_msgs) > self.bot_msgs_maxlen:
            reread_msgs = reread_msgs[-self.bot_msgs_maxlen:]
        if len(similarity_msgs) > self.bot_msgs_maxlen:
            similarity_msgs = similarity_msgs[-self.bot_msgs_maxlen:]
        return reread_msgs, similarity_msgs

    async def _query_chat_memory(self, umo: str, conv_id: str, uid: str) -> list[dict]:
        """调用 chat_memory v2.0+ 的 query_history。

        只在 SQL 层过滤 role='assistant'，不限制 tag——tag 分流交给
        _get_bot_msgs 在 Python 层做（reread 要宽，similarity 要严）。
        """
        star = self.context.get_registered_star("chat_memory")
        if star is None:
            return []
        candidate = getattr(star, "star", None) or getattr(star, "star_cls", None)
        query = getattr(candidate, "query_history", None)
        if query is None:
            return []
        try:
            limit = max(self.bot_msgs_maxlen, 5)
            return await query(
                umo, conv_id, uid,
                limit=limit,
                role_filter="assistant",
            )
        except Exception as e:
            logger.warning(f"[WakeLite] chat_memory 查询失败: {e}")
            return []

    async def _read_astrbot_history(self, umo: str, conv_id: str) -> list[dict]:
        """从 AstrBot 自带 conversation history 读取。"""
        try:
            conv = await self.conv_mgr.get_conversation(umo, conv_id)
            if not conv or not conv.history:
                return []
            raw = json.loads(conv.history) if isinstance(conv.history, str) else conv.history
            if not isinstance(raw, list):
                return []
            result: list[dict] = []
            for msg in raw:
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role", "")
                content = str(msg.get("content", "")).strip()
                if role in ("user", "assistant") and content:
                    result.append({"role": role, "content": content})
            return result
        except Exception as e:
            logger.warning(f"[WakeLite] 读取 AstrBot 上下文失败: {e}")
            return []

    @staticmethod
    def _parse_created_at(value) -> float | None:
        """best-effort 解析时间戳，失败返回 None。"""
        if not value:
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            pass
        try:
            return datetime.fromisoformat(str(value)).timestamp()
        except (TypeError, ValueError):
            return None

    # ===================== 通用工具 =====================

    @staticmethod
    def _get_plain(event: AstrMessageEvent) -> str:
        if event.message_str:
            return event.message_str.strip()
        plains = [seg.text for seg in event.get_messages() if isinstance(seg, Plain)]
        return " ".join(plains).strip()

    @staticmethod
    def _normalize(text: str) -> str:
        """去掉标点、空白、大小写差异，用于复读比对。"""
        return re.sub(r"[^\w一-鿿]", "", text).lower()

    def _is_reread(self, plain: str, bot_msgs: list[str]) -> bool:
        """用户消息与 bot 历史某条消息完全相同（去标点后）即视为复读。"""
        cleaned = self._normalize(plain)
        if not cleaned:
            return False
        for msg in bot_msgs:
            if msg and self._normalize(msg) == cleaned:
                return True
        return False

    def _wake(self, event: AstrMessageEvent, uid: str, now: float, reason: str) -> None:
        event.is_at_or_wake_command = True
        self._last_wake[uid] = now
        logger.info(reason)

    # ===================== 多 bot 分流 =====================

    @staticmethod
    def _stable_hash(event: AstrMessageEvent) -> int:
        """稳定哈希：同一消息在所有 bot 上算出同一 int 值，供多 bot 分流。

        输入只取「消息本身」相关字段，不含时间、平台实例、bot ID：
          - 优先 message_id（平台原生 ID，所有 bot 看到同一条消息时一致）
          - 缺失时退到 umo + sender_id + content
        返回大整数，调用方 mod N 即可映射到 [0, N)。
        """
        msg_obj = getattr(event, "message_obj", None)
        msg_id = getattr(msg_obj, "message_id", "") if msg_obj else ""
        if msg_id:
            key = f"mid:{msg_id}"
        else:
            umo = event.unified_msg_origin
            sender = event.get_sender_id()
            content = event.message_str or ""
            key = f"{umo}|{sender}|{content}"
        return int(hashlib.md5(key.encode("utf-8")).hexdigest()[:8], 16)

    def _compute_my_turn(self, event: AstrMessageEvent, uid: str, bid: str) -> bool:
        """计算当前 bot 是否轮到跑阈值/概率类判定（多 bot 分流）。

        - 未配置 bots → 单 bot 模式，永远返回 True
        - self_id 不在 bots 列表 → 返回 False（配置错误，消息丢就丢）
        - 用户消息（sender 不在 bots）→ 池大小 = N，原 bots 列表
        - bot 消息（sender 在 bots）→ 池大小 = N-1，原 bots 列表除去 sender

        人格名唤醒、复读过滤、CD、白名单 gate 不调用本方法（所有 bot 都跑）。
        """
        if not self.bots:
            return True

        my_pid = event.get_platform_id()
        my_key = (my_pid, bid)
        if my_key not in self.bots_index:
            logger.debug(
                f"[WakeLite] ({my_pid}, {bid}) 不在 bots 列表，跳过阈值分流"
            )
            return False

        sender_key = (my_pid, uid)
        if sender_key in self.bots_index:
            # 情况 2：bot 消息，原列表除去发送者
            active = [k for k in self.bots if k != sender_key]
        else:
            # 情况 1：用户消息，全部 bots
            active = self.bots

        if not active:
            return False

        try:
            my_pos = active.index(my_key)
        except ValueError:
            return False

        h = self._stable_hash(event)
        return h % len(active) == my_pos

    # ===================== Hook =====================

    @filter.event_message_type(filter.EventMessageType.ALL, priority=50)
    async def on_message(self, event: AstrMessageEvent):
        uid = event.get_sender_id()
        bid = event.get_self_id()
        if uid == bid:
            return

        # 仅处理白名单内的群聊
        gid = event.get_group_id()
        if not gid or str(gid) not in self.whitelist_groups:
            return

        umo = event.unified_msg_origin
        plain = self._get_plain(event)
        if not plain:
            return

        now = time.time()

        # 唤醒 CD（每用户独立）
        if self.wake_cd > 0:
            last = self._last_wake.get(uid, 0.0)
            if now - last < self.wake_cd:
                logger.debug(
                    f"[WakeLite] 唤醒CD中 uid={uid} "
                    f"剩余{self.wake_cd - (now - last):.2f}s"
                )
                return

        # 取 bot 历史回复：reread 用全量（含 non_llm），similarity 仅 LLM
        reread_msgs, similarity_msgs = await self._get_bot_msgs(umo, uid)

        # 复读过滤（含 non_llm，识别复读插件等场景）
        if self._is_reread(plain, reread_msgs):
            logger.debug(f"[WakeLite] 复读拦截 umo={umo}")
            return

        # 多 bot 分流：未轮到我则跳过阈值/概率类判定（人格名仍跑）
        is_my_turn = self._compute_my_turn(event, uid, bid)

        # 1. 人格名唤醒（命中后按概率；所有 bot 都跑，不受分流影响）
        if self.persona_name_prob > 0:
            persona_name = await self._get_persona_name(umo)
            if persona_name and persona_name in plain:
                if random.random() < self.persona_name_prob:
                    self._wake(event, uid, now,
                               f"[WakeLite] 人格名唤醒 umo={umo} name={persona_name}")
                    return
                logger.debug(
                    f"[WakeLite] 人格名命中但概率未过 umo={umo} name={persona_name}"
                )

        # 下面 5 项受多 bot 分流影响：未轮到我则跳过
        if not is_my_turn:
            logger.debug(f"[WakeLite] 非本 bot 分流轮次 uid={uid} 跳过阈值判定")
            return

        # 2. 概率唤醒
        if self.prob > 0 and random.random() < self.prob:
            self._wake(event, uid, now, f"[WakeLite] 概率唤醒 umo={umo}")
            return

        # 3. 答疑唤醒
        if self.ask_threshold < 1:
            try:
                score = sentiment.ask(plain)
            except Exception as e:
                logger.warning(f"[WakeLite] 答疑打分失败: {e}")
                score = 0.0
            if score > self.ask_threshold:
                self._wake(event, uid, now,
                           f"[WakeLite] 答疑唤醒 umo={umo} score={score:.3f}")
                return

        # 4. 无聊唤醒
        if self.bored_threshold < 1:
            try:
                score = sentiment.bored(plain)
            except Exception as e:
                logger.warning(f"[WakeLite] 无聊打分失败: {e}")
                score = 0.0
            if score > self.bored_threshold:
                self._wake(event, uid, now,
                           f"[WakeLite] 无聊唤醒 umo={umo} score={score:.3f}")
                return

        # 5. 兴趣唤醒
        if self.interest_threshold < 1 and self.interest_words:
            try:
                score = self.interest.calc_interest(plain)
            except Exception as e:
                logger.warning(f"[WakeLite] 兴趣打分失败: {e}")
                score = 0.0
            if score > self.interest_threshold:
                self._wake(event, uid, now,
                           f"[WakeLite] 兴趣唤醒 umo={umo} score={score:.3f}")
                return

        # 6. 相关性唤醒（最贵，最后；仅比对 LLM 回复，避免模板污染）
        if self.similar_threshold < 1 and similarity_msgs:
            try:
                sim = self.similarity.similarity(umo, plain, similarity_msgs)
            except Exception as e:
                logger.warning(f"[WakeLite] 相关性计算失败: {e}")
                sim = 0.0
            if sim > self.similar_threshold:
                self._wake(event, uid, now,
                           f"[WakeLite] 相关性唤醒 umo={umo} sim={sim:.3f}")
                return

    async def terminate(self):
        logger.info("[WakeLite] 已停用")
