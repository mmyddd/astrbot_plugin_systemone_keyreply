import time
import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from filters import MessageFilter
from utils import (
    CooldownTracker,
    SlidingWindowRateLimiter,
    SimpleTTLCache,
    truncate_reply,
    check_probability,
    calculate_reply_delay,
    normalize_failure_mode,
    normalize_confidence_level,
    normalize_model_mode,
    normalize_reply_style,
    normalize_reply_length_mode,
    normalize_filter_mode,
    normalize_force_reply_mode,
    normalize_reply_delay_mode,
    normalize_typesafe_model,
)
from context_manager import ContextManager
from classifier import MessageClassifier, ClassificationDecision
from typesafe_client import TypeSafeClientWrapper


class TestFilters(unittest.TestCase):
    def test_blacklist_takes_precedence(self):
        # Even if in whitelist, blacklist must block
        allowed, reason = MessageFilter.check_whitelist_blacklist(
            session_id="group_1",
            user_id="user_1",
            filter_mode="both",
            session_whitelist=["group_1"],
            session_blacklist=["group_1"],
            user_whitelist=[],
            user_blacklist=[],
        )
        self.assertFalse(allowed)
        self.assertIn("blacklist", reason)

    def test_multi_candidate_session_ids(self):
        # Candidates: group_id, session_id, umo
        candidates = ["group_100", "aiocqhttp:group:group_100"]
        # Whitelist matched via candidate
        allowed, _ = MessageFilter.check_whitelist_blacklist(
            session_id=candidates,
            user_id="user_1",
            filter_mode="whitelist_only",
            session_whitelist=["group_100"],
            session_blacklist=[],
            user_whitelist=[],
            user_blacklist=[],
        )
        self.assertTrue(allowed)

        # Blacklist matched via candidate
        allowed, reason = MessageFilter.check_whitelist_blacklist(
            session_id=candidates,
            user_id="user_1",
            filter_mode="all_allowed",
            session_whitelist=[],
            session_blacklist=["aiocqhttp:group:group_100"],
            user_whitelist=[],
            user_blacklist=[],
        )
        self.assertFalse(allowed)
        self.assertIn("session_in_blacklist", reason)

    def test_pure_media_detection(self):
        self.assertTrue(MessageFilter.is_pure_media_message(""))
        self.assertTrue(MessageFilter.is_pure_media_message("   "))
        self.assertTrue(MessageFilter.is_pure_media_message("[图片]"))
        self.assertTrue(MessageFilter.is_pure_media_message("[动画表情]"))
        self.assertTrue(MessageFilter.is_pure_media_message("[图片][动画表情]"))
        self.assertTrue(MessageFilter.is_pure_media_message("[CQ:image,file=abc.jpg]"))
        self.assertFalse(MessageFilter.is_pure_media_message("请问这个报错怎么解决？"))
        self.assertFalse(MessageFilter.is_pure_media_message("[图片] 请问这怎么看？"))

    def test_whitelist_modes(self):
        # Whitelist only
        allowed, _ = MessageFilter.check_whitelist_blacklist(
            session_id="group_1",
            user_id="user_1",
            filter_mode="whitelist_only",
            session_whitelist=["group_1"],
            session_blacklist=[],
            user_whitelist=[],
            user_blacklist=[],
        )
        self.assertTrue(allowed)

        # Not in whitelist
        allowed, reason = MessageFilter.check_whitelist_blacklist(
            session_id="group_2",
            user_id="user_1",
            filter_mode="whitelist_only",
            session_whitelist=["group_1"],
            session_blacklist=[],
            user_whitelist=[],
            user_blacklist=[],
        )
        self.assertFalse(allowed)
        self.assertEqual(reason, "not_in_whitelist")

    def test_message_length(self):
        valid, reason = MessageFilter.check_length("a", min_len=2, max_len=10)
        self.assertFalse(valid)
        self.assertIn("too_short", reason)

        valid, _ = MessageFilter.check_length("hello", min_len=2, max_len=10)
        self.assertTrue(valid)

        valid, reason = MessageFilter.check_length("this is way too long", min_len=2, max_len=10)
        self.assertFalse(valid)
        self.assertIn("too_long", reason)

    def test_command_detection(self):
        self.assertTrue(MessageFilter.is_command_message("/help"))
        self.assertTrue(MessageFilter.is_command_message("!status"))
        self.assertTrue(MessageFilter.is_command_message("。ping"))
        self.assertFalse(MessageFilter.is_command_message("请问这个怎么弄？"))

    def test_keywords_and_regex(self):
        keywords = ["有人知道", "求助"]
        self.assertTrue(MessageFilter.matches_keywords("有人知道 Docker 怎么装吗？", keywords))
        self.assertFalse(MessageFilter.matches_keywords("好的谢谢", keywords))

        regex = r"^(有人知道|求助)"
        self.assertTrue(MessageFilter.matches_regex("求助！系统崩了", regex))
        self.assertFalse(MessageFilter.matches_regex("系统崩了，求助", regex))


