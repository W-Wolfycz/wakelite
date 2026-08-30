import dataclasses
import json
import sys
import time
import types
import unittest
from datetime import datetime, timezone

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
    sys.modules.update(modules)


_install_astrbot_stubs()

from astrbot.api import FunctionTool, ToolSet
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
        self.records = []

    async def query_history(self, *args, **kwargs):
        self.args = (args, kwargs)
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
        return []

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
    def test_question_particle_is_not_removed(self):
        self.assertIn("吗", sentiment.seg("你好吗"))
        self.assertGreater(sentiment.ask("你好吗"), 0)

    def test_negation_reduces_bored_score(self):
        self.assertLess(sentiment.bored("我不无聊"), sentiment.bored("我好无聊"))

    def test_similarity_prefers_related_message(self):
        similarity = Similarity(bot_template_threshold=0)
        related = similarity.similarity(
            "group_demo",
            "原神角色怎么配队",
            ["原神角色配队需要考虑元素反应"],
        )
        unrelated = similarity.similarity(
            "group_demo",
            "原神角色怎么配队",
            ["今天上海天气晴朗适合散步"],
        )
        self.assertGreater(related, unrelated)

    def test_invalid_interest_items_are_ignored(self):
        plugin = make_plugin(interest_words=["原神 风", None, 123])
        self.assertEqual(plugin.interest_words, [["原神", "风"]])

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
        actual = WakeLitePlugin._parse_created_at("2026-07-17T12:00:00Z")
        expected = datetime(2026, 7, 17, 12, tzinfo=timezone.utc).timestamp()
        self.assertEqual(actual, expected)

    def test_wake_cd_is_scoped_by_umo(self):
        plugin = make_plugin()
        event = Event()
        plugin._wake(event, "10002", 123.0, "test")
        self.assertEqual(
            plugin._last_wake[(event.unified_msg_origin, "10002")],
            123.0,
        )


class SimilarityTimeDecayTests(unittest.TestCase):
    MSG = "原神角色怎么配队"
    CAND = ["原神角色配队需要考虑元素反应"]

    def test_decay_lowers_older_candidate_score(self):
        s = Similarity(bot_template_threshold=0)
        fresh = s.similarity("k", self.MSG, self.CAND, ages=[0.0])
        old = s.similarity("k", self.MSG, self.CAND, ages=[600.0])
        self.assertGreater(fresh, old)

    def test_no_ages_matches_undecayed(self):
        s = Similarity(bot_template_threshold=0)
        with_ages = s.similarity("k", self.MSG, self.CAND, ages=[0.0])
        no_ages = s.similarity("k", self.MSG, self.CAND)
        self.assertAlmostEqual(with_ages, no_ages)

    def test_unknown_age_not_decayed(self):
        s = Similarity(bot_template_threshold=0)
        a = s.similarity("k", self.MSG, self.CAND, ages=[None])
        b = s.similarity("k", self.MSG, self.CAND, ages=[0.0])
        self.assertAlmostEqual(a, b)

    def test_token_cache_reuses_tokens(self):
        s = Similarity(bot_template_threshold=0)
        calls = {"n": 0}
        real_lcut = jieba.lcut

        def counting(text):
            calls["n"] += 1
            return real_lcut(text)

        jieba.lcut = counting
        try:
            s.similarity("k", self.MSG, self.CAND)
            first = calls["n"]
            s.similarity("k", self.MSG, self.CAND)
            self.assertEqual(calls["n"], first)  # 第二次全部命中缓存
        finally:
            jieba.lcut = real_lcut


class CandidateWindowTests(unittest.IsolatedAsyncioTestCase):
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
        self.assertTrue(all(a is not None and a > 3600 for a in ages))

    async def test_disabled_history_skips_query(self):
        plugin = make_plugin(bot_msgs_maxlen=0)

        reread, sims, ages = await plugin._get_bot_msgs("umo_demo", "10002")

        self.assertEqual((reread, sims, ages), ([], [], []))
        self.assertIsNone(plugin.context.query_recorder.args)  # 未发生查询

    async def test_fallback_source_ages_are_none(self):
        plugin = make_plugin(use_chat_memory=False, bot_msgs_maxlen=5)
        Conversation.history = json.dumps(
            [
                {"role": "assistant", "content": "兜底回复内容一二"},
                {"role": "user", "content": "用户消息"},
            ]
        )
        try:
            reread, sims, ages = await plugin._get_bot_msgs("umo_demo", "10002")
        finally:
            Conversation.history = "[]"

        self.assertEqual(len(sims), 1)
        self.assertEqual(ages, [None])


class WakeHintTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_conversation_wake_adds_temporary_hint(self):
        plugin = make_plugin()
        event = Event()
        plugin._wake(event, "10002", 123.0, "概率唤醒", source="probability")
        req = ProviderRequest()

        await plugin.inject_wake_hint(event, req)

        self.assertEqual(len(req.extra_user_content_parts), 1)
        part = req.extra_user_content_parts[0]
        self.assertTrue(part._no_save)
        self.assertIn("概率信号被唤醒", part.text)
        self.assertIn("不是历史对话的自然续接", part.text)
        self.assertIn("其他用户带有鲜明人物设定", part.text)
        self.assertIn("不要模仿、接管或延续", part.text)

    async def test_all_wake_sources_add_hint(self):
        plugin = make_plugin()
        for source in ("persona_name", "ask", "bored", "interest", "similarity"):
            event = Event()
            plugin._wake(event, "10002", 123.0, source, source=source)
            req = ProviderRequest()

            await plugin.inject_wake_hint(event, req)

            self.assertEqual(len(req.extra_user_content_parts), 1, source)

    async def test_non_wake_event_does_not_add_hint(self):
        plugin = make_plugin()
        event = Event()
        req = ProviderRequest()

        await plugin.inject_wake_hint(event, req)

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

    async def test_reject_guide_added_to_hint_when_tool_enabled(self):
        plugin = make_plugin(enable_reject_tool=True)
        event = Event()
        plugin._wake(event, "10002", 123.0, "概率唤醒", source="probability")
        req = ProviderRequest()

        await plugin.inject_wake_hint(event, req)

        self.assertIn("wakelite_decline_reply", req.extra_user_content_parts[0].text)

    async def test_no_reject_guide_when_tool_disabled(self):
        plugin = make_plugin(enable_reject_tool=False)
        event = Event()
        plugin._wake(event, "10002", 123.0, "概率唤醒", source="probability")
        req = ProviderRequest()

        await plugin.inject_wake_hint(event, req)

        self.assertNotIn("wakelite_decline_reply", req.extra_user_content_parts[0].text)

    async def test_no_reject_guide_for_strong_signal_by_default(self):
        plugin = make_plugin(enable_reject_tool=True)
        event = Event()
        plugin._wake(event, "10002", 123.0, "答疑唤醒", source="ask")
        req = ProviderRequest()

        await plugin.inject_wake_hint(event, req)

        self.assertNotIn("wakelite_decline_reply", req.extra_user_content_parts[0].text)


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

    async def test_non_whitelist_group_skipped(self):
        plugin = self._quiet_plugin()
        event = Event()
        event.group_id = "other_group"

        await plugin.on_message(event)

        self.assertFalse(event.is_at_or_wake_command)

    async def test_bot_self_message_skipped(self):
        plugin = self._quiet_plugin()
        event = Event()
        event.sender_id = event.self_id

        await plugin.on_message(event)

        self.assertFalse(event.is_at_or_wake_command)

    async def test_wake_cd_blocks_second_message(self):
        plugin = self._quiet_plugin(wake_cd=5.0)
        event = Event()
        plugin._last_wake[(event.unified_msg_origin, event.sender_id)] = time.time()

        await plugin.on_message(event)

        self.assertFalse(event.is_at_or_wake_command)

    async def test_probability_wake_sets_flag(self):
        plugin = self._quiet_plugin(prob=1.0)
        event = Event()

        await plugin.on_message(event)

        self.assertTrue(event.is_at_or_wake_command)
        self.assertEqual(event.get_extra("wakelite_wake_source"), "probability")

    async def test_persona_name_wake_sets_flag(self):
        plugin = self._quiet_plugin(persona_name_prob=1.0)
        event = Event()
        event.message_str = "会话人格你好"

        await plugin.on_message(event)

        self.assertTrue(event.is_at_or_wake_command)
        self.assertEqual(event.get_extra("wakelite_wake_source"), "persona_name")


