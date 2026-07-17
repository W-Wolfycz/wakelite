import sys
import types
import unittest
from datetime import datetime, timezone


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
        "astrbot.api.star": types.ModuleType("astrbot.api.star"),
        "astrbot.core": types.ModuleType("astrbot.core"),
        "astrbot.core.message": types.ModuleType("astrbot.core.message"),
        "astrbot.core.message.components": types.ModuleType(
            "astrbot.core.message.components"
        ),
    }
    modules["astrbot.api"].logger = Logger()
    modules["astrbot.api"].AstrBotConfig = dict
    modules["astrbot.api.event"].filter = Filter()
    modules["astrbot.api.event"].AstrMessageEvent = object
    modules["astrbot.api.star"].Context = object
    modules["astrbot.api.star"].Star = Star
    modules["astrbot.core.message.components"].Plain = Plain
    sys.modules.update(modules)


_install_astrbot_stubs()

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

    async def resolve_selected_persona(self, **kwargs):
        self.resolve_kwargs = kwargs
        return "persona_conversation", {"name": "会话人格"}, None, False

    def get_persona_v3_by_id(self, persona_id):
        return {"name": persona_id}

    async def get_default_persona_v3(self, umo):
        return {"name": "默认人格"}


class QueryRecorder:
    def __init__(self):
        self.args = None

    async def query_history(self, *args, **kwargs):
        self.args = (args, kwargs)
        return []


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

    def get_platform_name(self):
        return "aiocqhttp"

    def get_self_id(self):
        return "10001"


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
            bot_msgs_ttl=-10,
        )
        self.assertEqual(plugin.prob, 1.0)
        self.assertEqual(plugin.ask_threshold, 0.0)
        self.assertEqual(plugin.wake_cd, 10.0)
        self.assertEqual(plugin.bot_msgs_maxlen, 0)
        self.assertEqual(plugin.bot_msgs_ttl, 0)

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


class IntegrationAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_persona_name_uses_resolved_conversation_persona(self):
        plugin = make_plugin(persona_name_cache_ttl=0)
        name = await plugin._get_persona_name(Event.unified_msg_origin, Event())
        self.assertEqual(name, "会话人格")
        kwargs = plugin.persona_mgr.resolve_kwargs
        self.assertEqual(kwargs["conversation_persona_id"], "persona_conversation")
        self.assertEqual(kwargs["platform_name"], "aiocqhttp")

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


if __name__ == "__main__":
    unittest.main()
