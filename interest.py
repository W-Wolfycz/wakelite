import re
from functools import lru_cache

import jieba


class Interest:
    """自定义关键词包打分：用户配置若干关键词包，消息命中按权重计算兴趣值（0-1）。"""

    def __init__(
        self,
        interest_words: list[list[str]],
        cache_size: int = 2048,
        min_msg_len: int = 3,
        noise_pattern: str = r"^[\W_]+$",
    ):
        self.topics = [list(t) for t in interest_words]
        self.min_msg_len = min_msg_len
        self.noise_re = re.compile(noise_pattern)
        self._install_token_cache(cache_size)

    def _install_token_cache(self, size: int) -> None:
        @lru_cache(maxsize=size)
        def cached(msg: str) -> tuple[str, ...]:
            msg = msg.lower()
            return tuple(t for t in jieba.lcut(msg) if t.strip())

        self._cached_tokenize = cached

    def tokenize(self, msg: str) -> list[str]:
        return list(self._cached_tokenize(msg))

    def _is_noise(self, msg: str) -> bool:
        msg = msg.strip()
        if len(msg) < self.min_msg_len:
            return True
        if self.noise_re.match(msg):
            return True
        noise_words = {"嗯", "啊", "哦", "哈", "哈哈", "嘿嘿", "哎", "欸"}
        if msg in noise_words:
            return True
        return False

    def calc_interest(self, msg: str) -> float:
        """返回兴趣值 0-1。"""
        if self._is_noise(msg):
            return 0.0
        tokens = self.tokenize(msg)
        best = 0.0
        for topic in self.topics:
            score = self._score_topic(msg, tokens, topic)
            best = max(best, score)
        return min(1.0, best)

    def _score_topic(self, msg: str, tokens: list[str], topic_keywords: list[str]) -> float:
        total_weight = 0.0
        gained = 0.0
        for kw in topic_keywords:
            w = self._keyword_weight(kw)
            total_weight += w
            gained += w * self._match_strength(kw, msg, tokens)
        if total_weight == 0:
            return 0.0
        base = gained / total_weight
        return base ** 0.8  # γ < 1 → 强化强关联

    @staticmethod
    def _keyword_weight(kw: str) -> float:
        L = len(kw)
        if L <= 1:
            return 0.8
        if L == 2:
            return 1.2
        if L == 3:
            return 1.5
        return 1.8

    @staticmethod
    def _match_strength(kw: str, msg: str, tokens: list[str]) -> float:
        # 完整 token 命中
        if kw in tokens:
            return 1.0
        # 原文子串命中（句首更强）
        pos = msg.find(kw)
        if pos != -1:
            pos_factor = max(0.5, 1.0 - pos / max(1, len(msg)))
            return 0.7 * pos_factor
        # 半命中（关键词部分字符被切开）
        chars_hit = sum(1 for c in kw if c in msg)
        if chars_hit >= len(kw) / 2:
            return 0.35
        return 0.0
