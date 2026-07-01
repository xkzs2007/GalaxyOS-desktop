from typing import List, Optional
import logging
import json
import gzip
import hashlib
import urllib.request
from pathlib import Path
import os

logger = logging.getLogger(__name__)

# 默认配置
DEFAULT_LLM_API_URL = os.environ.get("LLM_API_URL", "http://localhost:8000/v1/chat/completions")
DEFAULT_LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
DEFAULT_LLM_UID = os.environ.get("LLM_UID", "default")


class LLMEngine:
    def __init__(
        self,
        url: str = None,
        key: str = None,
        uid: str = None,
        model: str = "LLM_GLM5",
        cache_dir: str = None
    ):
        self.url = url or DEFAULT_LLM_API_URL
        self.key = key or DEFAULT_LLM_API_KEY
        self.uid = uid or DEFAULT_LLM_UID
        self.model = model
        _openclaw_home = os.environ.get("OPENCLAW_HOME", str(Path.home() / ".openclaw"))
        self.cache_dir = Path(cache_dir) if cache_dir else Path(os.environ.get("OPENCLAW_CACHE_DIR", os.path.join(_openclaw_home, "memory-tdai", ".cache")))
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def _hash(self, text: str) -> str:
        return hashlib.sha256(text.encode()).hexdigest()

    def _get_cache(self, key: str) -> Optional[str]:
        file = self.cache_dir / f"llm_{key}.json"
        if file.exists():
            try:
                data = json.loads(file.read_text())
                return data.get("content")
            except Exception as e:
                logger.error(f"操作失败: {e}")
        return None

    def _set_cache(self, key: str, content: str):
        file = self.cache_dir / f"llm_{key}.json"
        file.write_text(json.dumps({"content": content}))

    def chat(
            self,
            prompt: str,
            max_tokens: int = 100,
            temperature: float = 0.3,
            use_cache: bool = True) -> Optional[str]:
        """对话（支持缓存）"""
        key = self._hash(prompt)

        if use_cache:
            cached = self._get_cache(key)
            if cached:
                return cached

        data = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": temperature,
            "stream": True
        }

        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "Accept-Encoding": "gzip, deflate",
            "x-request-from": "openclaw",
            "x-uid": self.uid,
            "x-api-key": self.key
        }

        try:
            req = urllib.request.Request(
                self.url, data=json.dumps(data).encode('utf-8'),
                headers=headers, method='POST'
            )

            content = ""
            with urllib.request.urlopen(req, timeout=30) as resp:
                # 处理 gzip 压缩响应（非 SSE 场景）
                encoding = resp.headers.get('Content-Encoding', '')
                if encoding == 'gzip':
                    raw = gzip.decompress(resp.read())
                    # 尝试作为完整 JSON 解析
                    try:
                        result = json.loads(raw.decode('utf-8'))
                        if 'choices' in result:
                            content = result['choices'][0].get('message', {}).get('content', '')
                    except (json.JSONDecodeError, KeyError):
                        # 如果不是 JSON，按行解析 SSE
                        for line in raw.decode('utf-8').split('\n'):
                            if line.startswith('data: '):
                                try:
                                    chunk = json.loads(line[6:])
                                    if 'choices' in chunk:
                                        delta = chunk['choices'][0].get('delta', {})
                                        content += delta.get('content', '')
                                except Exception:
                                    pass
                elif encoding == 'deflate':
                    import zlib
                    raw = zlib.decompress(resp.read())
                    try:
                        result = json.loads(raw.decode('utf-8'))
                        if 'choices' in result:
                            content = result['choices'][0].get('message', {}).get('content', '')
                    except (json.JSONDecodeError, KeyError):
                        for line in raw.decode('utf-8').split('\n'):
                            if line.startswith('data: '):
                                try:
                                    chunk = json.loads(line[6:])
                                    if 'choices' in chunk:
                                        delta = chunk['choices'][0].get('delta', {})
                                        content += delta.get('content', '')
                                except Exception:
                                    pass
                else:
                    # 标准 SSE 流式响应
                    for line in resp:
                        line = line.decode('utf-8').strip()
                        if line.startswith('data: '):
                            try:
                                chunk = json.loads(line[6:])
                                if 'choices' in chunk:
                                    delta = chunk['choices'][0].get('delta', {})
                                    content += delta.get('content', '')
                            except Exception:
                                pass

            if not content:
                logger.error("LLM 请求未返回内容")

            if content and use_cache:
                self._set_cache(key, content)

            return content if content else None
        except Exception as e:
            return None

    def expand_query(self, query: str) -> List[str]:
        """查询扩展（优化：更精准的扩展词生成）"""
        prompt = f"""请为以下查询生成3个语义相关的搜索词，用于记忆检索系统。

要求：
1. 保持原查询的核心意图
2. 使用同义词或相关概念
3. 每行一个，不要编号

查询: {query}

搜索词:"""
        result = self.chat(prompt, max_tokens=150, temperature=0.5)
        if result:
            expansions = [line.strip() for line in result.split('\n') if line.strip() and len(line.strip()) > 2][:5]
            return expansions
        return [query]

    def rerank(self, query: str, results: List[dict]) -> List[dict]:
        """重排序"""
        if len(results) <= 1:
            return results

        text = "\n".join([f"{i+1}. [{r['type']}] {r['content'][:60]}..." for i, r in enumerate(results[:8])])
        prompt = f"根据查询'{query}'对以下结果排序，返回编号列表（逗号分隔）：\n{text}"

        result = self.chat(prompt, max_tokens=50, temperature=0.1)
        if result:
            try:
                order = [int(x.strip()) - 1 for x in result.split(',') if x.strip().isdigit()]
                if order and max(order) < len(results):
                    return [results[i] for i in order if i < len(results)]
            except Exception:
                pass
            logger.error("重排序失败")

        return results
