#!/usr/bin/env python3
"""
跨语言搜索模块
多语言支持、语言检测、跨语言向量对齐

改进：
- 支持真实多语言嵌入模型 API 调用
- LLM 翻译查询
- 语言检测增强
- 回退到模拟向量（当 API 不可用时）
"""

import logging
import numpy as np
from typing import List, Optional, Dict, Any, Callable
import re

import sys as _sys
from galaxyos.shared.paths import galaxyos_home, workspace
_ws_root = workspace()
for _p in [_ws_root, "/workspace"]:
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
logger = logging.getLogger(__name__)


class LanguageDetector:
    """
    语言检测器

    支持基于 Unicode 范围和统计特征的检测。
    """

    def __init__(self):
        """初始化语言检测器"""
        self.language_features = {
            'zh': r'[\u4e00-\u9fff]',
            'en': r'[a-zA-Z]',
            'ja': r'[\u3040-\u309f\u30a0-\u30ff]',
            'ko': r'[\uac00-\ud7af]',
            'ru': r'[\u0400-\u04ff]',
            'ar': r'[\u0600-\u06ff]',
            'fr': r'[\u00e0-\u00ff]',
            'de': r'[\u00c0-\u00ff]',
            'pt': r'[\u00e0-\u00ff]',
            'es': r'[\u00e0-\u00ff]',
        }

        # 拉丁语系细分关键词
        self._latin_keywords = {
            'fr': ['le', 'la', 'les', 'un', 'une', 'des', 'est', 'que', 'qui', 'dans'],
            'de': ['der', 'die', 'das', 'und', 'ist', 'ein', 'eine', 'nicht', 'mit', 'auf'],
            'pt': ['o', 'a', 'os', 'as', 'um', 'uma', 'que', 'não', 'em', 'para'],
            'es': ['el', 'la', 'los', 'las', 'un', 'una', 'que', 'no', 'en', 'es'],
        }

    def detect(self, text: str) -> str:
        """
        检测语言

        Args:
            text: 文本

        Returns:
            str: 语言代码
        """
        # 统计各语言字符数
        counts = {}
        for lang, pattern in self.language_features.items():
            matches = re.findall(pattern, text)
            counts[lang] = len(matches)

        # 找到主要语言
        if not counts or max(counts.values()) == 0:
            return 'unknown'

        # 过滤掉拉丁语系的通用匹配（en 也会匹配）
        primary = max(counts, key=counts.get)

        # 如果主要语言是 en，检查是否为拉丁语系其他语言
        if primary == 'en' and counts.get('en', 0) > 0:
            words = re.findall(r'\b[a-zA-Z]+\b', text.lower())
            if words:
                # 用关键词细分
                for lang, keywords in self._latin_keywords.items():
                    overlap = sum(1 for w in words if w in keywords)
                    if overlap >= 2:
                        return lang

        # 对于中文和日文混合的情况
        if counts.get('zh', 0) > 0 and counts.get('ja', 0) > 0:
            if counts['zh'] > counts['ja']:
                return 'zh'
            return 'ja'

        return primary


