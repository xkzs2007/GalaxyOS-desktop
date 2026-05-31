"""Embedding 引擎 - 支持预计算和批量"""
import json
import gzip
import hashlib
import urllib.request
import logging
import os
from pathlib import Path
from typing import List, Optional

# 初始化 logger
logger = logging.getLogger(__name__)

# 默认配置（从环境变量读取）
DEFAULT_EMBEDDING_API_URL = os.environ.get("EMBEDDING_API_URL", "http://localhost:8000/v1/embeddings")
DEFAULT_EMBEDDING_API_KEY = os.environ.get("EMBEDDING_API_KEY", "")


class EmbeddingEngine:
    def __init__(
        self,
        api_url: str = None,
        api_key: str = None,
        model: str = "bge-m3",
        dimensions: int = 1024
    ):
        self.api_url = api_url or DEFAULT_EMBEDDING_API_URL
        self.api_key = api_key or DEFAULT_EMBEDDING_API_KEY
        self.model = model
        self.dimensions = dimensions
        self.cache = {}
        self.precomputed = {}
        _openclaw_home = os.environ.get("OPENCLAW_HOME", str(Path.home() / ".openclaw"))
        self.cache_dir = Path(os.environ.get("OPENCLAW_CACHE_DIR", os.path.join(_openclaw_home, "memory-tdai", ".cache")))
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self._load_precomputed()

    def _load_precomputed(self):
        """加载预计算向量"""
        file = self.cache_dir / "precomputed.json"
        if file.exists():
            try:
                self.precomputed = json.loads(file.read_text())
            except Exception as e:
                logger.error(f"操作失败: {e}")

    def _save_precomputed(self):
        """保存预计算向量"""
        file = self.cache_dir / "precomputed.json"
        file.write_text(json.dumps(self.precomputed))

    def _hash(self, text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    def get(self, text: str) -> Optional[List[float]]:
        """获取单个向量"""
        # 检查预计算
        h = self._hash(text)
        if h in self.precomputed:
            return self.precomputed[h]

        # 检查缓存
        if text in self.cache:
            return self.cache[text]

        # 调用 API
        result = self.batch([text])
        return result[0] if result else None

    def encode(self, text: str) -> Optional[List[float]]:
        """
        编码单个文本为向量（encode 是 get 的别名）
        """
        return self.get(text)

    def batch(self, texts: List[str]) -> List[Optional[List[float]]]:
        """批量获取向量"""
        results = [None] * len(texts)
        uncached = []
        indices = []

        for i, text in enumerate(texts):
            h = self._hash(text)
            if h in self.precomputed:
                results[i] = self.precomputed[h]
            elif text in self.cache:
                results[i] = self.cache[text]
            else:
                uncached.append(text)
                indices.append(i)

        if uncached:
            data = json.dumps({
                "input": uncached,
                "model": self.model,
                "dimensions": self.dimensions
            }).encode('utf-8')

            req = urllib.request.Request(
                self.api_url, data=data,
                headers={
                    "Content-Type": "application/json",
                    "Authorization": f"Bearer {self.api_key}",
                    "Accept-Encoding": "gzip, deflate",
                }
            )

            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    raw = resp.read()
                    # 处理 gzip 压缩响应
                    if resp.headers.get('Content-Encoding') == 'gzip':
                        raw = gzip.decompress(raw)
                    elif resp.headers.get('Content-Encoding') == 'deflate':
                        import zlib
                        raw = zlib.decompress(raw)
                    result = json.loads(raw.decode('utf-8'))
                    for j, item in enumerate(result['data']):
                        emb = item['embedding']
                        self.cache[uncached[j]] = emb
                        self.precomputed[self._hash(uncached[j])] = emb
                        results[indices[j]] = emb
                    self._save_precomputed()
            except Exception as e:
                logger.error(f"操作失败: {e}")

        return results