class TestCooldownAndUtils(unittest.TestCase):
    def test_cooldown_tracker(self):
        tracker = CooldownTracker()
        session = "group_100"
        user = "user_200"

        # Initially not cooling down
        is_cd, _ = tracker.is_cooling_down(session, user, session_cooldown=10, user_cooldown=10)
        self.assertFalse(is_cd)

        # Empty user_id doesn't break
        is_cd, _ = tracker.is_cooling_down(session, "", session_cooldown=0, user_cooldown=10)
        self.assertFalse(is_cd)

        # Record reply
        tracker.record_reply_sent(session, user)

        # Now in cooldown
        is_cd, reason = tracker.is_cooling_down(session, user, session_cooldown=10, user_cooldown=10)
        self.assertTrue(is_cd)
        self.assertIn("session_cooldown", reason)

        # Bypass cooldown
        is_cd, _ = tracker.is_cooling_down(session, user, session_cooldown=10, user_cooldown=10, bypass=True)
        self.assertFalse(is_cd)

    def test_continuous_replies_limit_and_reset(self):
        tracker = CooldownTracker()
        session = "group_100"
        user = "user_200"

        tracker.record_reply_sent(session, user)
        self.assertFalse(tracker.is_continuous_limit_reached(session, max_continuous=2))

        # 1 user message should not instantly reset continuous count
        tracker.record_user_message(session, is_bot=False, session_cooldown=30)
        self.assertFalse(tracker.is_continuous_limit_reached(session, max_continuous=2))

        # Bot replies second time -> reaches limit 2
        tracker.record_reply_sent(session, user)
        self.assertTrue(tracker.is_continuous_limit_reached(session, max_continuous=2))

        # Multiple user messages pass (>=3) -> resets continuous count
        tracker.record_user_message(session, is_bot=False, session_cooldown=30)
        tracker.record_user_message(session, is_bot=False, session_cooldown=30)
        tracker.record_user_message(session, is_bot=False, session_cooldown=30)
        self.assertFalse(tracker.is_continuous_limit_reached(session, max_continuous=2))

        # Explicit reset
        tracker.record_reply_sent(session, user)
        tracker.record_reply_sent(session, user)
        self.assertTrue(tracker.is_continuous_limit_reached(session, max_continuous=2))
        tracker.reset_continuous(session)
        self.assertFalse(tracker.is_continuous_limit_reached(session, max_continuous=2))

    def test_cooldown_cleanup_old_records(self):
        tracker = CooldownTracker()
        old_time = time.time() - 4000
        tracker._session_last_reply["old_session"] = old_time
        tracker._user_last_reply["old_user"] = old_time
        tracker._continuous_reply_count["old_session"] = 3
        tracker._intervening_messages["old_session"] = 1

        tracker.cleanup_old_records(max_idle_seconds=3600)
        self.assertNotIn("old_session", tracker._session_last_reply)
        self.assertNotIn("old_user", tracker._user_last_reply)
        self.assertNotIn("old_session", tracker._continuous_reply_count)

    def test_reply_delay_calculation(self):
        # Disabled
        d = calculate_reply_delay(enabled=False, mode="随机延时", min_delay=1, max_delay=3)
        self.assertEqual(d, 0.0)

        # Fixed delay
        d_fixed = calculate_reply_delay(enabled=True, mode="固定延时", fixed_delay=2.5)
        self.assertEqual(d_fixed, 2.5)

        # Random delay range
        for _ in range(20):
            d_rand = calculate_reply_delay(enabled=True, mode="随机延时 (推荐)", min_delay=1.0, max_delay=3.0)
            self.assertTrue(1.0 <= d_rand <= 3.0)

    def test_normalization_functions(self):
        self.assertEqual(normalize_failure_mode("静默，不回复"), "silent")
        self.assertEqual(normalize_failure_mode("基础规则判断 (问号回复)"), "rule_based")
        self.assertEqual(normalize_failure_mode("直通 AstrBot"), "pass_to_astrbot")

        self.assertEqual(normalize_confidence_level("高"), "high")
        self.assertEqual(normalize_confidence_level("中"), "medium")
        self.assertEqual(normalize_confidence_level("低"), "low")

        self.assertEqual(normalize_model_mode("跟随当前会话模型"), "follow_session")
        self.assertEqual(normalize_model_mode("指定已配置 Provider"), "custom")

        self.assertEqual(normalize_reply_style("自然"), "natural")
        self.assertEqual(normalize_reply_style("群友"), "group_peer")

        self.assertEqual(normalize_reply_length_mode("简短 (50~100字)"), "short")
        self.assertEqual(normalize_filter_mode("仅黑名单"), "blacklist_only")
        self.assertEqual(normalize_force_reply_mode("直接回复"), "direct_reply")
        self.assertEqual(normalize_reply_delay_mode("固定延时"), "fixed")

        # TypeSafe model normalization
        self.assertEqual(normalize_typesafe_model("jev-latest (推荐最新旗舰)"), "jev-latest")
        self.assertEqual(normalize_typesafe_model("jev"), "jev")
        self.assertEqual(normalize_typesafe_model("自定义模型", "custom-jev-v2"), "custom-jev-v2")

        # At-bot mode normalization

    def test_rate_limiter(self):
        limiter = SlidingWindowRateLimiter(limit_per_minute=2)
        self.assertTrue(limiter.allow_request())
        self.assertTrue(limiter.allow_request())
        self.assertFalse(limiter.allow_request())

    def test_ttl_cache(self):
        cache = SimpleTTLCache(ttl_seconds=1)
        cache.set("test message", {"result": "ok"})
        self.assertEqual(cache.get("test message"), {"result": "ok"})
        time.sleep(1.1)
        self.assertIsNone(cache.get("test message"))

    def test_truncate_reply(self):
        text = "这是一个很长的测试回答。第一句话解释原理。第二句话补充细节！第三句话给出结论。"
        truncated = truncate_reply(text, max_chars=25)
        self.assertTrue(len(truncated) <= 26)