class RejectToolTests(unittest.IsolatedAsyncioTestCase):
    def test_reject_tool_disabled_by_default(self):
        self.assertFalse(make_plugin().enable_reject_tool)

    def test_default_scopes_are_weak_signals_only(self):
        self.assertEqual(
            make_plugin().reject_tool_scopes,
            {"probability", "bored", "interest", "similarity"},
        )

    async def test_tool_injected_when_woken_and_enabled(self):
        plugin = make_plugin(enable_reject_tool=True)
        event = Event()
        plugin._wake(event, "10002", 123.0, "概率唤醒", source="probability")
        req = ProviderRequest()

        await plugin.inject_wake_hint(event, req)

        self.assertIsNotNone(req.func_tool)
        self.assertIn("wakelite_decline_reply", req.func_tool.names())

    async def test_tool_not_injected_without_wake(self):
        plugin = make_plugin(enable_reject_tool=True)
        req = ProviderRequest()

        await plugin.inject_wake_hint(Event(), req)

        self.assertIsNone(req.func_tool)

    async def test_tool_not_injected_when_disabled(self):
        plugin = make_plugin(enable_reject_tool=False)
        event = Event()
        plugin._wake(event, "10002", 123.0, "概率唤醒", source="probability")
        req = ProviderRequest()

        await plugin.inject_wake_hint(event, req)

        self.assertIsNone(req.func_tool)

    async def test_strong_signal_not_injected_by_default_scopes(self):
        plugin = make_plugin(enable_reject_tool=True)
        event = Event()
        plugin._wake(event, "10002", 123.0, "答疑唤醒", source="ask")
        req = ProviderRequest()

        await plugin.inject_wake_hint(event, req)

        self.assertIsNone(req.func_tool)

    async def test_strong_signal_injected_when_explicitly_scoped(self):
        plugin = make_plugin(
            enable_reject_tool=True, reject_tool_scopes=["probability", "ask"]
        )
        event = Event()
        plugin._wake(event, "10002", 123.0, "答疑唤醒", source="ask")
        req = ProviderRequest()

        await plugin.inject_wake_hint(event, req)

        self.assertIsNotNone(req.func_tool)
        self.assertIn("wakelite_decline_reply", req.func_tool.names())

    def test_invalid_scope_values_ignored(self):
        plugin = make_plugin(reject_tool_scopes=["ask", "bogus", 123])
        self.assertEqual(plugin.reject_tool_scopes, {"ask"})

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

    async def test_non_toolset_func_tool_skips_injection_safely(self):
        plugin = make_plugin(enable_reject_tool=True)
        event = Event()
        plugin._wake(event, "10002", 123.0, "概率唤醒", source="probability")
        req = ProviderRequest()
        req.func_tool = ["not", "a", "toolset"]  # 异常类型防护

        await plugin.inject_wake_hint(event, req)  # 不应抛异常

        self.assertEqual(req.func_tool, ["not", "a", "toolset"])

    async def test_unwritable_parts_skips_hint_safely(self):
        plugin = make_plugin()
        event = Event()
        plugin._wake(event, "10002", 123.0, "概率唤醒", source="probability")
        req = ProviderRequest()
        req.extra_user_content_parts = tuple()  # 无 append 的容器

        await plugin.inject_wake_hint(event, req)  # 不应抛异常

        self.assertEqual(req.extra_user_content_parts, tuple())

    async def test_decline_handler_marks_event(self):
        plugin = make_plugin()
        event = Event()

        result = await plugin._decline_reply_handler(event)

        self.assertTrue(event.get_extra("wakelite_declined", False))
        self.assertIsInstance(result, str)

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

    async def test_no_persona_cache_config_remaining(self):
        plugin = make_plugin()
        self.assertFalse(hasattr(plugin, "persona_name_cache_ttl"))
        self.assertFalse(hasattr(plugin, "_persona_name_cache"))

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

    def test_group_bots_parse_forms(self):
        plugin = make_plugin(
            group_bots=["10001", "10002:", "10003:123456789"]
        )
        self.assertIn(("10001", ""), plugin.group_bots)
        self.assertIn(("10002", ""), plugin.group_bots)
        self.assertIn(("10003", "123456789"), plugin.group_bots)

    def test_group_bots_ignore_invalid_entries(self):
        plugin = make_plugin(group_bots=[":group_demo", "   ", 123])
        self.assertEqual(plugin.group_bots, [])

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


class LogConfigTests(unittest.TestCase):
    def test_log_with_bot_id_reads_top_level_and_defaults_true(self):
        self.assertTrue(make_plugin(log_with_bot_id=True).log_with_bot_id)
        self.assertFalse(make_plugin(log_with_bot_id=False).log_with_bot_id)
        self.assertTrue(make_plugin().log_with_bot_id)  # 缺省默认 true

    def test_no_legacy_migration_code_remaining(self):
        plugin = make_plugin()
        self.assertFalse(hasattr(plugin, "_migrate_log_config"))
        self.assertFalse(hasattr(plugin, "_resolve_log_with_bot_id"))
        self.assertFalse(hasattr(plugin, "initialize"))


if __name__ == "__main__":
    unittest.main()
