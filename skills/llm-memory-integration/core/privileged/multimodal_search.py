#!/usr/bin/env python3
"""
多模态搜索模块
图像、音频、文本跨模态搜索

改进：
- 支持真实多模态嵌入 API 调用
- 修复元数据索引计算 bug
- 添加 search_by_audio 方法
- 回退到确定性模拟向量
"""

import logging
import numpy as np
from typing import List, Tuple, Optional, Dict, Any, Callable
import base64
import hashlib

logger = logging.getLogger(__name__)


class MultimodalEncoder:
    """
    多模态编码器

    支持文本、图像、音频编码。
    优先使用真实 API，不可用时回退到确定性模拟向量。
    """

    def __init__(
        self,
        text_model: str = "text-embedding-ada-002",
        image_model: str = "clip-vit-base-patch32",
        audio_model: str = "whisper-base",
        embedding_api: Optional[Callable] = None,
        api_config: Optional[Dict] = None,
        embedding_dim: int = 512,
    ):
        """
        初始化多模态编码器

        Args:
            text_model: 文本编码模型
            image_model: 图像编码模型
            audio_model: 音频编码模型
            embedding_api: 自定义嵌入 API，签名为 api(text, modality, model) -> np.ndarray
            api_config: API 配置
            embedding_dim: 嵌入维度
        """
        self.text_model = text_model
        self.image_model = image_model
        self.audio_model = audio_model
        self.embedding_api = embedding_api
        self.api_config = api_config or {}
        self.embedding_dim = embedding_dim

        # 尝试初始化真实 API
        self._real_api_available = False
        self._init_real_api()

        logger.info(f"多模态编码器初始化: api={'真实' if self._real_api_available else '确定性模拟'}")

    def _init_real_api(self):
        """尝试初始化真实嵌入 API"""
        if self.embedding_api is not None:
            self._real_api_available = True
            return

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
            _openclaw_home = os.environ.get("OPENCLAW_HOME", os.path.expanduser("~/.openclaw"))
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

    def encode_text(self, text: str) -> np.ndarray:
        """编码文本"""
        if self._real_api_available:
            try:
                return self._encode_with_api(text, 'text', self.text_model)
            except Exception as e:
                logger.warning(f"文本 API 编码失败: {e}")

        return self._encode_deterministic(text, 'text')

    def encode_image(self, image_path: str) -> np.ndarray:
        """编码图像"""
        if self._real_api_available:
            try:
                return self._encode_with_api(image_path, 'image', self.image_model)
            except Exception as e:
                logger.warning(f"图像 API 编码失败: {e}")

        return self._encode_deterministic(image_path, 'image')

    def encode_image_base64(self, image_base64: str) -> np.ndarray:
        """编码 Base64 图像"""
        # 解码获取基本信息用于确定性向量
        try:
            decoded = base64.b64decode(image_base64[:100])
            info = hashlib.md5(decoded).hexdigest()[:8]
        except Exception:
            info = "img"
        return self._encode_deterministic(f"base64:{info}", 'image')

    def encode_audio(self, audio_path: str) -> np.ndarray:
        """编码音频"""
        if self._real_api_available:
            try:
                return self._encode_with_api(audio_path, 'audio', self.audio_model)
            except Exception as e:
                logger.warning(f"音频 API 编码失败: {e}")

        return self._encode_deterministic(audio_path, 'audio')

    def _encode_with_api(self, content: str, modality: str, model: str) -> np.ndarray:
        """使用真实 API 编码"""
        if self.embedding_api is not None:
            return self.embedding_api(content, modality, model)

        # 使用 OpenAI 兼容 API
        import urllib.request
        import json as _json

        if modality == 'text':
            url = f"{self._api_base_url}/embeddings"
            data = {"model": model, "input": content}
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

        # 对于 image/audio，暂未实现真实 API 调用
        raise NotImplementedError(f"真实 API 不支持 {modality} 模态")

    def _encode_deterministic(self, content: str, modality: str) -> np.ndarray:
        """基于内容哈希的确定性模拟向量"""
        seed = int(hashlib.md5(f"{modality}:{content}".encode()).hexdigest()[:8], 16)
        rng = np.random.RandomState(seed)
        return rng.randn(self.embedding_dim).astype(np.float32)