class TestContextManager(unittest.TestCase):
    def test_context_management(self):
        cm = ContextManager(max_history_per_session=10)
        session = "sess_1"

        cm.add_message(session, "u1", "张三", "Docker 怎么启动？")
        cm.add_message(session, "u2", "李四", "使用 systemctl start docker")
        cm.add_message(session, "bot", "机器人", "回答完毕", is_bot=True)
        cm.add_message(session, "u1", "张三", "/help", is_command=True)

        # Retrieve ignoring bots and commands
        msgs = cm.get_recent_messages(session, count=5, ignore_bots=True, ignore_commands=True)
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0].content, "Docker 怎么启动？")
        self.assertEqual(msgs[1].content, "使用 systemctl start docker")

        # Formatted string
        context_str = cm.format_context_string(msgs)
        self.assertIn("张三: Docker 怎么启动？", context_str)
        self.assertIn("李四: 使用 systemctl start docker", context_str)

    def test_context_manager_cleanup(self):
        cm = ContextManager(max_history_per_session=10)
        # Add 250 distinct sessions to trigger bounded eviction
        for i in range(250):
            cm.add_message(f"sess_{i}", f"u_{i}", f"User {i}", "Hello world")
        self.assertLessEqual(len(cm._history), 200)


class TestReplyEngine(unittest.IsolatedAsyncioTestCase):
    async def test_reply_engine_fallback_provider(self):
        from reply_engine import ReplyEngine
        context = MagicMock()
        context.get_current_chat_provider_id = AsyncMock(return_value=None)
        default_provider = MagicMock()
        default_provider.id = "fallback_provider"
        context.get_using_provider_async = AsyncMock(return_value=default_provider)
        llm_resp = MagicMock()
        llm_resp.completion_text = "这是回退回复"
        context.llm_generate = AsyncMock(return_value=llm_resp)

        engine = ReplyEngine(context)
        event = MagicMock()
        event.unified_msg_origin = "umo_1"

        reply = await engine.generate_reply(
            event=event,
            current_message="测试",
            chat_context="上下文",
        )
        self.assertEqual(reply, "这是回退回复")
        context.llm_generate.assert_called_once()
        self.assertEqual(context.llm_generate.call_args[1]["chat_provider_id"], "fallback_provider")


