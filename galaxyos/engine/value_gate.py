"""
Value Gate — 注入价值评估器

LLM 回复后，检测回复中是否使用了 injection 中的信息。
反馈给 Impact Tracker 更新"模块产出利用率"指标。

架构定位:
  JS 侧在收到 LLM 回复后调用 Worker 的 value_gate.analyze()
"""

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger("galaxyos.valuegate")


class ValueGate:
    """
    注入价值评估器。

    用法:
      gate = ValueGate()
      results = gate.analyze(query, injection, response)
      # → [{module_id, field, usage_rate}, ...]
    """

    def __init__(self):
        pass

    def analyze(
        self,
        injection: str,
        response: str,
    ) -> Dict[str, float]:
        """
        分析 LLM 回复中对 injection 中信息的引用程度。

        Args:
            injection: context_assemble 拼出的 injection 文本
            response: LLM 的实际回复文本

        Returns:
            {module_key: usage_rate} 
            module_key 形如 "ssm:记忆预测", "memoryos:长期画像"
        """
        if not injection or not response:
            return {}

        results: Dict[str, float] = {}
        response_lower = response.lower()

        # 按 section 拆分 injection
        sections = self._parse_injection_sections(injection)

        for key, text in sections.items():
            if not text:
                continue

            # 提取核心实体和术语
            entities = self._extract_entities(text)
            if not entities:
                continue

            # 检查回复中是否提到了这些实体
            mentioned = 0
            for entity in entities:
                entity_lower = entity.lower()
                # 实体匹配（允许部分重叠）
                if entity_lower in response_lower:
                    mentioned += 1
                else:
                    # 尝试模糊匹配（词级重叠）
                    e_words = set(entity_lower.split())
                    r_words = set(response_lower.split())
                    overlap = e_words & r_words
                    if len(overlap) >= min(2, len(e_words)):
                        mentioned += 1

            usage = mentioned / max(len(entities), 1)
            results[key] = round(usage, 3)

        return results

    # ─── 提取 injection section ────────────────────────

    _SECTION_PATTERN = re.compile(r'\[([^\]]+)\]\s*(.*?)(?=\n\[|$)')

    def _parse_injection_sections(self, injection: str) -> Dict[str, str]:
        """
        从 injection 文本中解析出各 section。

        输入示例:
          [检索增强] ... 
          [分层记忆] ...
          [记忆预测] 下一步可能需要: foo(0.8), bar(0.6)

        返回:
          {"检索增强": "...", "分层记忆": "...", "ssp:记忆预测": "..."}
        """
        sections: Dict[str, str] = {}

        # 先尝试按 [xxx] 分组
        matches = self._SECTION_PATTERN.findall(injection)
        if matches:
            for tag, content in matches:
                tag_stripped = tag.strip()
                content_stripped = content.strip()
                # 映射到 module key
                module_key = self._tag_to_module_key(tag_stripped)
                if module_key:
                    sections[module_key] = content_stripped
        else:
            # 没有 [xxx] 标记 → 整段文本算"注入"
            if injection.strip():
                sections["_raw_injection"] = injection.strip()

        return sections

    @staticmethod
    def _tag_to_module_key(tag: str) -> Optional[str]:
        """[xxx] 标签 → module_key"""
        mapping = {
            "检索增强": "retrieval",
            "分层记忆": "memgpt",
            "空间场景": "arigraph",
            "记忆预测": "ssm",
            "长期画像": "memoryos",
            "跨会话画像": "memoryos_cross",
            "行为模式": "kora",
            "多路推理": "thinking",
            "历史摘要": "raptor",
            "cove修正": "cove",
            "场景": "arigraph",
            "代码分析": "code_aware",
            "无损上下文还原": "blob",
        }
        return mapping.get(tag)

    # ─── 实体提取 ──────────────────────────────────────

    @staticmethod
    def _extract_entities(text: str) -> List[str]:
        """从文本中提取可被追踪的实体/关键词"""
        entities = []

        # 提取引号中的内容
        quoted = re.findall(r'"([^"]+)"', text)
        entities.extend(q for q in quoted if len(q) > 3)

        # 提取括号中的内容
        paren = re.findall(r'\(([^)]+)\)', text)
        entities.extend(p for p in paren if len(p) > 3)

        # 提取冒号后的较长内容（排除标记）
        colon = re.findall(r':\s*(.{10,60})', text)
        entities.extend(c.strip() for c in colon if len(c.strip()) > 5)

        # 去除过短和过长的
        entities = [e for e in entities if 3 < len(e) < 200]
        # 去重
        seen = set()
        unique = []
        for e in entities:
            if e.lower() not in seen:
                seen.add(e.lower())
                unique.append(e)

        # 如果没有提取到，返回整个文本的句子片段
        if not unique:
            sentences = re.split(r'[。！？\n]', text)
            for s in sentences:
                s = s.strip()
                if 10 < len(s) < 200:
                    unique.append(s)

        return unique[:10]  # 最多 10 个
