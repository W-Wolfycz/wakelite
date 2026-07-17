import math
import re
from functools import lru_cache

import jieba


class Sentiment:
    """文本情绪/意图打分（ASK / BORED 两类）。
    - jieba 分词 → 词表匹配（weight × intensity）
    - 否定词衰减、反问句衰减、关键词密度增强
    - Sigmoid 归一化到 0-1
    - 内置 LRU 分词缓存，ask/bored 同一文本只分一次
    """

    STOP = {
        "的", "了", "在", "是", "都", "就", "也", "和", "把",
        "我", "你", "他", "她", "它", "啊", "吧", "嘛",
    }

    # 提问类（按提问明确度分级，weight × intensity）
    ASK_WORDS = {
        # 明确提问 (1.7-1.8)
        "请问": (1.0, 1.8),
        "求解": (1.0, 1.8),
        "求教": (1.0, 1.7),
        "请教": (1.0, 1.7),
        "如何解决": (1.0, 1.8),
        "怎么处理": (1.0, 1.7),
        "怎么办": (1.0, 1.7),
        "为什么": (1.0, 1.6),
        "什么原因": (1.0, 1.6),
        "怎么回事": (1.0, 1.7),
        "谁能帮": (1.0, 1.7),
        # 一般提问 (1.4-1.6)
        "怎么": (0.9, 1.5),
        "如何": (0.9, 1.5),
        "啥意思": (0.9, 1.5),
        "怎么做": (0.9, 1.6),
        "哪里": (0.8, 1.4),
        "哪个": (0.8, 1.4),
        "哪能": (0.8, 1.4),
        "有什么": (0.8, 1.4),
        "有没有": (0.8, 1.4),
        "会不会": (0.8, 1.4),
        "能不能": (0.8, 1.5),
        "可不可以": (0.8, 1.5),
        # 模糊提问 (1.1-1.3)
        "什么": (0.7, 1.3),
        "啥": (0.7, 1.2),
        "呢": (0.5, 1.1),
        "吗": (0.5, 1.0),
        "谁懂": (0.8, 1.4),
        "谁知道": (0.8, 1.4),
        "有人会": (0.7, 1.3),
    }

    # 无聊类（按表达强度分级）
    BORED_WORDS = {
        # 强烈表达 (1.7-1.8)
        "无聊死了": (1.0, 1.8),
        "好无聊": (1.0, 1.7),
        "太无聊": (1.0, 1.7),
        "闷死了": (1.0, 1.7),
        "好没劲": (1.0, 1.6),
        "真没意思": (1.0, 1.6),
        "闲得慌": (1.0, 1.6),
        # 中度表达 (1.4-1.5)
        "无聊": (0.8, 1.5),
        "好闲": (0.8, 1.4),
        "寂寞": (0.8, 1.4),
        "冷清": (0.8, 1.4),
        "空虚": (0.7, 1.3),
        "没人": (0.7, 1.3),
        "冷场": (0.8, 1.5),
        "死群": (0.8, 1.5),
        # 轻度表达 (1.1-1.2)
        "有点闷": (0.6, 1.2),
        "没事做": (0.6, 1.1),
        "打发时间": (0.6, 1.1),
        "求聊天": (0.7, 1.3),
        "有人吗": (0.7, 1.4),
        "在吗": (0.5, 1.0),
        "滴滴": (0.5, 1.0),
    }

    NEGATION_WORDS = {
        "不", "没", "无", "非", "否", "别",
        "不要", "不太", "不太想", "不想",
        "不至于", "算不上", "才不", "才不会",
    }

    RHETORICAL_WORDS = {"难道", "何必", "怎么可以", "怎么可能", "哪能", "岂能", "谁还"}

    def __init__(self, cache_size: int = 2048):
        # 使用独立 Tokenizer，避免修改 AstrBot 进程内其他插件共享的 jieba 词典。
        self._tokenizer = jieba.Tokenizer()
        for word in (
            set(self.ASK_WORDS)
            | set(self.BORED_WORDS)
            | self.NEGATION_WORDS
            | self.RHETORICAL_WORDS
        ):
            self._tokenizer.add_word(word)
        self._install_seg_cache(cache_size)

    def _install_seg_cache(self, size: int) -> None:
        stop = self.STOP

        @lru_cache(maxsize=size)
        def cached(text: str) -> tuple[str, ...]:
            text = re.sub(r"[^\w\s一-鿿]", "", text.lower())
            return tuple(
                w for w in self._tokenizer.lcut(text) if w.strip() and w not in stop
            )

        self._cached_seg = cached

    def seg(self, text: str) -> list[str]:
        """分词并过滤停用词。带 LRU 缓存，同文本只算一次。"""
        return list(self._cached_seg(text))

    def _calculate_confidence(
        self,
        text: str,
        words: list[str],
        keyword_dict: dict,
    ) -> float:
        # 1. 基础匹配分数
        base_score = 0.0
        matched: list[str] = []
        has_rhetorical = any(
            rhetorical in words or rhetorical in text
            for rhetorical in self.RHETORICAL_WORDS
        )

        for i, word in enumerate(words):
            if word in keyword_dict:
                weight, intensity = keyword_dict[word]
                has_negation = any(
                    neg in words[max(0, i - 3):i] for neg in self.NEGATION_WORDS
                )
                if has_negation:
                    weight *= 0.3
                    intensity *= 0.5
                elif has_rhetorical:
                    weight *= 0.7
                    intensity *= 0.8
                base_score += weight * intensity
                matched.append(word)

        if not matched:
            return 0.0

        # 2. 上下文增强
        context_score = 0.0
        density = len(matched) / len(words) if words else 0
        context_score += min(1.0, density * 5) * 0.5
        if len(matched) > 1:
            context_score += min(1.0, (len(matched) - 1) * 0.4)

        # 3. Sigmoid 归一化
        total = base_score + context_score
        confidence = 1 / (1 + math.exp(-4 * (total - 1.5)))
        return min(0.99, confidence)

    def ask(self, text: str) -> float:
        """提问意图强度（0-1）。"""
        normalized = text.lower()
        return self._calculate_confidence(
            normalized,
            self.seg(normalized),
            self.ASK_WORDS,
        )

    def bored(self, text: str) -> float:
        """无聊表达强度（0-1）。"""
        normalized = text.lower()
        return self._calculate_confidence(
            normalized,
            self.seg(normalized),
            self.BORED_WORDS,
        )


sentiment = Sentiment()
