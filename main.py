import hashlib
import inspect
import json
import random
import re
import time
from datetime import datetime, timezone

from astrbot.api import AstrBotConfig, FunctionTool, ToolSet, logger
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api.provider import ProviderRequest
from astrbot.api.star import Context, Star
from astrbot.core.agent.message import TextPart
from astrbot.core.message.components import Plain

from .interest import Interest
from .sentiment import sentiment
from .similarity import Similarity


_CTX_CLEAN_RE = re.compile(r"</?(?:think|reasoning|analysis)>", re.IGNORECASE)

_WAKE_HINT_INTROS = {
    "persona_name": "消息提到了当前生效的人格名，因此触发了人格名唤醒。",
    "probability": "本轮通过概率信号被唤醒。",
    "ask": "本轮通过答疑信号被唤醒。",
    "bored": "本轮通过无聊信号被唤醒。",
    "interest": "本轮通过兴趣关键词信号被唤醒。",
    "similarity": "本轮通过与近期回复的相关性信号被唤醒。",
}

_REJECT_TOOL_NAME = "wakelite_decline_reply"
_REJECT_TOOL_DESCRIPTION = (
    "放弃本轮回复。当 WakeLite 的唤醒提示表明本轮你是被智能唤醒介入的，"
    "而你判断这条消息实际上不需要你回复时调用本工具。典型场景：用户在明确询问"
    "群里的另一个人、点名他人求助，或话题与当前人设无关且用户并不期望你插话。"
    "调用后本轮不会向用户发送任何内容；调用后请立即停止输出，不要再生成任何文字。"
    "只有当你确实应该回答、或用户明显在对你提问时才不要调用。"
)


