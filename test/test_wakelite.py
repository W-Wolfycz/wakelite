import dataclasses
import json
import sys
import time
import types
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import jieba


def _install_astrbot_stubs() -> None:
    if "astrbot.api" in sys.modules:
        return

    class Logger:
        def debug(self, *args, **kwargs):
            pass

        def info(self, *args, **kwargs):
            pass

        def warning(self, *args, **kwargs):
            pass

    class Filter:
        class EventMessageType:
            ALL = "all"

        @staticmethod
        def event_message_type(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

        @staticmethod
        def on_llm_request(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

        @staticmethod
        def on_decorating_result(*args, **kwargs):
            def decorator(func):
                return func

            return decorator

    class Star:
        def __init__(self, context):
            self.context = context

    class Plain:
        def __init__(self, text=""):
            self.text = text

    class At:
        def __init__(self, qq=""):
            self.qq = qq

    class Reply:
        def __init__(self, sender_id=0):
            self.sender_id = sender_id

    modules = {
        "astrbot": types.ModuleType("astrbot"),
        "astrbot.api": types.ModuleType("astrbot.api"),
        "astrbot.api.event": types.ModuleType("astrbot.api.event"),
        "astrbot.api.provider": types.ModuleType("astrbot.api.provider"),
        "astrbot.api.star": types.ModuleType("astrbot.api.star"),
        "astrbot.core": types.ModuleType("astrbot.core"),
        "astrbot.core.agent": types.ModuleType("astrbot.core.agent"),
        "astrbot.core.agent.message": types.ModuleType("astrbot.core.agent.message"),
        "astrbot.core.message": types.ModuleType("astrbot.core.message"),
        "astrbot.core.message.components": types.ModuleType(
            "astrbot.core.message.components"
        ),
    }
    modules["astrbot.api"].logger = Logger()
    modules["astrbot.api"].AstrBotConfig = dict
    modules["astrbot.api.event"].filter = Filter()
    modules["astrbot.api.event"].AstrMessageEvent = object
    modules["astrbot.api.provider"].ProviderRequest = object
    modules["astrbot.api.star"].Context = object
    modules["astrbot.api.star"].Star = Star

    @dataclasses.dataclass
    class FunctionTool:
        name: str
        description: str = ""
        parameters: dict = dataclasses.field(default_factory=dict)
        handler: object = None
        active: bool = True

    class ToolSet:
        def __init__(self):
            self.tools = []

        def add_tool(self, tool):
            self.tools.append(tool)

        def names(self):
            return [tool.name for tool in self.tools]

        def __bool__(self):
            return bool(self.tools)

    modules["astrbot.api"].FunctionTool = FunctionTool
    modules["astrbot.api"].ToolSet = ToolSet

    class TextPart:
        def __init__(self, text=""):
            self.text = text
            self._no_save = False

        def mark_as_temp(self):
            self._no_save = True
            return self

    modules["astrbot.core.agent.message"].TextPart = TextPart
    modules["astrbot.core.message.components"].Plain = Plain
    modules["astrbot.core.message.components"].At = At
    modules["astrbot.core.message.components"].Reply = Reply
    sys.modules.update(modules)


_install_astrbot_stubs()

from astrbot.api import FunctionTool, ToolSet
from astrbot.core.message.components import At, Plain, Reply
from wakelite import main as plugin_main
from wakelite.main import WakeLitePlugin
from wakelite.sentiment import sentiment
from wakelite.similarity import Similarity


class Conversation:
    persona_id = "persona_conversation"
    history = "[]"


class ConversationManager:
    async def get_curr_conversation_id(self, umo):
        return "conversation_demo"

    async def get_conversation(self, umo, conversation_id):
        return Conversation()


class PersonaManager:
    def __init__(self):
        self.resolve_kwargs = None
        self.resolve_count = 0

    async def resolve_selected_persona(self, **kwargs):
        self.resolve_kwargs = kwargs
        self.resolve_count += 1
        return "persona_conversation", {"name": "会话人格"}, None, False

    def get_persona_v3_by_id(self, persona_id):
        return {"name": persona_id}

    async def get_default_persona_v3(self, umo):
        return {"name": "默认人格"}


class QueryRecorder:
    def __init__(self):
        self.args = None
        self.calls = []
        self.records = []
        # 按 user_filter 返回不同记录（None = 全量查询）
        self.records_by_user = {}

    async def query_history(self, *args, **kwargs):
        self.args = (args, kwargs)
        self.calls.append((args, kwargs))
        user_filter = args[2] if len(args) > 2 else kwargs.get("user_filter")
        if self.records_by_user:
            return self.records_by_user.get(user_filter, [])
        return self.records


class ProviderRequest:
    def __init__(self):
        self.extra_user_content_parts = []
        self.func_tool = None


class Context:
    def __init__(self):
        self.persona_manager = PersonaManager()
        self.conversation_manager = ConversationManager()
        self.query_recorder = QueryRecorder()

    def get_config(self, umo=None):
        return {"provider_settings": {"default_personality": "persona_default"}}

    def get_registered_star(self, name):
        if name != "chat_memory":
            return None
        return types.SimpleNamespace(star_cls=self.query_recorder)


class Event:
    unified_msg_origin = "platform_demo:GroupMessage:group_demo"
    is_at_or_wake_command = False

    def __init__(self):
        self._extras = {}
        self._result = None
        self._force_stopped = False
        self.message_str = "用户消息"
        self.message_obj = types.SimpleNamespace(message_str=self.message_str)
        self.messages = []
        self.group_id = "group_demo"
        self.sender_id = "10002"
        self.self_id = "10001"

    def get_platform_name(self):
        return "aiocqhttp"

    def get_platform_id(self):
        return "BOT1"

    def get_self_id(self):
        return self.self_id

    def get_sender_id(self):
        return self.sender_id

    def get_group_id(self):
        return self.group_id

    def get_messages(self):
        return self.messages

    def set_extra(self, key, value):
        self._extras[key] = value

    def get_extra(self, key=None, default=None):
        if key is None:
            return self._extras
        return self._extras.get(key, default)

    def set_result(self, result):
        self._result = result

    def get_result(self):
        return self._result

    def clear_result(self):
        self._result = None

    def stop_event(self):
        self._force_stopped = True

    def is_stopped(self):
        return self._force_stopped


def make_plugin(**overrides):
    config = {
        "whitelist_groups": ["group_demo"],
        "interest_words": [],
        **overrides,
    }
    return WakeLitePlugin(Context(), config)


class DomainTests(unittest.TestCase):

    def test_negation_reduces_bored_score(self):
        self.assertLess(sentiment.bored("我不无聊"), sentiment.bored("我好无聊"))

    def test_similarity_prefers_related_message(self):
        similarity = Similarity(bot_template_threshold=0)
        related = similarity.similarity(
            "group_demo",
            "咖啡怎么手冲",
            ["手冲咖啡需要控制水温与研磨度"],
        )
        unrelated = similarity.similarity(
            "group_demo",
            "咖啡怎么手冲",
            ["今天上海天气晴朗适合散步"],
        )
        self.assertGreater(related, unrelated)

    def test_invalid_interest_items_are_ignored(self):
        plugin = make_plugin(interest_words=["咖啡 手冲", None, 123])
        self.assertEqual(plugin.interest_words, [["咖啡", "手冲"]])

    def test_reread_ignores_short_messages(self):
        # 守静默失效：短句被误判复读（「好的」「收到」）→ 常见应答应被吞掉
        plugin = make_plugin()
        self.assertFalse(plugin._is_reread("好的", ["好的"]))
        self.assertFalse(plugin._is_reread("收到", ["收到"]))
        self.assertTrue(plugin._is_reread("好的收到", ["好的收到"]))

    def test_numeric_config_is_clamped(self):
        plugin = make_plugin(
            prob=2,
            ask_threshold=-1,
            wake_cd=99,
            bot_msgs_maxlen=-5,
        )
        self.assertEqual(plugin.prob, 1.0)
        self.assertEqual(plugin.ask_threshold, 0.0)
        self.assertEqual(plugin.wake_cd, 10.0)
        self.assertEqual(plugin.bot_msgs_maxlen, 0)

    def test_history_limit_capped_at_maximum(self):
        plugin = make_plugin(bot_msgs_maxlen=999)
        self.assertEqual(plugin.bot_msgs_maxlen, 50)

    def test_created_at_utc_is_timezone_safe(self):
        # 守静默失效：naive 时间被按宿主机本地时区解释 → 时间衰减整体偏移
        expected = datetime(2026, 7, 17, 12, tzinfo=timezone.utc).timestamp()
        self.assertEqual(
            WakeLitePlugin._parse_created_at("2026-07-17T12:00:00Z"), expected
        )
        # chat_memory 的数据库时间为 UTC naive：必须按 UTC 解释
        self.assertEqual(
            WakeLitePlugin._parse_created_at("2026-07-17 12:00:00"), expected
        )


class FollowUpTests(unittest.IsolatedAsyncioTestCase):
    """??????????????? x ??????"""

    UMO = "platform_demo:GroupMessage:group_demo"

    @staticmethod
    def _plugin(**overrides):
        base = {"enabled": True, "window": 120}
        flat = {}
        for key in list(overrides):
            if key.startswith("follow_up_") and key != "follow_up_block_ids":
                flat[key[len("follow_up_"):]] = overrides.pop(key)
        explicit = overrides.get("follow_up")
        if isinstance(explicit, dict):
            merged = {**base, **explicit}
        else:
            merged = dict(base)
        merged.update(flat)
        if merged.get("window", 0) > 0 and "enabled" not in merged:
            merged["enabled"] = True
        overrides["follow_up"] = merged
        defaults = dict(
            prob=0,
            persona_name_prob=0,
            ask_threshold=1,
            bored_threshold=1,
            interest_threshold=1,
            similar_threshold=1,
            wake_cd=0,
        )
        defaults.update(overrides)
        return make_plugin(**defaults)

    def test_prob_time_curve_and_start(self):
        # ???????????? ? ?????????????
        plugin = self._plugin(
            follow_up_window=100, follow_up_prob=1.0, follow_up_curve="linear"
        )
        plugin._follow_up_start[self.UMO] = 1000.0
        self.assertAlmostEqual(plugin._follow_up_hit_prob(self.UMO, 1, 1000.0), 1.0)
        self.assertAlmostEqual(plugin._follow_up_hit_prob(self.UMO, 1, 1050.0), 0.5)
        self.assertAlmostEqual(plugin._follow_up_hit_prob(self.UMO, 1, 1100.0), 0.0)

        constant = self._plugin(
            follow_up_window=100, follow_up_prob=0.5, follow_up_curve="constant"
        )
        constant._follow_up_start[self.UMO] = 1000.0
        self.assertAlmostEqual(
            constant._follow_up_hit_prob(self.UMO, 1, 1099.0), 0.5
        )

    def test_power_curve_shape(self):
        # 守静默失效：power 曲线写成线性或方向反了（先慢后快变先快后慢）
        plugin = self._plugin(
            follow_up_window=100, follow_up_prob=1.0, follow_up_curve="power"
        )
        plugin._follow_up_start[self.UMO] = 1000.0
        self.assertAlmostEqual(plugin._follow_up_hit_prob(self.UMO, 1, 1000.0), 1.0)
        self.assertAlmostEqual(plugin._follow_up_hit_prob(self.UMO, 1, 1050.0), 0.75)
        self.assertAlmostEqual(plugin._follow_up_hit_prob(self.UMO, 1, 1100.0), 0.0)

    def test_prob_gap_factor(self):
        # ???????????? ? ????????????
        plugin = self._plugin(
            follow_up_window=100,
            follow_up_prob=1.0,
            follow_up_curve="linear",
            follow_up_gap=10,
        )
        plugin._follow_up_start[self.UMO] = 1000.0
        plugin._last_bot_seq[self.UMO] = 5
        self.assertAlmostEqual(plugin._follow_up_hit_prob(self.UMO, 5, 1000.0), 1.0)
        self.assertAlmostEqual(plugin._follow_up_hit_prob(self.UMO, 10, 1000.0), 0.5)
        self.assertAlmostEqual(plugin._follow_up_hit_prob(self.UMO, 15, 1000.0), 0.0)

        plugin._last_bot_seq.pop(self.UMO)  # ????????
        self.assertAlmostEqual(plugin._follow_up_hit_prob(self.UMO, 999, 1000.0), 1.0)

    async def test_window_signals_and_probability_both_participate(self):
        # 守静默失效：窗口内信号被跳过（明确叫它却不回）；或概率兜底不生效
        plugin = self._plugin(
            follow_up_window=120,
            follow_up_prob=0.5,
            keyword_prob=1.0,
            keyword_words=["咖啡"],
        )
        plugin._follow_up_start[self.UMO] = time.time()

        # 1) 窗口内关键词命中 → 直接唤醒（信号参与判定）
        event = Event()
        event.message_str = "今天咖啡豆到了"
        with patch("wakelite.main.random.random", return_value=0.0):
            await plugin.on_message(event)
        self.assertTrue(event.is_at_or_wake_command)

        # 2) 窗口内无信号命中，概率兜底命中 → 唤醒
        plugin._follow_up_start[self.UMO] = time.time()  # 关键词唤醒已刷新起点
        event2 = Event()
        event2.message_str = "随便聊聊"
        with patch("wakelite.main.random.random", return_value=0.0):
            await plugin.on_message(event2)
        self.assertTrue(event2.is_at_or_wake_command)
        self.assertEqual(event2.get_extra("wakelite_wake_source"), "window")

        # 3) 概率未命中 → 不唤醒
        event3 = Event()
        event3.message_str = "随便聊聊"
        with patch("wakelite.main.random.random", return_value=0.9):
            await plugin.on_message(event3)
        self.assertFalse(event3.is_at_or_wake_command)

    async def test_window_expired_falls_back_to_signals(self):
        # 守静默失效：窗口过期后仍按窗口概率；或旧窗口没被清理
        plugin = self._plugin(follow_up_window=60, prob=1.0)
        old = time.time() - 61
        plugin._follow_up_start[self.UMO] = old
        event = Event()

        await plugin.on_message(event)

        self.assertTrue(event.is_at_or_wake_command)  # 走普通概率信号
        # 新语义：任何唤醒都会重新开窗，起点被刷新
        self.assertGreater(plugin._follow_up_start[self.UMO], old)

    async def test_window_other_at(self):
        # ?????????? @?? ??????????????
        plugin = self._plugin(follow_up_window=120, follow_up_prob=1.0)
        event = Event()
        event.messages = [At(qq="10009")]  # @??
        plugin._follow_up_start[self.UMO] = time.time()

        await plugin.on_message(event)

        self.assertFalse(event.is_at_or_wake_command)  # ?????

        allowed = self._plugin(
            follow_up_window=120, follow_up_prob=1.0, follow_up_allow_other_at=True
        )
        allowed._follow_up_start[self.UMO] = time.time()
        event2 = Event()
        event2.messages = [At(qq="10009")]
        with patch("wakelite.main.random.random", return_value=0.0):
            await allowed.on_message(event2)
        self.assertTrue(event2.is_at_or_wake_command)  # ???????

    async def test_blacklist_window_source(self):
        # 守静默失效：窗口兜底命中不受唤醒源黑名单约束
        plugin = self._plugin(
            follow_up_window=120,
            follow_up_prob=1.0,
            blacklist_users=["10003 跟随窗口"],
        )
        plugin._follow_up_start[self.UMO] = time.time()
        event = Event()
        event.sender_id = "10003"

        with patch("wakelite.main.random.random", return_value=0.0):
            await plugin.on_message(event)

        self.assertFalse(event.is_at_or_wake_command)

    async def test_window_ignores_bot_sharding(self):
        # 守静默失效：窗口内仍被多 bot 分流拦住（该接话时无人接）
        bots = ["10002:group_demo"]  # 本 bot 10001 不在池内
        outside = self._plugin(prob=0, group_bots=bots)
        event = Event()
        with patch("wakelite.main.random.random", return_value=0.0):
            await outside.on_message(event)
        self.assertFalse(event.is_at_or_wake_command)  # 窗口外按分流轮空

        inside = self._plugin(
            prob=0,
            group_bots=bots,
            follow_up_window=120,
            follow_up_prob=1.0,
        )
        inside._follow_up_start[self.UMO] = time.time()
        event2 = Event()
        with patch("wakelite.main.random.random", return_value=0.0):
            await inside.on_message(event2)
        self.assertTrue(event2.is_at_or_wake_command)  # 窗口内不受分流影响

    async def test_follow_up_disabled_ignores_window(self):
        # 守静默失效：关闭启用开关后窗口仍生效（配置总开关形同虚设）
        plugin = self._plugin(follow_up={"enabled": False, "window": 120, "prob": 1.0})
        plugin._follow_up_start[self.UMO] = time.time()
        event = Event()
        with patch("wakelite.main.random.random", return_value=0.0):
            await plugin.on_message(event)
        self.assertFalse(event.is_at_or_wake_command)  # 窗口不生效
        self.assertFalse(plugin._follow_up_active(time.time(), self.UMO))

    async def test_window_hit_refreshes_start(self):
        # 守静默失效：窗口兜底命中后没启动/刷新窗口（唤醒后应进入玩手机状态）
        plugin = self._plugin(follow_up_prob=1.0)
        old = time.time() - 60
        plugin._follow_up_start[self.UMO] = old
        event = Event()

        with patch("wakelite.main.random.random", return_value=0.0):
            await plugin.on_message(event)

        self.assertTrue(event.is_at_or_wake_command)
        self.assertGreater(plugin._follow_up_start[self.UMO], old)

    async def test_window_opens_on_trigger(self):
        # ???????????????????????????
        plugin = self._plugin(
            follow_up_window=120, keyword_prob=1.0, keyword_words=["??"]
        )
        event = Event()
        event.message_str = "???????"

        with patch("wakelite.main.random.random", return_value=0.0):
            await plugin.on_message(event)

        self.assertTrue(event.is_at_or_wake_command)
        self.assertIn(self.UMO, plugin._follow_up_start)  # keyword ??? triggers ?

    async def test_window_at_self_refreshes_start(self):
        # ????????? @ ???????
        plugin = self._plugin(follow_up_window=120)
        event = Event()
        event.is_at_or_wake_command = True
        event.messages = [At(qq="10001")]
        old = time.time() - 60
        plugin._follow_up_start[self.UMO] = old

        await plugin.on_message(event)

        self.assertGreater(plugin._follow_up_start[self.UMO], old)

    async def test_window_wake_can_be_rejected(self):
        # 守静默失效：勾了窗口兜底却不注入拒绝工具（或未勾选也注入）
        plugin = self._plugin(
            follow_up_prob=1.0,
            enable_reject_tool=True,
            reject_tool_scopes=["window"],
        )
        plugin._follow_up_start[self.UMO] = time.time()
        event = Event()
        with patch("wakelite.main.random.random", return_value=0.0):
            await plugin.on_message(event)
        self.assertTrue(event.is_at_or_wake_command)

        req = ProviderRequest()
        await plugin.inject_wake_hint(event, req)
        self.assertIsNotNone(req.func_tool)  # 窗口兜底可拒绝

        plain = self._plugin(
            follow_up_prob=1.0,
            enable_reject_tool=True,
            reject_tool_scopes=["keyword"],
        )
        plain._follow_up_start[self.UMO] = time.time()
        event2 = Event()
        with patch("wakelite.main.random.random", return_value=0.0):
            await plain.on_message(event2)
        req2 = ProviderRequest()
        await plain.inject_wake_hint(event2, req2)
        self.assertIsNone(req2.func_tool)  # 未勾选 window 时不注入

    async def test_anchor_recorded_on_result(self):
        # ???????????????? ? ????????
        plugin = self._plugin(follow_up_window=120)
        event = Event()
        event._result = object()
        plugin._session_msg_seq[self.UMO] = 7

        await plugin.block_declined_reply(event)

        self.assertEqual(plugin._last_bot_seq.get(self.UMO), 7)


    async def test_command_does_not_open_window(self):
        # 守静默失效：指令唤醒也开窗（管理命令让 Bot 进入玩手机状态）
        plugin = self._plugin(follow_up_window=120)
        event = Event()
        event.is_at_or_wake_command = True
        event.message_obj.message_str = "/help"

        await plugin.on_message(event)

        self.assertNotIn(self.UMO, plugin._follow_up_start)

    async def test_command_opens_when_selected(self):
        # 守静默失效：勾了 command 却不开窗
        plugin = self._plugin(follow_up_window=120, follow_up_command=True)
        event = Event()
        event.is_at_or_wake_command = True
        event.message_obj.message_str = "/help"

        await plugin.on_message(event)

        self.assertIn(self.UMO, plugin._follow_up_start)

    async def test_blacklist_keyword_source(self):
        # 守静默失效：按唤醒源屏蔽失效 → 被屏蔽的关键词仍唤醒
        plugin = self._plugin(
            follow_up_window=120,
            keyword_prob=1.0,
            keyword_words=["咖啡"],
            blacklist_users=["10003 keyword"],
        )
        event = Event()
        event.sender_id = "10003"
        event.message_str = "今天咖啡豆到了"

        with patch("wakelite.main.random.random", return_value=0.0):
            await plugin.on_message(event)
        self.assertFalse(event.is_at_or_wake_command)  # keyword 被屏蔽

        # 同一 ID 的 @ 不受黑名单影响（核心唤醒照常开窗）
        at_event = Event()
        at_event.sender_id = "10003"
        at_event.is_at_or_wake_command = True
        at_event.messages = [At(qq="10001")]
        await plugin.on_message(at_event)
        self.assertIn(self.UMO, plugin._follow_up_start)

    async def test_blacklist_empty_sources_means_all(self):
        # 守静默失效：只填 ID（唤醒源留空）没有全部屏蔽
        plugin = self._plugin(
            follow_up_window=120,
            keyword_prob=1.0,
            keyword_words=["咖啡"],
            blacklist_users=["10003"],
        )
        event = Event()
        event.sender_id = "10003"
        event.message_str = "今天咖啡豆到了"

        with patch("wakelite.main.random.random", return_value=0.0):
            await plugin.on_message(event)
        self.assertFalse(event.is_at_or_wake_command)  # 关键词被屏蔽

        plugin._follow_up_start[self.UMO] = time.time()
        event2 = Event()
        event2.sender_id = "10003"
        with patch("wakelite.main.random.random", return_value=0.0):
            await plugin.on_message(event2)
        self.assertFalse(event2.is_at_or_wake_command)  # 窗口兜底同样被屏蔽


class BlockMigrationTests(unittest.TestCase):
    """唤醒源黑名单迁移：旧「群号:QQ」列表 / 1.3.7 跟随黑名单都要无损转换。"""

    def test_legacy_list_normalizes_to_template(self):
        # 守静默失效：旧版「群号:QQ」列表迁移后丢失或唤醒源为空
        entries = plugin_main._normalize_blacklist_entries(
            ["group_demo:10003", "10004", ""]
        )
        self.assertEqual([e["id"] for e in entries], ["group_demo:10003", "10004"])
        self.assertEqual(
            entries[0]["sources"], list(plugin_main._WAKE_SOURCE_OPTIONS)
        )

    def test_migrate_writeback_from_legacy_list(self):
        # 守静默失效：旧列表没写回新键（重启后又变回旧格式）
        config = {"blacklist_users": ["group_demo:10003"]}
        entries = plugin_main._migrate_blacklist_config(config)
        self.assertEqual(entries[0]["id"], "group_demo:10003")
        self.assertEqual(config["blacklist_users"], ["group_demo:10003"])

    def test_migrate_flat_follow_up_config(self):
        # 守静默失效：旧平铺配置没迁移到分组（窗口配置丢失/未启用）
        config = {
            "follow_up": {"enabled": False, "window": 0.0},
            "follow_up_window": 180,
            "follow_up_prob": 0.4,
            "follow_up_curve": "smooth",
        }
        plugin_main._migrate_follow_up_config(config)
        group = config["follow_up"]
        self.assertEqual(group["window"], 180)
        self.assertEqual(group["prob"], 0.4)
        self.assertEqual(group["curve"], "smooth")
        self.assertTrue(group["enabled"])  # window>0 自动启用
        self.assertEqual(config["follow_up_window"], 0.0)  # 平铺键清空

        # 分组里已显式设置时不被平铺键覆盖
        config2 = {
            "follow_up": {"window": 60, "enabled": False},
            "follow_up_window": 999,
        }
        plugin_main._migrate_follow_up_config(config2)
        self.assertEqual(config2["follow_up"]["window"], 60)
        self.assertFalse(config2["follow_up"]["enabled"])

    def test_migrate_from_follow_up_positions(self):
        # 守静默失效：1.3.7 跟随黑名单的条目丢失
        config = {
            "follow_up_block_ids": [{"id": "10003", "scopes": ["keyword", "at"]}],
            "follow_up": {"block_ids": [{"id": "10004", "scopes": ["window"]}]},
        }
        entries = plugin_main._migrate_blacklist_config(config)
        by_id = {e["id"]: e["sources"] for e in entries}
        self.assertEqual(by_id["10003"], ["keyword"])  # at 不归黑名单管
        self.assertEqual(by_id["10004"], ["window"])
        self.assertEqual(config["follow_up_block_ids"], [])

    def test_migrate_merges_both_sources(self):
        # 守静默失效：只迁移一处，另一处黑名单丢失
        config = {
            "blacklist_users": ["group_demo:10003"],
            "follow_up_block_ids": [{"id": "10004", "scopes": ["keyword"]}],
        }
        entries = plugin_main._migrate_blacklist_config(config)
        self.assertEqual([e["id"] for e in entries], ["group_demo:10003", "10004"])
        self.assertEqual(len(config["blacklist_users"]), 2)

    def test_template_writeback_to_lines(self):
        # 守静默失效：1.4.0 template 条目没转成 list 行（旧格式残留）
        config = {
            "blacklist_users": [{"id": "10003", "sources": ["keyword"]}]
        }
        entries = plugin_main._migrate_blacklist_config(config)
        self.assertEqual(entries[0]["sources"], ["keyword"])
        self.assertEqual(config["blacklist_users"], ["10003 keyword"])


class SimilarityTimeDecayTests(unittest.TestCase):
    MSG = "咖啡怎么手冲"
    CAND = ["手冲咖啡需要控制水温与研磨度"]

    def test_decay_lowers_older_candidate_score(self):
        s = Similarity(bot_template_threshold=0)
        fresh = s.similarity("k", self.MSG, self.CAND, ages=[0.0])
        old = s.similarity("k", self.MSG, self.CAND, ages=[600.0])
        self.assertGreater(fresh, old)

    def test_unknown_age_not_decayed(self):
        s = Similarity(bot_template_threshold=0)
        a = s.similarity("k", self.MSG, self.CAND, ages=[None])
        b = s.similarity("k", self.MSG, self.CAND, ages=[0.0])
        self.assertAlmostEqual(a, b)

    def test_token_cache_is_bounded(self):
        s = Similarity(bot_template_threshold=0, token_cache_size=8)
        for i in range(30):
            s.similarity("k", f"用户消息{i}", [f"候选回复内容{i}"])
        self.assertLessEqual(len(s._token_cache), 8)


class CandidateWindowTests(unittest.IsolatedAsyncioTestCase):
    async def test_reread_uses_current_user_scope(self):
        # 守静默失效：group 模式下复读拿全群回复比对 → 用户说别人的话也被拦
        plugin = make_plugin(history_scope="group")
        plugin.context.query_recorder.records_by_user = {
            None: [
                {"role": "assistant", "content": "对别人的回复内容",
                 "llm_status": "llm_success"},
                {"role": "assistant", "content": "对当前用户的回复",
                 "llm_status": "llm_success"},
            ],
            "10002": [
                {"role": "assistant", "content": "对当前用户的回复",
                 "llm_status": "llm_success"},
            ],
        }

        reread, sims, ages = await plugin._get_bot_msgs("umo_demo", "10002")

        self.assertEqual(reread, ["对当前用户的回复"])  # 复读只看当前用户触发
        self.assertEqual(len(sims), 2)  # 相关性仍按 group 范围

    async def test_candidates_capped_by_history_limit(self):
        now = time.time()
        records = [
            {
                "role": "assistant",
                "content": f"回复内容 {i}",
                "llm_status": "llm_success",
                "created_at_utc": now - (9 - i) * 60,
            }
            for i in range(10)
        ]
        plugin = make_plugin(bot_msgs_maxlen=3)
        plugin.context.query_recorder.records = records

        reread, sims, ages = await plugin._get_bot_msgs("umo_demo", "10002")

        self.assertEqual(len(reread), 3)
        self.assertEqual(len(sims), 3)
        self.assertEqual(len(ages), 3)
        self.assertTrue(all(a is not None for a in ages))
        self.assertLess(ages[-1], ages[0])  # 旧→新，年龄递减

    async def test_no_ttl_hard_drop_old_records_kept_within_limit(self):
        now = time.time()
        records = [
            {
                "role": "assistant",
                "content": f"很久之前的回复 {i}",
                "llm_status": "llm_success",
                "created_at_utc": now - 3600 - i * 60,
            }
            for i in range(10)
        ]
        plugin = make_plugin(bot_msgs_maxlen=10)
        plugin.context.query_recorder.records = records

        reread, sims, ages = await plugin._get_bot_msgs("umo_demo", "10002")

        # 取消过期时间后，超过旧 TTL（10 分钟）的记录不再被硬丢弃，
        # 只受条数上限约束，旧记录靠时间衰减压低权重。
        self.assertEqual(len(sims), 10)
        # 用 >=：年龄按「测试取时」与「实现取时」两次 clock 之差算，可能相等
        self.assertTrue(all(a is not None and a >= 3600 for a in ages))


class WakeHintTests(unittest.IsolatedAsyncioTestCase):
    async def test_hint_only_injected_on_wake_and_not_saved_to_history(self):
        # 守两种静默失效：未唤醒轮被注入提示（污染模型输入）、
        # 提示未标记临时而写入对话历史（污染历史）
        plugin = make_plugin()
        sources = ("persona_name", "keyword", "probability", "ask", "bored",
                   "interest", "similarity")
        for source in sources:
            event = Event()
            plugin._wake(event, "10002", 123.0, source, source=source)
            req = ProviderRequest()

            await plugin.inject_wake_hint(event, req)

            self.assertEqual(len(req.extra_user_content_parts), 1, source)
            self.assertTrue(req.extra_user_content_parts[0]._no_save, source)

        req = ProviderRequest()
        await plugin.inject_wake_hint(Event(), req)
        self.assertEqual(req.extra_user_content_parts, [])

    async def test_hint_keeps_existing_extra_parts(self):
        plugin = make_plugin()
        event = Event()
        plugin._wake(event, "10002", 123.0, "兴趣唤醒", source="interest")
        existing = object()
        req = ProviderRequest()
        req.extra_user_content_parts.append(existing)

        await plugin.inject_wake_hint(event, req)

        self.assertIs(req.extra_user_content_parts[0], existing)
        self.assertEqual(len(req.extra_user_content_parts), 2)


class OnMessageTests(unittest.IsolatedAsyncioTestCase):
    """覆盖插件类入口 on_message 的事件分支（AGENTS.md：不能只测被调函数）。"""

    @staticmethod
    def _quiet_plugin(**overrides):
        defaults = dict(
            prob=0,
            persona_name_prob=0,
            ask_threshold=1,
            bored_threshold=1,
            interest_threshold=1,
            similar_threshold=1,
            wake_cd=0,
        )
        defaults.update(overrides)
        return make_plugin(**defaults)

    async def test_wake_cd_blocks_second_message(self):
        plugin = self._quiet_plugin(wake_cd=5.0)
        event = Event()
        plugin._last_wake[(event.unified_msg_origin, event.sender_id)] = time.time()

        await plugin.on_message(event)

        self.assertFalse(event.is_at_or_wake_command)

    async def test_blacklist_user_skips_wake(self):
        # 概率拉满：黑名单用户若不跳过，这条消息必然被唤醒
        plugin = self._quiet_plugin(prob=1.0, blacklist_users=["group_demo:10002"])
        event = Event()  # sender=10002, group_demo

        await plugin.on_message(event)

        self.assertFalse(event.is_at_or_wake_command)

    def test_blacklist_entries_parsed(self):
        # 守静默失效：list 行解析失败（合并 ID / 中文源 / 留空 = 全部）
        plugin = make_plugin(
            blacklist_users=[
                "10003 keyword",
                "group_demo:10004 跟随窗口",
                "10005,10006 关键词 概率",
                "10007",
            ]
        )
        self.assertEqual(plugin._wake_blacklist["10003"], frozenset({"keyword"}))
        self.assertEqual(
            plugin._wake_blacklist["group_demo:10004"], frozenset({"window"})
        )
        self.assertEqual(
            plugin._wake_blacklist["10005"], frozenset({"keyword", "probability"})
        )
        self.assertEqual(
            plugin._wake_blacklist["10006"], frozenset({"keyword", "probability"})
        )
        self.assertEqual(
            plugin._wake_blacklist["10007"],
            frozenset(m for m in plugin_main._WAKE_SOURCE_OPTIONS),
        )

    async def test_mention_other_skips_wake(self):
        # 概率拉满：若 @他人 未被跳过，这条消息必然被唤醒
        plugin = self._quiet_plugin(prob=1.0)
        event = Event()
        event.messages = [At(qq="10003"), Plain("你看看这个")]

        await plugin.on_message(event)

        self.assertFalse(event.is_at_or_wake_command)

    async def test_mention_self_or_all_still_wakes(self):
        # 守静默漏唤醒：@自己、@全体、@自己+@他人 混合都不应被当成"@他人"跳过
        plugin = self._quiet_plugin(prob=1.0)
        for segs in ([At(qq="10001")], [At(qq="all")],
                     [At(qq="10001"), At(qq="10003")]):
            event = Event()
            event.messages = segs + [Plain("在吗")]

            await plugin.on_message(event)

            self.assertTrue(event.is_at_or_wake_command, segs)

    async def test_reply_other_skips_wake(self):
        # 概率拉满：若"引用他人"未被跳过，这条消息必然被唤醒
        plugin = self._quiet_plugin(prob=1.0)
        event = Event()
        event.messages = [Reply(sender_id="10003"), Plain("哈哈哈哈")]

        await plugin.on_message(event)

        self.assertFalse(event.is_at_or_wake_command)

    async def test_reply_self_or_unknown_sender_still_wakes(self):
        # 守静默漏唤醒：引用 bot 自己、发送者缺失（平台未提供）、
        # @自己+引用他人 混合，都必须照常判定
        plugin = self._quiet_plugin(prob=1.0)
        for segs in ([Reply(sender_id="10001")], [Reply(sender_id="0")],
                     [At(qq="10001"), Reply(sender_id="10003")]):
            event = Event()
            event.messages = segs + [Plain("在吗")]

            await plugin.on_message(event)

            self.assertTrue(event.is_at_or_wake_command, segs)

    async def test_persona_name_wake_sets_flag(self):
        plugin = self._quiet_plugin(persona_name_prob=1.0)
        event = Event()
        event.message_str = "会话人格你好"

        await plugin.on_message(event)

        self.assertTrue(event.is_at_or_wake_command)
        self.assertEqual(event.get_extra("wakelite_wake_source"), "persona_name")

    async def test_keyword_wake_sets_flag(self):
        plugin = self._quiet_plugin(keyword_prob=1.0, keyword_words=["咖啡", "摄影"])
        event = Event()
        event.message_str = "今天咖啡豆到了"

        await plugin.on_message(event)

        self.assertTrue(event.is_at_or_wake_command)
        self.assertEqual(event.get_extra("wakelite_wake_source"), "keyword")

    async def test_keyword_empty_list_does_not_wake(self):
        # 关键词留空时，即使概率拉满也不得唤醒（否则等于全群概率插话）
        plugin = self._quiet_plugin(keyword_prob=1.0, keyword_words=[])
        event = Event()
        event.message_str = "今天咖啡豆到了"

        await plugin.on_message(event)

        self.assertFalse(event.is_at_or_wake_command)


class RejectToolTests(unittest.IsolatedAsyncioTestCase):

    async def test_tool_injected_when_woken_and_enabled(self):
        plugin = make_plugin(enable_reject_tool=True)
        event = Event()
        plugin._wake(event, "10002", 123.0, "概率唤醒", source="probability")
        req = ProviderRequest()

        await plugin.inject_wake_hint(event, req)

        self.assertIsNotNone(req.func_tool)
        self.assertIn("wakelite_decline_reply", req.func_tool.names())

    async def test_injection_preserves_existing_tools(self):
        plugin = make_plugin(enable_reject_tool=True)
        event = Event()
        plugin._wake(event, "10002", 123.0, "概率唤醒", source="probability")
        req = ProviderRequest()
        req.func_tool = ToolSet()
        req.func_tool.add_tool(
            FunctionTool(name="other_tool", parameters={"type": "object", "properties": {}})
        )

        await plugin.inject_wake_hint(event, req)

        names = req.func_tool.names()
        self.assertIn("other_tool", names)
        self.assertIn("wakelite_decline_reply", names)

    async def test_decline_handler_marks_event(self):
        # 守静默失效：工具参数退化成空 schema（部分中转站报错）、
        # handler 返回字符串让 tool loop 继续（模型反复调用本工具）、
        # 调用前已产生的文本没被清掉（executor 会直接发送出去）
        plugin = make_plugin()
        props = plugin._reject_tool.parameters.get("properties") or {}
        self.assertIn("reason", props)
        event = Event()
        event.set_result("模型调用工具前先说了话")

        result = await plugin._decline_reply_handler(event, reason="用户在问别人")

        self.assertIsNone(result)
        self.assertTrue(event.get_extra("wakelite_declined", False))
        self.assertIsNone(event.get_result())

    async def test_block_clears_result_when_declined(self):
        plugin = make_plugin()
        event = Event()
        event.set_result("dummy")
        event.set_extra("wakelite_declined", True)

        await plugin.block_declined_reply(event)

        self.assertIsNone(event.get_result())
        self.assertTrue(event.is_stopped())

    async def test_block_untouched_when_not_declined(self):
        plugin = make_plugin()
        event = Event()
        event.set_result("dummy")

        await plugin.block_declined_reply(event)

        self.assertEqual(event.get_result(), "dummy")
        self.assertFalse(event.is_stopped())


class AdapterLogicTests(unittest.IsolatedAsyncioTestCase):
    async def test_persona_name_uses_resolved_conversation_persona(self):
        plugin = make_plugin()
        name = await plugin._get_persona_name(Event.unified_msg_origin, Event())
        self.assertEqual(name, "会话人格")
        kwargs = plugin.persona_mgr.resolve_kwargs
        self.assertEqual(kwargs["conversation_persona_id"], "persona_conversation")
        self.assertEqual(kwargs["platform_name"], "aiocqhttp")

    async def test_persona_name_queried_every_call_without_plugin_cache(self):
        plugin = make_plugin()
        for _ in range(3):
            name = await plugin._get_persona_name(Event.unified_msg_origin, Event())
            self.assertEqual(name, "会话人格")
        self.assertEqual(plugin.persona_mgr.resolve_count, 3)

    async def test_group_history_scope_omits_user_filter(self):
        plugin = make_plugin(history_scope="group")
        await plugin._query_chat_memory("umo_demo", "cid_demo", "10002")
        args, kwargs = plugin.context.query_recorder.args
        self.assertIsNone(args[2])
        self.assertEqual(kwargs["role_filter"], "assistant")

    async def test_user_history_scope_keeps_user_filter(self):
        plugin = make_plugin(history_scope="user")
        await plugin._query_chat_memory("umo_demo", "cid_demo", "10002")
        args, _ = plugin.context.query_recorder.args
        self.assertEqual(args[2], "10002")

    def test_compute_my_turn_bot_sender_excluded_from_pool(self):
        plugin = make_plugin(group_bots=["10001", "10002"])
        event = Event()  # 本 bot 10001，消息来自池内 bot 10002
        # 池=[10001,10002]，排除发送者后只剩自己，hash % 1 == 0 → 轮到自己
        self.assertTrue(plugin._compute_my_turn(event, "10002", "10001"))

    def test_compute_my_turn_single_bot_sender_leaves_empty_pool(self):
        plugin = make_plugin(group_bots=["10001"])
        event = Event()
        self.assertFalse(plugin._compute_my_turn(event, "10001", "10001"))

    def test_group_entry_scoped_to_its_group(self):
        plugin = make_plugin(group_bots=["10001:group_demo"])
        event = Event()  # gid=group_demo，用户消息
        self.assertTrue(plugin._compute_my_turn(event, "10002", "10001"))
        other = Event()
        other.group_id = "other_group"
        self.assertFalse(plugin._compute_my_turn(other, "10002", "10001"))

    def test_global_entry_applies_to_any_group(self):
        plugin = make_plugin(group_bots=["10001"])
        event = Event()
        event.group_id = "other_group"
        self.assertTrue(plugin._compute_my_turn(event, "10002", "10001"))


if __name__ == "__main__":
    unittest.main()
