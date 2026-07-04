import math
import re
from collections import defaultdict, deque

import jieba


class Similarity:
    """
    话题相关性检测（TF-IDF + Cosine）。
    - 会话隔离（按 key 分桶，调用方决定用 gid 还是 umo）
    - bot 消息预处理：去噪、去重、过滤模板句
    """

    def __init__(
        self,
        history_limit: int = 120,
        stopwords: set[str] | None = None,
        bot_template_threshold: int = 2,
        early_stop: float = 0.92,
    ):
        self._GROUP_DATA: dict[str, dict] = defaultdict(
            lambda: {
                "history": deque(maxlen=history_limit),
                "idf": defaultdict(int),
                "total_docs": 0,
            }
        )

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

    def _update_idf(self, key: str, tokens: set[str]) -> None:
        data = self._GROUP_DATA[key]
        for t in tokens:
            data["idf"][t] += 1
        data["total_docs"] += 1

    def _tfidf_vector(self, key: str, tokens: list[str]) -> dict[str, float]:
        data = self._GROUP_DATA[key]
        total_docs = data["total_docs"] or 1

        tf: dict[str, int] = defaultdict(int)
        for t in tokens:
            tf[t] += 1

        vec: dict[str, float] = {}
        for t, c in tf.items():
            idf = math.log((total_docs + 1) / (data["idf"][t] + 1)) + 1
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
        update_history: bool = False,
    ) -> float:
        user_tokens = self._tokenize(user_msg)
        if not user_tokens:
            return 0.0

        if update_history:
            entry = " ".join(user_tokens)
            self._GROUP_DATA[key]["history"].append(entry)
            self._update_idf(key, set(user_tokens))

        user_vec = self._tfidf_vector(key, user_tokens)

        bot_list = self._preprocess_bot_msgs(bot_msgs)[::-1]

        best = 0.0
        for bm in bot_list:
            bm_tokens = self._tokenize(bm)
            if not bm_tokens:
                continue
            bm_vec = self._tfidf_vector(key, bm_tokens)
            sim = self._cosine(user_vec, bm_vec)
            if sim > best:
                best = sim
            if sim >= self.early_stop:
                return sim

        return best