class TestClassifier(unittest.IsolatedAsyncioTestCase):
    async def test_failure_modes(self):
        # Test silent failure mode
        wrapper = TypeSafeClientWrapper(api_key="", failure_mode="静默，不回复")
        classifier = MessageClassifier(wrapper)
        state = {"current_message": {"text": "Docker 怎么装？"}}

        decision = await classifier.classify_message(state)
        self.assertFalse(decision.should_reply)
        self.assertTrue(decision.is_fallback)

        # Test pass_to_astrbot failure mode
        wrapper.failure_mode = "pass_to_astrbot"
        decision = await classifier.classify_message(state)
        self.assertTrue(decision.should_reply)
        self.assertTrue(decision.is_fallback)

        # Test rule_based failure mode
        wrapper.failure_mode = "rule_based"
        decision = await classifier.classify_message(state)
        self.assertTrue(decision.should_reply)  # Has question mark

    def test_threshold_and_type_check(self):
        # Chinese & English confidence
        self.assertTrue(MessageClassifier.is_confidence_sufficient("高", "中"))
        self.assertTrue(MessageClassifier.is_confidence_sufficient("high", "中"))
        self.assertFalse(MessageClassifier.is_confidence_sufficient("低", "中"))

        # Chinese & English allowed reply types
        allowed_chinese = ["明确提问", "技术问题"]
        self.assertTrue(MessageClassifier.is_reply_type_allowed("explicit_question", allowed_chinese))
        self.assertTrue(MessageClassifier.is_reply_type_allowed("明确提问", allowed_chinese))
        self.assertFalse(MessageClassifier.is_reply_type_allowed("casual_chat", allowed_chinese))


