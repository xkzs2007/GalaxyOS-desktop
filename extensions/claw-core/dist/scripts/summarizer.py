"""结果摘要生成"""
import os
from typing import List, Dict, Optional
import urllib.request
import gzip
import json
import logging
logger = logging.getLogger(__name__)

# 默认配置
DEFAULT_LLM_API_URL = os.environ.get("LLM_API_URL", "http://localhost:8000/v1/chat/completions")
DEFAULT_LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
DEFAULT_LLM_UID = os.environ.get("LLM_UID", "default")


class ResultSummarizer:
    def __init__(
        self,
        url: str = None,
        key: str = None,
        uid: str = None,
        model: str = "LLM_GLM5"
    ):
        self.url = url or DEFAULT_LLM_API_URL
        self.key = key or DEFAULT_LLM_API_KEY
        self.uid = uid or DEFAULT_LLM_UID
        self.model = model

    def summarize(self, query: str, results: List[Dict], max_length: int = 200) -> Optional[str]:
        """生成结果摘要"""
        if not results:
            return None

        # 构建内容摘要
        contents = []
        for i, r in enumerate(results[:5], 1):
            content = r.get("content", "")[:150]
            contents.append(f"{i}. {content}")

        prompt = f"""请为以下搜索结果生成一个简洁摘要（{max_length}字以内）：

查询: {query}

结果:
{chr(10).join(contents)}

摘要:"""

        data = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 150,
            "temperature": 0.3,
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
            with urllib.request.urlopen(req, timeout=20) as resp:
                encoding = resp.headers.get('Content-Encoding', '')
                if encoding in ('gzip', 'deflate'):
                    raw = resp.read()
                    if encoding == 'gzip':
                        raw = gzip.decompress(raw)
                    else:
                        import zlib
                        raw = zlib.decompress(raw)
                    for line in raw.decode('utf-8').split('\n'):
                        line = line.strip()
                        if line.startswith('data: '):
                            try:
                                chunk = json.loads(line[6:])
                                if 'choices' in chunk:
                                    delta = chunk['choices'][0].get('delta', {})
                                    content += delta.get('content', '')
                            except Exception:
                                pass
                else:
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
                logger.warning("摘要生成未返回内容")

            return content[:max_length] if content else None
        except Exception as e:
            return None

    @staticmethod
    def quick_summary(results: List[Dict]) -> str:
        """快速摘要（无LLM）"""
        if not results:
            return "未找到相关记忆"

        types = {}
        for r in results:
            t = r.get("type", "unknown")
            types[t] = types.get(t, 0) + 1

        type_str = ", ".join([f"{t}({c}条)" for t, c in types.items()])

        return f"找到{len(results)}条记忆: {type_str}"