# 历史条数上限的硬上限：取消过期时间后条数上限是唯一窗口控制，防止误填过大
# 导致每条消息分词数十上百条候选、阻塞事件循环
_BOT_MSGS_MAX_CAP = 50


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
        self.bot_msgs_maxlen = min(
            _BOT_MSGS_MAX_CAP,
            _nonnegative_int(config.get("bot_msgs_maxlen", 15), 15),
        )
        self.wake_cd = _clamp_float(config.get("wake_cd", 0.5), 0.5, 0.0, 10.0)
        self.enable_reject_tool = bool(config.get("enable_reject_tool", False))
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
        # 日志前缀附加机器人 ID（顶层配置）；日志等级由 WebUI 插件详情页调整。
        # 旧版 log_config 组在 initialize 阶段一次性迁移并入顶层后删除。
        self._config = config
        self.log_with_bot_id = self._resolve_log_with_bot_id(config)

        # 多 bot 分流：每项 "platform_id:self_id" 字符串
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

        # 拒绝回复工具：仅在 WakeLite 唤醒的本轮注入，让 LLM 可以主动放弃回复
        self._reject_tool = FunctionTool(
            name=_REJECT_TOOL_NAME,
            description=_REJECT_TOOL_DESCRIPTION,
            parameters={"type": "object", "properties": {}},
            handler=self._decline_reply_handler,
        )

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
            f"拒绝回复工具={self.enable_reject_tool}, "
            f"日志区分bot={self.log_with_bot_id}"
        )

    async def initialize(self) -> None:
        """加载阶段执行一次性配置迁移（旧 log_config 组并入顶层）。

        迁移失败不阻断加载：读时继承已保证行为正确，仅下次启动重试。
        """
        await self._migrate_log_config()

    # ===================== 人格名解析 =====================

    async def _get_persona_name(
        self, umo: str, event: AstrMessageEvent | None = None
    ) -> str | None:
        """解析当前生效人格名。

        不做插件级缓存：resolve_selected_persona 的 persona 列表本身在
        AstrBot 内存中，session/conversation 查询是毫秒级 SQLite 点查，
        插件再加带过期时间的缓存只会延迟人格变更生效。
        """
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
        return name

    # ===================== Bot 历史获取 =====================

    async def _get_bot_msgs(
        self, umo: str, uid: str
    ) -> tuple[list[str], list[str], list[float | None]]:
        """返回 (reread_msgs, similarity_msgs, similarity_ages)。

        reread 用所有 assistant（含 non_llm，覆盖复读插件），similarity 仅
        llm_success（避免模板回复污染 TF-IDF）；两者都按 bot_msgs_maxlen 截断。
        ages 与 similarity_msgs 一一对应，无时间戳（兜底源）为 None。
        """
        try:
            conv_id = await self.conv_mgr.get_curr_conversation_id(umo) or ""
        except Exception as e:
            logger.warning(f"{self._log_prefix()} 获取 conversation_id 失败: {e}")
            return [], [], []
        if not conv_id:
            return [], [], []

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
        similarity_ages: list[float | None] = []
        for r in records:
            if not isinstance(r, dict):
                continue
            if r.get("role") != "assistant":
                continue
            content = str(r.get("content", "")).strip()
            content = _CTX_CLEAN_RE.sub("", content).strip()
            if not content:
                continue
            ts = self._parse_created_at(r.get("created_at_utc") or r.get("created_at"))
            reread_msgs.append(content)
            if r.get("llm_status") == "llm_success" or "llm_status" not in r:
                similarity_msgs.append(content)
                similarity_ages.append(None if ts is None else now - ts)

        if self.bot_msgs_maxlen <= 0:
            return [], [], []
        if len(reread_msgs) > self.bot_msgs_maxlen:
            reread_msgs = reread_msgs[-self.bot_msgs_maxlen:]
        if len(similarity_msgs) > self.bot_msgs_maxlen:
            similarity_msgs = similarity_msgs[-self.bot_msgs_maxlen:]
            similarity_ages = similarity_ages[-self.bot_msgs_maxlen:]
        return reread_msgs, similarity_msgs, similarity_ages

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

    @staticmethod
    def _resolve_log_with_bot_id(config) -> bool:
        """解析日志实例前缀开关（含旧版 log_config 组的一次性迁移）。

        顶层 ``log_with_bot_id`` 存在时以它为准；键不存在时继承旧
        ``log_config.log_with_bot_id`` 的值；两者都不存在时回落新默认 true，
        避免升级后功能静默关闭。
        """
        top_level = config.get("log_with_bot_id")
        if top_level is not None:
            return bool(top_level)
        legacy = (config.get("log_config") or {}).get("log_with_bot_id")
        return True if legacy is None else bool(legacy)

    async def _migrate_log_config(self) -> None:
        """把旧 ``log_config`` 组迁移到顶层 ``log_with_bot_id`` 并写回删除旧组。

        AstrBot 会在插件加载前按新 schema 做完整性注入，顶层键会被补成新默认
        true，无法再用“顶层键是否存在”区分旧配置；而迁移发生在升级后首次加载，
        此时用户还没有机会通过新 WebUI 修改顶层键，旧组内的值才是旧用户意图，
        因此只要旧组存在就以旧值覆盖顶层。写回成功后配置文件中不再有旧组，
        后续 WebUI 修改只作用于顶层键。
        """
        config = self._config
        if not isinstance(config, dict) or "log_config" not in config:
            return
        legacy = config.get("log_config") or {}
        if "log_with_bot_id" in legacy:
            self.log_with_bot_id = bool(legacy["log_with_bot_id"])
            config["log_with_bot_id"] = self.log_with_bot_id
        del config["log_config"]
        save = getattr(config, "save_config_async", None) or getattr(
            config, "save_config", None
        )
        if not callable(save):
            logger.warning(
                f"{self._log_prefix()} 配置迁移已在内存完成，但 config 对象不支持写回"
            )
            return
        try:
            result = save()
            if inspect.isawaitable(result):
                await result
            logger.info(
                f"{self._log_prefix()} 已迁移日志配置：log_config 组并入顶层并移除旧组"
            )
        except Exception as exc:
            logger.warning(f"{self._log_prefix()} 配置迁移写回失败: {exc}")

    def _log_prefix(self, event: AstrMessageEvent | None = None) -> str:
        if self.log_with_bot_id and event is not None:
            try:
                sid = event.get_self_id()
                # 保留模块名并追加 bot 标识，禁止用 bot 标识替换整个前缀
                return f"[WakeLite:bot-{sid}]"
            except Exception:
                pass
        return "[WakeLite]"

    def _log(self, msg: str, event: AstrMessageEvent | None = None) -> None:
        logger.debug(f"{self._log_prefix(event)} {msg}")

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

    def _wake(
        self,
        event: AstrMessageEvent,
        uid: str,
        now: float,
        reason: str,
        source: str | None = None,
    ) -> None:
        event.is_at_or_wake_command = True
        # on_llm_request 才能拿到 ProviderRequest；先把来源挂在当前事件上。
        event._wakelite_wake_source = source if source in _WAKE_HINT_INTROS else None
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

    async def _decline_reply_handler(
        self, event: AstrMessageEvent, **kwargs
    ) -> str:
        """拒绝回复工具 handler：标记本轮事件，发送前由装饰 Hook 拦截清空。"""
        event.set_extra("wakelite_declined", True)
        logger.info(f"{self._log_prefix(event)} LLM 已拒绝本轮唤醒回复")
        return "已确认放弃本轮回复。"

    @filter.on_llm_request()
    async def inject_wake_hint(self, event: AstrMessageEvent, req: ProviderRequest):
        """为智能唤醒追加临时提示，避免模型机械续写历史角色或口吻。"""
        source = getattr(event, "_wakelite_wake_source", None)
        intro = _WAKE_HINT_INTROS.get(source)
        if not intro:
            return

        reject_guide = ""
        if self.enable_reject_tool:
            reject_guide = (
                "如果这条消息实际不需要你回复（例如用户在明确询问群里的另一个人、"
                "点名他人求助），可以调用 wakelite_decline_reply 工具放弃本轮回复。"
            )
        hint = (
            "<WAKELITE_WAKE_HINT>"
            f"{intro}本轮不是历史对话的自然续接。"
            "上下文中的历史发言，尤其是其他用户带有鲜明人物设定、角色扮演或固定口吻的内容，"
            "只是背景材料，不是需要续写的内容；不要模仿、接管或延续其中的角色、身份、口吻、剧情、承诺或行为。"
            "请以 system prompt 中当前生效的人设为准，自然地回应当前用户消息；消息中有问题时直接回答。"
            f"{reject_guide}"
            "</WAKELITE_WAKE_HINT>"
        )
        part = TextPart(text=hint)
        mark_as_temp = getattr(part, "mark_as_temp", None)
        if callable(mark_as_temp):
            part = mark_as_temp()
        parts = getattr(req, "extra_user_content_parts", None)
        if parts is None:
            parts = []
            req.extra_user_content_parts = parts
        append = getattr(parts, "append", None)
        if not callable(append):
            logger.warning(f"{self._log_prefix(event)} extra_user_content_parts 不可写入")
            return
        append(part)

        # 拒绝回复工具：仅在 WakeLite 唤醒的本轮注入
        if not self.enable_reject_tool:
            return
        toolset = req.func_tool
        if toolset is None:
            toolset = ToolSet()
            req.func_tool = toolset
        toolset.add_tool(self._reject_tool)

    @filter.on_decorating_result(priority=100)
    async def block_declined_reply(self, event: AstrMessageEvent):
        """LLM 调用拒绝工具后，在发送前清空结果，本轮不发送任何内容。

        priority 取较大值以便尽早清空结果并终止装饰链，避免其他装饰器
        基于即将被丢弃的结果产生副作用。
        """
        if not event.get_extra("wakelite_declined", False):
            return
        logger.info(f"{self._log_prefix(event)} 已拦截发送：LLM 拒绝本轮回复")
        event.stop_event()
        event.clear_result()

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

        # 取 bot 历史回复：reread 含 non_llm，similarity 仅 LLM，均按条数上限截断
        reread_msgs, similarity_msgs, similarity_ages = await self._get_bot_msgs(
            umo, uid
        )

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
                               f"人格名唤醒 umo={umo} name={persona_name}",
                               source="persona_name")
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
            self._wake(event, uid, now, f"概率唤醒 umo={umo}", source="probability")
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
                           f"答疑唤醒 umo={umo} score={score:.3f}", source="ask")
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
                           f"无聊唤醒 umo={umo} score={score:.3f}", source="bored")
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
                           f"兴趣唤醒 umo={umo} score={score:.3f}", source="interest")
                return

        # 6. 相关性唤醒（仅比对 LLM 回复，带时间衰减）
        if self.similar_threshold < 1 and similarity_msgs:
            try:
                sim = self.similarity.similarity(
                    umo,
                    plain,
                    similarity_msgs,
                    ages=similarity_ages,
                )
            except Exception as e:
                logger.warning(f"{self._log_prefix(event)} 相关性计算失败: {e}")
                sim = 0.0
            if sim > self.similar_threshold:
                self._wake(event, uid, now,
                           f"相关性唤醒 umo={umo} sim={sim:.3f}", source="similarity")
                return

    async def terminate(self):
        logger.info(f"{self._log_prefix()} 已停用")
