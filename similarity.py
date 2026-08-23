import math
import re
from collections import Counter, OrderedDict

import jieba


class Similarity:
    """
    话题相关性检测（当前消息窗口 TF-IDF + Cosine + 时间衰减）。
    - 每次基于当前 user 消息与近期 bot 回复重建文档频率
    - bot 消息预处理：去噪、去重、过滤模板句
    - 候选可携带年龄（秒），按 1/(1+age/tau) 衰减，越新的回复权重越高；
      年龄未知（None）的候选不衰减
    - 分词结果有界 LRU 缓存，重复比对的 Bot 回复零分词开销
    - 无跨会话常驻状态，不会随群/用户数量增长
    """

    def __init__(
        self,
        stopwords: set[str] | None = None,
        bot_template_threshold: int = 2,
        early_stop: float = 0.92,
        token_cache_size: int = 512,
    ):
        self.stopwords = stopwords or {
            "的", "了", "吗", "吧", "啊", "哦", "嗯", "恩",
            "你", "我", "他", "她", "它", "这", "那", "就", "都", "又",
        }

        self.bot_template_threshold = bot_template_threshold
        self.early_stop = early_stop
        self.token_cache_size = token_cache_size
        self._token_cache: OrderedDict[str, list[str]] = OrderedDict()

    def _cached_tokenize(self, text: str) -> list[str]:
        tokens = self._token_cache.get(text)
        if tokens is not None:
            self._token_cache.move_to_end(text)
            return tokens
        tokens = self._tokenize(text)
        self._token_cache[text] = tokens
        self._token_cache.move_to_end(text)
        while len(self._token_cache) > self.token_cache_size:
            self._token_cache.popitem(last=False)
        return tokens

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

    def _preprocess_bot_msgs(
        self, msgs: list[str], ages: list[float | None] | None = None
    ) -> tuple[list[str], list[float | None]]:
        """去噪去重，返回 (cleaned_msgs, 与 cleaned_msgs 对齐的 ages)。"""
        cleaned: list[str] = []
        cleaned_ages: list[float | None] = []
        seen: set[str] = set()
        for i, m in enumerate(msgs):
            if not m:
                continue
            if m in seen:
                continue
            seen.add(m)
            if self._is_noise_msg(m):
                continue
            tokens = self._cached_tokenize(m)
            if len(tokens) <= self.bot_template_threshold:
                continue
            cleaned.append(m)
            if ages is not None:
                cleaned_ages.append(ages[i])
        if ages is None:
            cleaned_ages = [None] * len(cleaned)
        return cleaned, cleaned_ages

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
        ages: list[float | None] | None = None,
    ) -> float:
        # key 保留在公开签名中，便于调用方继续传 UMO；当前算法无跨调用状态。
        del key
        user_tokens = self._cached_tokenize(user_msg)
        if not user_tokens:
            return 0.0

        if ages is not None and len(ages) != len(bot_msgs):
            ages = None  # 长度不符时忽略，避免错位
        cleaned_msgs, cleaned_ages = self._preprocess_bot_msgs(bot_msgs, ages)
        bot_token_docs: list[list[str]] = []
        for message in cleaned_msgs:
            tokens = self._cached_tokenize(message)
            if tokens:
                bot_token_docs.append(tokens)
        if not bot_token_docs:
            return 0.0

        # 时间衰减：age=10 分钟时权重降为 0.5；ages 缺失/None 项不衰减
        # （如无时间戳的兜底源），只靠文本相似度计分。
        tau = 600.0

        def decay(age: float | None) -> float:
            if age is None or age <= 0:
                return 1.0
            return 1.0 / (1.0 + age / tau)

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
        for bot_tokens, age in zip(bot_token_docs, cleaned_ages):
            bot_vec = self._tfidf_vector(
                bot_tokens,
                document_frequency,
                total_docs,
            )
            sim = self._cosine(user_vec, bot_vec) * decay(age)
            if sim > best:
                best = sim
            if sim >= self.early_stop:
                return sim

        return best