class CrossLingualEncoder:
    """
    跨语言编码器

    将不同语言的文本映射到统一向量空间。
    支持真实 API 调用和模拟向量回退。
    """

    def __init__(
        self,
        model: str = "multilingual-e5-base",
        supported_languages: List[str] = None,
        embedding_api: Optional[Callable] = None,
        api_config: Optional[Dict] = None,
    ):
        """
        初始化跨语言编码器

        Args:
            model: 多语言模型名称
            supported_languages: 支持的语言列表
            embedding_api: 嵌入 API 函数，签名为 embedding_api(text, model) -> np.ndarray
            api_config: API 配置（base_url, api_key 等）
        """
        self.model = model
        self.supported_languages = supported_languages or ['zh', 'en', 'ja', 'ko', 'ru', 'ar']
        self.detector = LanguageDetector()
        self.embedding_api = embedding_api
        self.api_config = api_config or {}

        # 尝试初始化真实 API
        self._real_api_available = False
        self._init_real_api()

        logger.info(f"跨语言编码器初始化: model={model}, api={'真实' if self._real_api_available else '模拟'}")

    def _init_real_api(self):
        """尝试初始化真实嵌入 API"""
        if self.embedding_api is not None:
            self._real_api_available = True
            return

        # 尝试使用 OpenAI 兼容 API
        try:
            import json
            import os

            base_url = self.api_config.get('base_url') or os.environ.get('EMBEDDING_BASE_URL', '')
            api_key = self.api_config.get('api_key') or os.environ.get('EMBEDDING_API_KEY', '')

            if base_url and api_key:
                self._api_base_url = base_url.rstrip('/')
                self._api_key = api_key
                self._real_api_available = True
                return

            # 尝试从 llm_config 加载
            _openclaw_home = galaxyos_home()
            config_path = os.environ.get(
                "OPENCLAW_LLM_CONFIG",
                os.path.join(_openclaw_home, "workspace/skills/llm-memory-integration/config/llm_config.json")
            )
            if os.path.exists(config_path):
                with open(config_path, 'r') as f:
                    config = json.load(f)
                llm_config = config.get("llm", {})
                self._api_base_url = (llm_config.get("base_url") or "").rstrip('/')
                self._api_key = llm_config.get("api_key") or ""
                if self._api_base_url and self._api_key:
                    self._real_api_available = True

        except Exception as e:
            logger.debug(f"真实 API 初始化失败: {e}")
            self._real_api_available = False

    def encode(self, text: str, language: Optional[str] = None) -> np.ndarray:
        """
        编码文本（跨语言）

        Args:
            text: 文本
            language: 语言代码（可选）

        Returns:
            np.ndarray: 向量
        """
        if language is None:
            language = self.detector.detect(text)

        # 尝试真实 API
        if self._real_api_available:
            try:
                return self._encode_with_api(text, language)
            except Exception as e:
                logger.warning(f"API 编码失败，回退到模拟: {e}")

        # 回退：基于文本哈希的确定性模拟向量
        return self._encode_deterministic(text, language)

    def _encode_with_api(self, text: str, language: str) -> np.ndarray:
        """使用真实 API 编码"""
        if self.embedding_api is not None:
            return self.embedding_api(text, self.model)

        # 使用 OpenAI 兼容 Embeddings API
        import urllib.request
        import json as _json

        url = f"{self._api_base_url}/embeddings"
        data = {
            "model": self.model,
            "input": text,
        }
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._api_key}"
        }

        req = urllib.request.Request(
            url,
            data=_json.dumps(data).encode('utf-8'),
            headers=headers,
            method='POST'
        )

        with urllib.request.urlopen(req, timeout=30) as resp:
            result = _json.loads(resp.read().decode('utf-8'))

        embedding = result['data'][0]['embedding']
        return np.array(embedding, dtype=np.float32)

    def _encode_deterministic(self, text: str, language: str) -> np.ndarray:
        """基于文本哈希的确定性模拟向量（同一文本始终返回相同向量）"""
        import hashlib

        # 使用文本哈希作为随机种子，确保确定性
        seed = int(hashlib.md5(f"{language}:{text}".encode()).hexdigest()[:8], 16)
        rng = np.random.RandomState(seed)
        return rng.randn(768).astype(np.float32)

    def encode_batch(self, texts: List[str]) -> np.ndarray:
        """
        批量编码

        Args:
            texts: 文本列表

        Returns:
            np.ndarray: 向量矩阵
        """
        vectors = [self.encode(text) for text in texts]
        return np.array(vectors, dtype=np.float32)


