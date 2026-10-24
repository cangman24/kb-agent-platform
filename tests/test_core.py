# -*- coding: utf-8 -*-
"""tests/test_core.py —— core 基建单测。纯标准库，零第三方依赖，全部离线。

覆盖本仓库真正依赖的那些模块：
    llm.py     —— 限流 / 缓存 / 错误分类 / 重试退避 / 拒答判定
    sse.py     —— SSE 帧编码与解析（Agent 流式输出的正确性）
    text.py    —— 中文分词与数值抽取（BM25 与规则匹配的地基）
    bm25.py    —— 内置知识库的检索后端
    metrics.py —— 检索指标

★ 为什么这些必须测：LLM 客户端的重试与缓存一旦写错，现象是
  "偶发超时 / 缓存永远不命中"，在真机上极难复现和排查。
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.bm25 import BM25Index  # noqa: E402
from core.llm import (  # noqa: E402
    DiskCache, LLMClient, LLMError, NonRetryableError, RetryableError,
    TokenBucket, is_refusal,
)
from core.metrics import hit_at_k, mrr_at_k  # noqa: E402
from core.sse import (  # noqa: E402
    format_heartbeat, format_sse, iter_sse, parse_sse,
)
from core.text import extract_measures, jaccard, tokenize  # noqa: E402


def ok_response(text="好的", model="deepseek-chat", pt=10, ct=5):
    return {"choices": [{"message": {"content": text}}],
            "model": model,
            "usage": {"prompt_tokens": pt, "completion_tokens": ct}}


# ================================================================== 限流
class TestTokenBucket(unittest.TestCase):
    def test_rejects_non_positive_rate(self):
        with self.assertRaises(ValueError):
            TokenBucket(0)

    def test_capacity_allows_initial_burst(self):
        bucket = TokenBucket(rate=1.0, capacity=3.0)
        self.assertTrue(all(bucket.acquire(timeout=0.1) for _ in range(3)))

    def test_exhausted_bucket_can_recover(self):
        """令牌耗尽后按速率恢复，而不是永久卡死。"""
        bucket = TokenBucket(rate=50.0, capacity=1.0)
        self.assertTrue(bucket.acquire(timeout=0.1))
        self.assertTrue(bucket.acquire(timeout=1.0))  # 20ms 后补充


# ================================================================== 缓存
class TestDiskCache(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="kb_cache_")
        self.cache = DiskCache(path=self.dir)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_set_then_get(self):
        key = self.cache.make_key("chat", "m", [{"role": "user"}])
        self.assertTrue(self.cache.set(key, {"text": "答案"}))
        self.assertEqual(self.cache.get(key)["text"], "答案")

    def test_key_is_stable(self):
        """★ key 必须稳定：顺序不同但内容相同的参数要得到同一个 key。"""
        a = DiskCache.make_key("chat", "m", {"temperature": 0.0, "top_k": 5})
        b = DiskCache.make_key("chat", "m", {"top_k": 5, "temperature": 0.0})
        self.assertEqual(a, b)

    def test_missing_key_counts_miss(self):
        self.assertIsNone(self.cache.get("nonexistent"))
        self.assertEqual(self.cache.misses, 1)

    def test_disabled_cache_never_writes(self):
        c = DiskCache(path=os.path.join(self.dir, "off"), enabled=False)
        self.assertFalse(c.set("k", {"text": "x"}))
        self.assertIsNone(c.get("k"))


# ================================================================== 错误分类
class TestClassifyError(unittest.TestCase):
    def _with_status(self, code):
        exc = RuntimeError("boom")
        exc.status_code = code  # type: ignore[attr-defined]
        return LLMClient.classify_error(exc)

    def test_429_is_retryable(self):
        self.assertIs(self._with_status(429), RetryableError)

    def test_5xx_is_retryable(self):
        for code in (500, 502, 503, 504):
            self.assertIs(self._with_status(code), RetryableError)

    def test_4xx_is_not_retryable(self):
        for code in (400, 401, 403, 404):
            self.assertIs(self._with_status(code), NonRetryableError)

    def test_message_keyword_fallback(self):
        self.assertIs(LLMClient.classify_error(RuntimeError("connection reset")),
                      RetryableError)

    def test_unknown_defaults_to_non_retryable(self):
        self.assertIs(LLMClient.classify_error(ValueError("奇怪的错误")),
                      NonRetryableError)


# ================================================================== 客户端
class TestLLMClient(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="kb_llm_")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def _client(self, transport, **kw):
        kw.setdefault("rate", None)      # 单测里不限流
        kw.setdefault("max_retries", 2)
        kw.setdefault("base_delay", 0.001)
        kw.setdefault("sleep", lambda s: None)
        kw.setdefault("jitter", False)
        return LLMClient(transport=transport, **kw)

    def test_chat_returns_text_and_usage(self):
        c = self._client(lambda **kw: ok_response("答案 [1]", pt=7, ct=3))
        r = c.chat([{"role": "user", "content": "hi"}])
        self.assertEqual(r.text, "答案 [1]")
        self.assertEqual(r.total_tokens, 10)
        self.assertEqual(c.usage["calls"], 1)

    def test_retry_then_success(self):
        """★ 可重试错误第 2 次成功 —— 重试逻辑真在工作。"""
        state = {"n": 0}

        def flaky(**kw):
            state["n"] += 1
            if state["n"] == 1:
                exc = RuntimeError("rate limit exceeded")
                exc.status_code = 429  # type: ignore[attr-defined]
                raise exc
            return ok_response("恢复")

        r = self._client(flaky).chat([{"role": "user", "content": "hi"}])
        self.assertEqual(r.text, "恢复")
        self.assertEqual(r.attempts, 2)
        self.assertEqual(state["n"], 2)

    def test_non_retryable_fails_fast(self):
        def bad(**kw):
            exc = RuntimeError("invalid api key")
            exc.status_code = 401  # type: ignore[attr-defined]
            raise exc

        with self.assertRaises(NonRetryableError):
            self._client(bad).chat([{"role": "user", "content": "hi"}])

    def test_cache_hit_skips_transport(self):
        calls = {"n": 0}

        def counted(**kw):
            calls["n"] += 1
            return ok_response("缓存我")

        c = self._client(counted, cache=DiskCache(path=self.dir))
        first = c.chat([{"role": "user", "content": "hi"}])
        second = c.chat([{"role": "user", "content": "hi"}])
        self.assertFalse(first.cached)
        self.assertTrue(second.cached)
        self.assertEqual(second.text, "缓存我")
        self.assertEqual(calls["n"], 1, "第二次必须命中缓存，不该再打 API")

    def test_chat_json_parses(self):
        c = self._client(lambda **kw: ok_response('{"tool": "kb_search"}'))
        self.assertEqual(c.chat_json([{"role": "user"}])["tool"], "kb_search")

    def test_chat_json_rejects_invalid(self):
        c = self._client(lambda **kw: ok_response("这不是 JSON"))
        with self.assertRaises(LLMError):
            c.chat_json([{"role": "user"}])


class TestIsRefusal(unittest.TestCase):
    def test_empty_is_refusal(self):
        self.assertTrue(is_refusal(""))

    def test_refusal_phrase_at_head(self):
        self.assertTrue(is_refusal("知识库中未找到相关内容。"))

    def test_normal_answer_not_refusal(self):
        self.assertFalse(is_refusal("精度要求为 0.90 [1]"))

    def test_late_mention_not_refusal(self):
        """★ 只在开头附近匹配，避免正文里"顺带提到"被误判成整条拒答。"""
        long_answer = "根据文档 [1]，精度要求为 0.90。" + "补充说明。" * 10 + \
                      "另外，知识库中未找到历史版本记录。"
        self.assertFalse(is_refusal(long_answer))


# ================================================================== SSE
class TestSSE(unittest.TestCase):
    def test_format_sse_shape(self):
        frame = format_sse({"a": 1}, event="tool_call", event_id="3")
        self.assertIn("id: 3", frame)
        self.assertIn("event: tool_call", frame)
        self.assertIn('data: {"a": 1}', frame)
        self.assertTrue(frame.endswith("\n\n"))

    def test_multiline_data_is_prefixed_line_by_line(self):
        """★ data 里的换行必须逐行加前缀，否则一条消息会被拆成多条。"""
        frame = format_sse("第一行\n第二行")
        self.assertEqual(frame.count("data: "), 2)

    def test_roundtrip(self):
        events = [{"seq": 1, "type": "start", "data": {"query": "q"}},
                  {"seq": 2, "type": "answer", "data": {"text": "a"}}]
        text = "".join(iter_sse(events))
        parsed = parse_sse(text)
        self.assertEqual([e["event"] for e in parsed], ["start", "answer"])
        self.assertEqual(json.loads(parsed[0]["data"])["query"], "q")
        self.assertEqual(parsed[0]["id"], "1")

    def test_heartbeat_is_comment_frame(self):
        """心跳是注释帧：解析出来带 comment，没有 event —— 不该污染业务事件。"""
        self.assertTrue(format_heartbeat().startswith(": heartbeat"))
        parsed = parse_sse(format_heartbeat())
        self.assertEqual(len(parsed), 1)
        self.assertEqual(parsed[0]["comment"], "heartbeat")
        self.assertNotIn("event", parsed[0])

    def test_iter_sse_inserts_heartbeat(self):
        events = [{"seq": i, "type": "tok", "data": {"t": i}} for i in range(4)]
        text = "".join(iter_sse(events, heartbeat_every=2))
        self.assertIn(": heartbeat", text)


# ================================================================== 文本
class TestText(unittest.TestCase):
    def test_tokenize_produces_cjk_bigrams(self):
        toks = tokenize("精度要求")
        self.assertIn("精度", toks)
        self.assertIn("要求", toks)

    def test_tokenize_lowercases_latin(self):
        self.assertIn("map", tokenize("mAP"))

    def test_extract_measures(self):
        self.assertIn("0.90", extract_measures("精度不低于 0.90"))

    def test_jaccard_symmetry(self):
        self.assertAlmostEqual(jaccard(["a", "b"], ["b", "c"]),
                               jaccard(["b", "c"], ["a", "b"]))

    def test_jaccard_identical_is_one(self):
        self.assertEqual(jaccard(["a"], ["a"]), 1.0)


# ================================================================== BM25
class TestBM25(unittest.TestCase):
    def _index(self):
        idx = BM25Index()
        idx.add("c1", "工单复核流程 由 复核岗 执行")
        idx.add("c2", "账号权限 申请 需要 审批")
        idx.finalize()
        return idx

    def test_search_ranks_relevant_first(self):
        hits = self._index().search("复核", top_k=2)
        self.assertEqual(hits[0][0], "c1")

    def test_unknown_term_returns_empty(self):
        self.assertEqual(self._index().search("火星文"), [])

    def test_len(self):
        self.assertEqual(len(self._index()), 2)


# ================================================================== 指标
class TestMetrics(unittest.TestCase):
    def test_hit_at_k(self):
        self.assertEqual(hit_at_k(["a", "b", "c"], ["c"], 3), 1.0)
        self.assertEqual(hit_at_k(["a", "b", "c"], ["c"], 2), 0.0)

    def test_mrr_is_one_based(self):
        """★ MRR 必须用 1-based 排名 —— 用 0 会让指标整体虚高。"""
        self.assertAlmostEqual(mrr_at_k(["a", "b"], ["b"], 5), 0.5)


if __name__ == "__main__":
    unittest.main()