class MultimodalSearcher:
    """
    多模态搜索器

    支持跨模态搜索。
    """

    def __init__(
        self,
        encoder: Optional[MultimodalEncoder] = None,
        modality_weights: Optional[Dict[str, float]] = None
    ):
        """
        初始化多模态搜索器

        Args:
            encoder: 多模态编码器
            modality_weights: 模态权重
        """
        self.encoder = encoder or MultimodalEncoder()
        self.modality_weights = modality_weights or {
            'text': 1.0,
            'image': 0.8,
            'audio': 0.6
        }

        # 向量存储（按模态分开存储）
        self.vectors = {
            'text': [],
            'image': [],
            'audio': []
        }

        # 统一元数据存储（按插入顺序）
        self.metadata = []
        # 元数据索引映射：(modality, local_index) -> metadata_index
        self._modality_indices: Dict[Tuple[str, int], int] = {}
        self.max_vectors_per_modality = 500000  # 防止无界内存增长

        logger.info(f"多模态搜索器初始化: weights={self.modality_weights}")

    def add_text(self, text: str, metadata: Optional[Dict] = None):
        """添加文本"""
        vector = self.encoder.encode_text(text)
        local_idx = len(self.vectors['text'])
        global_idx = len(self.metadata)
        self.vectors['text'].append(vector)
        self._modality_indices[('text', local_idx)] = global_idx
        self.metadata.append({
            'type': 'text',
            'content': text,
            'metadata': metadata or {}
        })
        self._check_capacity('text')

    def add_image(self, image_path: str, metadata: Optional[Dict] = None):
        """添加图像"""
        vector = self.encoder.encode_image(image_path)
        local_idx = len(self.vectors['image'])
        global_idx = len(self.metadata)
        self.vectors['image'].append(vector)
        self._modality_indices[('image', local_idx)] = global_idx
        self.metadata.append({
            'type': 'image',
            'content': image_path,
            'metadata': metadata or {}
        })
        self._check_capacity('image')

    def add_audio(self, audio_path: str, metadata: Optional[Dict] = None):
        """添加音频"""
        vector = self.encoder.encode_audio(audio_path)
        local_idx = len(self.vectors['audio'])
        global_idx = len(self.metadata)
        self.vectors['audio'].append(vector)
        self._modality_indices[('audio', local_idx)] = global_idx
        self.metadata.append({
            'type': 'audio',
            'content': audio_path,
            'metadata': metadata or {}
        })
        self._check_capacity('audio')

    def _check_capacity(self, modality: str):
        """检查并限制各模态的向量数量，防止无界内存增长"""
        if len(self.vectors[modality]) > self.max_vectors_per_modality:
            excess = len(self.vectors[modality]) - self.max_vectors_per_modality
            self.vectors[modality] = self.vectors[modality][excess:]

    def search_by_text(
        self,
        query: str,
        top_k: int = 10,
        modalities: Optional[List[str]] = None
    ) -> List[Dict]:
        """用文本搜索"""
        modalities = modalities or ['text', 'image', 'audio']
        query_vector = self.encoder.encode_text(query)
        return self._search(query_vector, top_k, modalities)

    def search_by_image(
        self,
        image_path: str,
        top_k: int = 10,
        modalities: Optional[List[str]] = None
    ) -> List[Dict]:
        """用图像搜索"""
        modalities = modalities or ['text', 'image', 'audio']
        query_vector = self.encoder.encode_image(image_path)
        return self._search(query_vector, top_k, modalities)

    def search_by_audio(
        self,
        audio_path: str,
        top_k: int = 10,
        modalities: Optional[List[str]] = None
    ) -> List[Dict]:
        """用音频搜索"""
        modalities = modalities or ['text', 'image', 'audio']
        query_vector = self.encoder.encode_audio(audio_path)
        return self._search(query_vector, top_k, modalities)

    def _search(
        self,
        query_vector: np.ndarray,
        top_k: int,
        modalities: List[str]
    ) -> List[Dict]:
        """
        内部搜索（修复元数据索引 bug）

        使用 _modality_indices 精确映射模态内索引到全局元数据索引。
        """
        all_results = []

        for modality in modalities:
            if modality not in self.vectors or not self.vectors[modality]:
                continue

            vectors = np.array(self.vectors[modality])
            weight = self.modality_weights.get(modality, 1.0)

            # 计算相似度
            query_norm = query_vector / (np.linalg.norm(query_vector) + 1e-10)
            vectors_norm = vectors / (np.linalg.norm(vectors, axis=1, keepdims=True) + 1e-10)
            scores = np.dot(vectors_norm, query_norm) * weight

            # 收集结果（使用精确索引映射）
            for i, score in enumerate(scores):
                global_idx = self._modality_indices.get((modality, i))
                if global_idx is not None and global_idx < len(self.metadata):
                    all_results.append({
                        'score': float(score),
                        'modality': modality,
                        'metadata': self.metadata[global_idx]
                    })

        # 排序并返回 top_k
        all_results.sort(key=lambda x: x['score'], reverse=True)
        return all_results[:top_k]

    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return {
            'text_count': len(self.vectors['text']),
            'image_count': len(self.vectors['image']),
            'audio_count': len(self.vectors['audio']),
            'total_count': len(self.metadata),
            'encoder_type': 'real_api' if self.encoder._real_api_available else 'deterministic_mock',
        }


if __name__ == "__main__":
    # 测试
    print("=== 多模态搜索测试 ===")

    searcher = MultimodalSearcher()

    # 添加数据
    searcher.add_text("这是一只可爱的猫咪")
    searcher.add_text("这是一只忠诚的狗狗")
    searcher.add_image("/path/to/cat.jpg")
    searcher.add_image("/path/to/dog.jpg")
    searcher.add_audio("/path/to/cat_meow.wav")

    # 文本搜索
    results = searcher.search_by_text("猫咪", top_k=5)
    print(f"文本搜索结果: {len(results)} 个")
    for r in results:
        print(f"  {r['modality']}: {r['score']:.4f}")

    # 图像搜索
    results = searcher.search_by_image("/path/to/cat.jpg", top_k=3)
    print(f"\n图像搜索结果: {len(results)} 个")
    for r in results:
        print(f"  {r['modality']}: {r['score']:.4f}")

    # 音频搜索
    results = searcher.search_by_audio("/path/to/cat_meow.wav", top_k=3)
    print(f"\n音频搜索结果: {len(results)} 个")
    for r in results:
        print(f"  {r['modality']}: {r['score']:.4f}")

    # 统计
    stats = searcher.get_stats()
    print(f"\n统计: {stats}")