class CrossLingualSearcher:
    """
    跨语言搜索器

    支持多语言搜索和查询翻译。
    """

    def __init__(
        self,
        encoder: Optional[CrossLingualEncoder] = None,
        llm_client: Optional[Any] = None,
    ):
        """
        初始化跨语言搜索器

        Args:
            encoder: 跨语言编码器
            llm_client: LLM 客户端（用于翻译查询）
        """
        self.encoder = encoder or CrossLingualEncoder()
        self.detector = self.encoder.detector
        self.llm_client = llm_client

        # 向量存储
        self.vectors = []
        self.metadata = []
        self.max_vectors = 500000  # 防止无界内存增长

        logger.info("跨语言搜索器初始化完成")

    def add(self, text: str, language: Optional[str] = None, metadata: Optional[Dict] = None):
        """
        添加文本

        Args:
            text: 文本
            language: 语言代码
            metadata: 元数据
        """
        if language is None:
            language = self.detector.detect(text)

        vector = self.encoder.encode(text, language)

        self.vectors.append(vector)
        self.metadata.append({
            'text': text,
            'language': language,
            'metadata': metadata or {}
        })

        # 防止无界内存增长
        if len(self.vectors) > self.max_vectors:
            self.vectors = self.vectors[-self.max_vectors:]
            self.metadata = self.metadata[-self.max_vectors:]

    def search(
        self,
        query: str,
        top_k: int = 10,
        languages: Optional[List[str]] = None
    ) -> List[Dict]:
        """
        跨语言搜索

        Args:
            query: 查询文本
            top_k: 返回数量
            languages: 过滤语言

        Returns:
            List[Dict]: 搜索结果
        """
        if not self.vectors:
            return []

        query_vector = self.encoder.encode(query)

        vectors = np.array(self.vectors)
        query_norm = query_vector / (np.linalg.norm(query_vector) + 1e-10)
        vectors_norm = vectors / (np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-10)
        scores = np.dot(vectors_norm, query_norm)

        results = []
        for i, score in enumerate(scores):
            if languages and self.metadata[i]['language'] not in languages:
                continue

            results.append({
                'score': float(score),
                'text': self.metadata[i]['text'],
                'language': self.metadata[i]['language'],
                'metadata': self.metadata[i]['metadata']
            })

        results.sort(key=lambda x: x['score'], reverse=True)
        return results[:top_k]

    def translate_query(self, query: str, target_language: str) -> str:
        """
        翻译查询

        优先使用 LLM 翻译，不可用时返回原文。

        Args:
            query: 查询文本
            target_language: 目标语言

        Returns:
            str: 翻译结果
        """
        # 语言名称映射
        lang_names = {
            'zh': '中文', 'en': 'English', 'ja': '日本語',
            'ko': '한국어', 'ru': 'Русский', 'ar': 'العربية',
            'fr': 'Français', 'de': 'Deutsch', 'pt': 'Português', 'es': 'Español',
        }

        source_lang = self.detector.detect(query)
        if source_lang == target_language:
            return query

        # 尝试 LLM 翻译
        if self.llm_client is not None and hasattr(self.llm_client, 'chat'):
            try:
                target_name = lang_names.get(target_language, target_language)
                prompt = (
                    f"请将以下文本翻译为{target_name}，只返回翻译结果：\n\n"
                    f"{query}"
                )
                response = self.llm_client.chat(
                    [{"role": "user", "content": prompt}],
                    max_tokens=500,
                    temperature=0.3,
                )
                if response and response.strip():
                    return response.strip()
            except Exception as e:
                logger.warning(f"LLM 翻译失败: {e}")

        # 回退：返回原文
        logger.info(f"翻译不可用，返回原文 (source={source_lang}, target={target_language})")
        return query

    def search_with_translation(
        self,
        query: str,
        target_language: str,
        top_k: int = 10,
    ) -> List[Dict]:
        """
        翻译查询后搜索

        Args:
            query: 查询文本
            target_language: 目标语言
            top_k: 返回数量

        Returns:
            List[Dict]: 搜索结果
        """
        translated_query = self.translate_query(query, target_language)
        results = self.search(translated_query, top_k, languages=[target_language])
        return results

    def get_stats(self) -> Dict[str, Any]:
        """
        获取统计信息

        Returns:
            Dict: 统计信息
        """
        language_counts = {}
        for m in self.metadata:
            lang = m['language']
            language_counts[lang] = language_counts.get(lang, 0) + 1

        return {
            'total_count': len(self.metadata),
            'language_distribution': language_counts,
            'encoder_type': 'real_api' if self.encoder._real_api_available else 'deterministic_mock',
        }


if __name__ == "__main__":
    # 测试
    print("=== 跨语言搜索测试 ===")

    searcher = CrossLingualSearcher()

    # 添加多语言数据
    searcher.add("这是一段中文文本", language='zh')
    searcher.add("This is an English text", language='en')
    searcher.add("これは日本語のテキストです", language='ja')
    searcher.add("이것은 한국어 텍스트입니다", language='ko')

    # 搜索
    results = searcher.search("中文", top_k=5)
    print(f"搜索结果: {len(results)} 个")

    for r in results:
        print(f"  [{r['language']}] {r['text'][:30]}... (score: {r['score']:.4f})")

    # 翻译测试
    translated = searcher.translate_query("机器学习", "en")
    print(f"\n翻译测试: '机器学习' -> '{translated}'")

    # 统计
    stats = searcher.get_stats()
    print(f"\n统计: {stats}")
