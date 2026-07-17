import math
import re
from collections import Counter

import jieba


class Similarity:
    """
    话题相关性检测（当前消息窗口 TF-IDF + Cosine）。
    - 每次基于当前 user 消息与近期 bot 回复重建文档频率
    - bot 消息预处理：去噪、去重、过滤模板句
    - 无跨会话常驻状态，不会随群/用户数量增长
    """

    def __init__(
        self,
        stopwords: set[str] | None = None,
        bot_template_threshold: int = 2,
        early_stop: float = 0.92,
    ):
        self.stopwords = stopwords or {
            "的", "了", "吗", "吧", "啊", "哦", "嗯", "恩",
            "你", "我", "他", "她", "它", "这", "那", "就", "都", "又",
        }

        self.bot_template_threshold = bot_template_threshold
        self.early_stop = early_stop

    def _tokenize(self, text: str) -> list[str]:
        text = re.sub(r"[^\w一-龥]", " ", text)
        tokens = jieba.lcut(text)
        return [t for t in tokens if t not in self.stopwords and t.strip()]

    @staticmethod
    def _is_noise_msg(text: str) -> bool:
        s = text.strip()
        if not s:
            return True
        if re.fullmatch(r"\[CQ:[^\]]+]", s):
            return True
        if re.fullmatch(r"[\W_]+", s):
            return True
        if re.fullmatch(r"[\d\W_]+", s):
            return True
        return False

    def _preprocess_bot_msgs(self, msgs: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for m in msgs:
            if not m:
                continue
            if m in seen:
                continue
            seen.add(m)
            if self._is_noise_msg(m):
                continue
            tokens = self._tokenize(m)
            if len(tokens) <= self.bot_template_threshold:
                continue
            cleaned.append(m)
        return cleaned

    @staticmethod
    def _tfidf_vector(
        tokens: list[str],
        document_frequency: Counter[str],
        total_docs: int,
    ) -> dict[str, float]:
        tf = Counter(tokens)
        vec: dict[str, float] = {}
        for t, c in tf.items():
            idf = math.log((total_docs + 1) / (document_frequency[t] + 1)) + 1
            vec[t] = c * idf
        return vec

    @staticmethod
    def _cosine(v1: dict[str, float], v2: dict[str, float]) -> float:
        if not v1 or not v2:
            return 0.0
        dot = sum(v * v2.get(k, 0) for k, v in v1.items())
        norm1 = math.sqrt(sum(v * v for v in v1.values()))
        norm2 = math.sqrt(sum(v * v for v in v2.values()))
        if norm1 == 0 or norm2 == 0:
            return 0.0
        return dot / (norm1 * norm2)

    def similarity(
        self,
        key: str,
        user_msg: str,
        bot_msgs: list[str],
    ) -> float:
        # key 保留在公开签名中，便于调用方继续传 UMO；当前算法无跨调用状态。
        del key
        user_tokens = self._tokenize(user_msg)
        if not user_tokens:
            return 0.0

        bot_token_docs: list[list[str]] = []
        for message in self._preprocess_bot_msgs(bot_msgs):
            tokens = self._tokenize(message)
            if tokens:
                bot_token_docs.append(tokens)
        if not bot_token_docs:
            return 0.0

        all_docs = [user_tokens, *bot_token_docs]
        document_frequency: Counter[str] = Counter()
        for tokens in all_docs:
            document_frequency.update(set(tokens))
        total_docs = len(all_docs)
        user_vec = self._tfidf_vector(
            user_tokens,
            document_frequency,
            total_docs,
        )

        best = 0.0
        for bot_tokens in reversed(bot_token_docs):
            bot_vec = self._tfidf_vector(
                bot_tokens,
                document_frequency,
                total_docs,
            )
            sim = self._cosine(user_vec, bot_vec)
            if sim > best:
                best = sim
            if sim >= self.early_stop:
                return sim

        return best
