import hashlib
import json
import random
import re
import time
from datetime import datetime, timezone

from astrbot.api import logger, AstrBotConfig
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.star import Context, Star
from astrbot.core.message.components import Plain

from .interest import Interest
from .sentiment import sentiment
from .similarity import Similarity


_CTX_CLEAN_RE = re.compile(r"</?(?:think|reasoning|analysis)>", re.IGNORECASE)


def _clamp_float(value, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _nonnegative_int(value, default: int) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return default


class WakeLitePlugin(Star):
    """轻量唤醒：人格名/概率/答疑/无聊/兴趣/相关性 + 群白名单 + CD + 复读过滤 + 多 bot 分流。"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config

        self.persona_name_prob = _clamp_float(
            config.get("persona_name_prob", 0.5), 0.5, 0.0, 1.0
        )
        self.prob = _clamp_float(config.get("prob", 0.0), 0.0, 0.0, 1.0)
        self.ask_threshold = _clamp_float(
            config.get("ask_threshold", 0.5), 0.5, 0.0, 1.0
        )
        self.bored_threshold = _clamp_float(
            config.get("bored_threshold", 0.5), 0.5, 0.0, 1.0
        )
        self.interest_threshold = _clamp_float(
            config.get("interest_threshold", 0.5), 0.5, 0.0, 1.0
        )
        self.similar_threshold = _clamp_float(
            config.get("similar_threshold", 0.5), 0.5, 0.0, 1.0
        )
        self.bot_msgs_maxlen = _nonnegative_int(config.get("bot_msgs_maxlen", 5), 5)
        # 配置单位为分钟，内部统一转秒
        self.bot_msgs_ttl = _nonnegative_int(config.get("bot_msgs_ttl", 10), 10) * 60
        self.persona_name_cache_ttl = _nonnegative_int(
            config.get("persona_name_cache_ttl", 1), 1
        ) * 60
        self.wake_cd = _clamp_float(config.get("wake_cd", 0.5), 0.5, 0.0, 10.0)
        self.use_chat_memory = bool(config.get("use_chat_memory", True))
        history_scope = str(config.get("history_scope", "group") or "group").strip().lower()
        self.history_scope = history_scope if history_scope in {"user", "group"} else "group"
        whitelist_groups = config.get("whitelist_groups", []) or []
        if not isinstance(whitelist_groups, (list, tuple, set)):
            whitelist_groups = []
        self.whitelist_groups: set[str] = set(
            str(g) for g in whitelist_groups
        )
        interest_words_str = config.get("interest_words", []) or []
        if not isinstance(interest_words_str, (list, tuple)):
            interest_words_str = []
        self.interest_words: list[list[str]] = [
            [w for w in s.split() if w]
            for s in interest_words_str
            if isinstance(s, str)
        ]
        # 多 bot 分流：每项 "platform_id:self_id" 字符串
        log_conf = config.get("log_config", {}) or {}
        if not isinstance(log_conf, dict):
            log_conf = {}
        self.log_with_bot_id = bool(log_conf.get("log_with_bot_id", True))
        self.debug_to_info = bool(log_conf.get("debug_to_info", False))

        bots_raw = config.get("bots", []) or []
        if not isinstance(bots_raw, (list, tuple)):
            bots_raw = []
        self.bots: list[tuple[str, str]] = []
        self.bots_index: dict[tuple[str, str], int] = {}
        for entry in bots_raw:
            s = entry.strip() if isinstance(entry, str) else ""
            if not s:
                continue
            if ":" not in s:
                logger.warning(
                    f"{self._log_prefix()} bots 配置项格式错误（应为 platform_id:self_id）：{s}"
                )
                continue
            pid, sid = s.split(":", 1)
            pid, sid = pid.strip(), sid.strip()
            if not (pid and sid):
                logger.warning(
                    f"{self._log_prefix()} bots 配置项字段不能为空：{s}"
                )
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
        # (UMO, user_id) -> last wake timestamp，避免跨群/跨平台互相影响
        self._last_wake: dict[tuple[str, str], float] = {}
        self._runtime_ops = 0

        logger.info(
            f"{self._log_prefix()} 已加载：人格名={self.persona_name_prob}, "
            f"概率={self.prob}, 答疑={self.ask_threshold}, "
            f"无聊={self.bored_threshold}, 兴趣={self.interest_threshold}, "
            f"相关性={self.similar_threshold}, CD={self.wake_cd}s, "
            f"白名单群={len(self.whitelist_groups)}个, "
            f"兴趣关键词包={len(self.interest_words)}个, "
            f"分流bots={len(self.bots)}个, "
            f"使用chat_memory={self.use_chat_memory}, "
            f"历史范围={self.history_scope}, "
            f"日志区分bot={self.log_with_bot_id}, "
            f"调试提级={self.debug_to_info}"
        )

    # ===================== 人格名缓存 =====================

    async def _get_persona_name(
        self, umo: str, event: AstrMessageEvent | None = None
    ) -> str | None:
        cached = self._persona_name_cache.get(umo)
        now = time.time()
        if (
            self.persona_name_cache_ttl > 0
            and cached
            and now - cached[1] < self.persona_name_cache_ttl
        ):
            return cached[0]
        persona = None
        try:
            conversation_persona_id = None
            try:
                conv_id = await self.conv_mgr.get_curr_conversation_id(umo) or ""
                if conv_id:
                    conversation = await self.conv_mgr.get_conversation(umo, conv_id)
                    conversation_persona_id = getattr(conversation, "persona_id", None)
            except Exception as e:
                self._log(f"读取 conversation persona 失败 umo={umo}: {e}", event=event)

            provider_settings = {}
            try:
                runtime_config = self.context.get_config(umo=umo) or {}
                provider_settings = runtime_config.get("provider_settings", {}) or {}
            except Exception:
                provider_settings = {}

            resolved, persona, _, _ = await self.persona_mgr.resolve_selected_persona(
                umo=umo,
                conversation_persona_id=conversation_persona_id,
                platform_name=event.get_platform_name() if event else "",
                provider_settings=provider_settings,
            )
            if persona is None and resolved and resolved != "[%None]":
                getter = getattr(self.persona_mgr, "get_persona_v3_by_id", None)
                if getter is not None:
                    persona = getter(resolved)
        except Exception as e:
            logger.warning(f"{self._log_prefix(event)} 获取人格失败 umo={umo}: {e}")
            try:
                persona = await self.persona_mgr.get_default_persona_v3(umo)
            except Exception:
                return None
        if isinstance(persona, dict):
            name = persona.get("name")
        else:
            name = getattr(persona, "name", None) if persona else None
        if name and self.persona_name_cache_ttl > 0:
            self._persona_name_cache[umo] = (name, now)
            self._log(f"人格名缓存更新 umo={umo} name={name}", event=event)
        return name

    # ===================== Bot 历史获取 =====================

    async def _get_bot_msgs(self, umo: str, uid: str) -> tuple[list[str], list[str]]:
        """返回 (reread_msgs, similarity_msgs)。

        reread 用所有 assistant（含 non_llm，覆盖复读插件），
        similarity 仅 llm_success（避免模板回复污染 TF-IDF）。
        """
        try:
            conv_id = await self.conv_mgr.get_curr_conversation_id(umo) or ""
        except Exception as e:
            logger.warning(f"{self._log_prefix()} 获取 conversation_id 失败: {e}")
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
                ts = self._parse_created_at(
                    r.get("created_at_utc") or r.get("created_at")
                )
                if ts is not None and now - ts > self.bot_msgs_ttl:
                    continue
            reread_msgs.append(content)
            if r.get("llm_status") == "llm_success" or "llm_status" not in r:
                similarity_msgs.append(content)

        if self.bot_msgs_maxlen <= 0:
            return [], []
        if len(reread_msgs) > self.bot_msgs_maxlen:
            reread_msgs = reread_msgs[-self.bot_msgs_maxlen:]
        if len(similarity_msgs) > self.bot_msgs_maxlen:
            similarity_msgs = similarity_msgs[-self.bot_msgs_maxlen:]
        return reread_msgs, similarity_msgs

    async def _query_chat_memory(self, umo: str, conv_id: str, uid: str) -> list[dict]:
        """从 chat_memory v2.3+ 查 assistant 消息，llm_status 分流交给上层。"""
        star = self.context.get_registered_star("chat_memory")
        if star is None:
            return []
        candidate = getattr(star, "star", None) or getattr(star, "star_cls", None)
        query = getattr(candidate, "query_history", None)
        if query is None:
            return []
        try:
            limit = max(self.bot_msgs_maxlen, 5)
            user_filter = uid if self.history_scope == "user" else None
            return await query(
                umo,
                conv_id,
                user_filter,
                limit=limit,
                role_filter="assistant",
            )
        except Exception as e:
            logger.warning(f"{self._log_prefix()} chat_memory 查询失败: {e}")
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
            logger.warning(f"{self._log_prefix()} 读取 AstrBot 上下文失败: {e}")
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
            text = str(value).strip().replace("Z", "+00:00")
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                # chat_memory 的数据库时间统一为 UTC naive；不依赖宿主机本地时区。
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.timestamp()
        except (TypeError, ValueError):
            return None

    # ===================== 日志 =====================

    def _log_prefix(self, event: AstrMessageEvent | None = None) -> str:
        if self.log_with_bot_id and event is not None:
            try:
                sid = event.get_self_id()
                return f"[WakeLite:{sid}]"
            except Exception:
                pass
        return "[WakeLite]"

    def _log(self, msg: str, event: AstrMessageEvent | None = None) -> None:
        line = f"{self._log_prefix(event)} {msg}"
        if self.debug_to_info:
            logger.info(line)
        else:
            logger.debug(line)

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
        self._last_wake[(event.unified_msg_origin, uid)] = now
        logger.info(f"{self._log_prefix(event)} {reason}")

    def _maintain_runtime_state(self, now: float) -> None:
        """惰性清理长期运行状态，避免用户/会话数量无限增长。"""
        self._runtime_ops += 1
        if self._runtime_ops % 256:
            return
        wake_cutoff = now - max(self.wake_cd * 2, 300.0)
        self._last_wake = {
            key: ts for key, ts in self._last_wake.items() if ts >= wake_cutoff
        }
        if self.persona_name_cache_ttl <= 0:
            self._persona_name_cache.clear()
        else:
            cache_cutoff = now - self.persona_name_cache_ttl
            self._persona_name_cache = {
                key: value
                for key, value in self._persona_name_cache.items()
                if value[1] >= cache_cutoff
            }

    # ===================== 多 bot 分流 =====================

    @staticmethod
    def _stable_hash(event: AstrMessageEvent) -> int:
        """稳定哈希：同一消息在所有 bot 上算出同一 int 值，供多 bot 分流。

        只用 (group_id, sender_id, content) 三个跨 bot 一致字段。
        弃用 message_id（OneBot 不同实现间可能不一致）和 umo（含 platform_id，
        每 bot 不同）。
        """
        group_id = event.get_group_id() or ""
        sender = event.get_sender_id()
        content = event.message_str or ""
        key = f"{group_id}|{sender}|{content}"
        return int(hashlib.md5(key.encode("utf-8")).hexdigest()[:8], 16)

    def _compute_my_turn(self, event: AstrMessageEvent, uid: str, bid: str) -> bool:
        """当前 bot 是否轮到跑阈值/概率类判定（多 bot 分流）。

        未配置 bots → True；当前 bot 不在列表 → False；
        用户消息 → 全部 bots 池；bot 消息 → 除去发送者的池。
        """
        if not self.bots:
            return True

        my_pid = event.get_platform_id()
        my_key = (my_pid, bid)
        if my_key not in self.bots_index:
            self._log(
                f"({my_pid}, {bid}) 不在 bots 列表，跳过阈值分流",
                event=event,
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
        self._maintain_runtime_state(now)

        # 唤醒 CD（每用户独立）
        if self.wake_cd > 0:
            last = self._last_wake.get((umo, uid), 0.0)
            if now - last < self.wake_cd:
                self._log(
                    f"唤醒CD中 uid={uid} 剩余{self.wake_cd - (now - last):.2f}s",
                    event=event,
                )
                return

        # 取 bot 历史回复：reread 用全量（含 non_llm），similarity 仅 LLM
        reread_msgs, similarity_msgs = await self._get_bot_msgs(umo, uid)

        # 复读过滤（含 non_llm，识别复读插件等场景）
        if self._is_reread(plain, reread_msgs):
            self._log(f"复读拦截 umo={umo}", event=event)
            return

        # 多 bot 分流：未轮到我则跳过阈值/概率类判定（人格名仍跑）
        is_my_turn = self._compute_my_turn(event, uid, bid)

        # 1. 人格名唤醒（所有 bot 都跑，不受分流影响）
        if self.persona_name_prob > 0:
            persona_name = await self._get_persona_name(umo, event=event)
            if persona_name and persona_name in plain:
                if random.random() < self.persona_name_prob:
                    self._wake(event, uid, now,
                               f"人格名唤醒 umo={umo} name={persona_name}")
                    return
                self._log(
                    f"人格名命中但概率未过 umo={umo} name={persona_name}",
                    event=event,
                )

        # 下面 5 项受多 bot 分流影响：未轮到我则跳过
        if not is_my_turn:
            self._log(f"非本 bot 分流轮次 uid={uid} 跳过阈值判定", event=event)
            return

        # 2. 概率唤醒
        if self.prob > 0 and random.random() < self.prob:
            self._wake(event, uid, now, f"概率唤醒 umo={umo}")
            return

        # 3. 答疑唤醒
        if self.ask_threshold < 1:
            try:
                score = sentiment.ask(plain)
            except Exception as e:
                logger.warning(f"{self._log_prefix(event)} 答疑打分失败: {e}")
                score = 0.0
            if score > self.ask_threshold:
                self._wake(event, uid, now,
                           f"答疑唤醒 umo={umo} score={score:.3f}")
                return

        # 4. 无聊唤醒
        if self.bored_threshold < 1:
            try:
                score = sentiment.bored(plain)
            except Exception as e:
                logger.warning(f"{self._log_prefix(event)} 无聊打分失败: {e}")
                score = 0.0
            if score > self.bored_threshold:
                self._wake(event, uid, now,
                           f"无聊唤醒 umo={umo} score={score:.3f}")
                return

        # 5. 兴趣唤醒
        if self.interest_threshold < 1 and self.interest_words:
            try:
                score = self.interest.calc_interest(plain)
            except Exception as e:
                logger.warning(f"{self._log_prefix(event)} 兴趣打分失败: {e}")
                score = 0.0
            if score > self.interest_threshold:
                self._wake(event, uid, now,
                           f"兴趣唤醒 umo={umo} score={score:.3f}")
                return

        # 6. 相关性唤醒（仅比对 LLM 回复）
        if self.similar_threshold < 1 and similarity_msgs:
            try:
                sim = self.similarity.similarity(umo, plain, similarity_msgs)
            except Exception as e:
                logger.warning(f"{self._log_prefix(event)} 相关性计算失败: {e}")
                sim = 0.0
            if sim > self.similar_threshold:
                self._wake(event, uid, now,
                           f"相关性唤醒 umo={umo} sim={sim:.3f}")
                return

    async def terminate(self):
        logger.info(f"{self._log_prefix()} 已停用")