class TestPluginE2E(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        from main import TypeSafeAutoReplyPlugin

        self.context = MagicMock()
        self.context.get_current_chat_provider_id = AsyncMock(return_value="test_provider")
        llm_resp = MagicMock()
        llm_resp.completion_text = "这是测试自动回复。"
        self.context.llm_generate = AsyncMock(return_value=llm_resp)

        self.config = {
            "enable_plugin": True,
            "enable_group": True,
            "enable_private": False,
            "typesafe_api_key": "ts_test_key",
            "typesafe_model": "jev-latest (推荐最新旗舰)",
            "min_confidence": "中",
            "allowed_reply_types": ["明确提问", "技术问题"],
            "session_cooldown": 10,
            "user_cooldown": 10,
            "max_continuous_replies": 2,
            "reply_probability": 100,
            "enable_reply_delay": False,  # Unit test fast execution
            "debug_log": True,
            # 固定问答表默认关闭，保持既有用例继续覆盖「大模型自由回复」路径
            "reply_source": "大模型自由回复",
            "enable_jev_topic": True,
        }
        self.plugin = TypeSafeAutoReplyPlugin(self.context, self.config)

    def _make_mock_event(
        self, text, sender_id="user_1", group_id="group_1", is_private=False
    ):
        event = MagicMock()
        event.get_message_str.return_value = text
        event.get_sender_id.return_value = sender_id
        event.get_sender_name.return_value = "测试用户"
        event.get_group_id.return_value = group_id
        event.get_session_id.return_value = group_id
        event.get_self_id.return_value = "bot_id"
        event.unified_msg_origin = f"group:{group_id}"
        event.is_private_chat.return_value = is_private
        event.stop_event = MagicMock()
        event.plain_result = lambda msg: {"type": "plain", "text": msg}

        event.get_messages.return_value = []
        return event

    async def test_typesafe_decision_positive(self):
        self.plugin.classifier.classify_message = AsyncMock(
            return_value=ClassificationDecision(
                should_reply=True,
                reply_type="explicit_question",
                confidence_level="high",
                confidence_score=0.9,
                reason="明确提问",
                urgency="normal",
            )
        )
        # Normal message (not @ bot)
        event = self._make_mock_event("请问 Docker 怎么安装？")
        results = [res async for res in self.plugin.on_group_message(event)]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["text"], "这是测试自动回复。")
        self.plugin.classifier.classify_message.assert_called_once()
        event.stop_event.assert_called_once()

    async def test_pure_media_ignored(self):
        event = self._make_mock_event("[图片]")
        results = [res async for res in self.plugin.on_group_message(event)]
        self.assertEqual(len(results), 0)

    async def test_typesafe_decision_negative(self):
        self.plugin.classifier.classify_message = AsyncMock(
            return_value=ClassificationDecision(
                should_reply=False,
                reply_type="casual_chat",
                confidence_level="high",
                confidence_score=0.9,
                reason="闲聊不需回复",
                urgency="low",
            )
        )
        # Normal chat (not @ bot)
        event = self._make_mock_event("今天天气真好啊")
        results = [res async for res in self.plugin.on_group_message(event)]
        self.assertEqual(len(results), 0)
        self.plugin.classifier.classify_message.assert_called_once()

    async def test_disallowed_type_skipped(self):
        self.plugin.classifier.classify_message = AsyncMock(
            return_value=ClassificationDecision(
                should_reply=True,
                reply_type="emotion",
                confidence_level="high",
                confidence_score=0.9,
                reason="情绪表达",
                urgency="normal",
            )
        )
        event = self._make_mock_event("气死我了！")
        results = [res async for res in self.plugin.on_group_message(event)]
        self.assertEqual(len(results), 0)

    async def test_low_confidence_skipped(self):
        self.plugin.classifier.classify_message = AsyncMock(
            return_value=ClassificationDecision(
                should_reply=True,
                reply_type="explicit_question",
                confidence_level="low",
                confidence_score=0.4,
                reason="不确定",
                urgency="normal",
            )
        )
        event = self._make_mock_event("这是什么？")
        results = [res async for res in self.plugin.on_group_message(event)]
        self.assertEqual(len(results), 0)

    async def test_status_command(self):
        event = self._make_mock_event("/typesafe_status")
        results = [res async for res in self.plugin.command_typesafe_status(event)]
        self.assertEqual(len(results), 1)
        self.assertIn("TypeSafe 智能自动回复插件状态", results[0]["text"])
        self.assertIn("TypeSafe 判定模型: jev-latest", results[0]["text"])

    async def test_test_command(self):
        self.plugin.classifier.classify_message = AsyncMock(
            return_value=ClassificationDecision(
                should_reply=True,
                reply_type="technical_issue",
                confidence_level="high",
                confidence_score=0.88,
                reason="技术问题",
                urgency="high",
            )
        )
        event = self._make_mock_event("/typesafe_test 报错怎么解决")
        results = [
            res
            async for res in self.plugin.command_typesafe_test(event, message="报错怎么解决")
        ]
        self.assertEqual(len(results), 2)
        self.assertIn("分析消息", results[0]["text"])
        self.assertIn("结构化判断结果", results[1]["text"])
        self.assertIn("技术问题", results[1]["text"])


    async def test_pure_media_reply_when_not_ignored(self):
        # 验证当关闭“忽略纯媒体/图片表情消息”时，发送纯图片消息能够正常处理并回复，且传递图片 URL
        self.plugin.ignore_pure_media = False
        self.plugin.allowed_reply_types = ["明确提问", "求助", "讨论", "技术问题"]
        self.plugin.classifier.classify_message = AsyncMock(
            return_value=ClassificationDecision(
                should_reply=True,
                reply_type="discussion",
                confidence_level="high",
                confidence_score=0.92,
                reason="用户发送了图片发起讨论",
                urgency="normal",
            )
        )

        event = self._make_mock_event("")
        img_comp = MagicMock()
        img_comp.__class__.__name__ = "Image"
        img_comp.file = "https://example.com/screenshot.png"
        img_comp.url = None
        img_comp.path = None
        event.get_messages.return_value = [img_comp]

        results = [res async for res in self.plugin.on_group_message(event)]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["text"], "这是测试自动回复。")
        self.plugin.classifier.classify_message.assert_called_once()
        # 验证图片 URL 被成功提取并传入了 LLM generate
        self.context.llm_generate.assert_called()
        call_kwargs = self.context.llm_generate.call_args[1]
        self.assertIn("image_urls", call_kwargs)
        self.assertEqual(call_kwargs["image_urls"], ["https://example.com/screenshot.png"])

    async def test_old_config_migration_adds_discussion(self):
        from main import TypeSafeAutoReplyPlugin

        # 模拟用户 AstrBot 原有旧配置（5 项英文默认值）
        old_config = {
            "allowed_reply_types": [
                "explicit_question",
                "seek_help",
                "technical_issue",
                "info_query",
                "recommendation",
            ]
        }
        plugin_instance = TypeSafeAutoReplyPlugin(self.context, old_config)
        # 验证已自动转换为中文，且自动平滑补齐了 "讨论"
        self.assertIn("讨论", plugin_instance.allowed_reply_types)
        self.assertIn("明确提问", plugin_instance.allowed_reply_types)
        self.assertIn("求助", plugin_instance.allowed_reply_types)
        # 所有项目均应为中文
        for item in plugin_instance.allowed_reply_types:
            self.assertIn(item, ["明确提问", "求助", "讨论", "技术问题", "信息查询", "建议请求"])

    async def test_discussion_allowed_and_replies(self):
        # 模拟“你觉得兴发集团怎么样”被 TypeSafe 识别为 discussion (讨论)
        self.plugin.allowed_reply_types = ["明确提问", "求助", "讨论", "技术问题"]
        self.plugin.classifier.classify_message = AsyncMock(
            return_value=ClassificationDecision(
                should_reply=True,
                reply_type="discussion",
                confidence_level="high",
                confidence_score=0.95,
                reason="用户探讨观点看法",
                urgency="normal",
            )
        )
        event = self._make_mock_event("你觉得兴发集团怎么样")
        results = [res async for res in self.plugin.on_group_message(event)]
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["text"], "这是测试自动回复。")

    async def test_early_short_circuit_disabled_private(self):
        # 当未启用私聊时，私聊消息极速短路退出，不写入历史管理器
        event = self._make_mock_event("私聊消息", is_private=True)
        results = [res async for res in self.plugin.on_private_message(event)]
        self.assertEqual(len(results), 0)
        self.assertNotIn("group_1", self.plugin.context_manager._history)


if __name__ == "__main__":
    unittest.main()

