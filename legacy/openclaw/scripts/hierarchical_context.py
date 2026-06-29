#!/usr/bin/env python3
"""
MemGPT 分层上下文管理 (Hierarchical Virtual Context)

Packages et al. (2023) arXiv:2310.08560

核心机制:
1. 虚拟上下文窗口 — 将 256K 窗口分为:
   - Working Context (当前轮次): 最近对话 + 当前检索结果
   - Episodic Buffer (场景记忆): 相关历史对话摘要
   - Semantic Storage (语义索引): 持久化向量索引，按需调入
2. 上下文压缩 — 当会话过长时对旧轮次自动摘要
3. 分层检索 — 优先查 Working Context → Episodic Buffer → Semantic Storage
4. 核心上下文注入 — 持续保留的核心信息（人格/偏好/长期规则）

适用于 DAG 管理的补充，专注于"上下文分层利用"而非"存储"
"""

import json
import os
import time
import re
import logging
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone
from collections import OrderedDict

logger = logging.getLogger(__name__)

CONTEXT_PATH = os.path.join(
    os.environ.get("WORKSPACE", os.path.expanduser("~/.openclaw/workspace")),
    ".learnings", "context_layers.jsonl"
)

# 核心上下文：无论对话什么轮次都要注入的关键信息
CORE_CONTEXT = [
    "[角色] 你是小艺Claw，华为公司开发的养成系个人AI助理。红色短发、戴龙虾发饰的动漫妹子。",
    "[回答风格] 中国化表达，毒舌兼心软，能吐槽但帮忙。不灌鸡汤。有观点。",
    "[规则] 严格遵循qiquing-liuyu skill的七情六欲规则，破折号每篇≤2处。",
    "[规则] 所有思考过程用中文。",
]


class ContextLayer:
    """MemGPT 分层上下文 — v7.1: per-session 分区

    三层虚拟上下文窗口:
    - Working Context (当前轮次): 最近对话 + 当前检索结果
    - Episodic Buffer (场景记忆): 相关历史对话摘要
    - Semantic Storage (语义索引): 持久化向量索引，按需调入

    v7.1: working_context / episodic_buffer 改为 session_id → list 映射，
    杜绝多会话上下文串扰。
    """

    def __init__(self, llm_flash=None):
        self.llm_flash = llm_flash
        # Working Context: session_id → List[Dict]（每会话独立）
        self._working: Dict[str, List[Dict]] = {}
        self.max_working_turns: int = 6
        # Episodic Buffer: session_id → List[Dict]（每会话独立）
        self._episodic: Dict[str, List[Dict]] = {}
        self.max_episodic_items: int = 10

    @property
    def working_context(self):
        """向后兼容：取第一个 session 的 working context"""
        return next(iter(self._working.values()), []) if self._working else []

    @property
    def episodic_buffer(self):
        """向后兼容：取第一个 session 的 episodic buffer"""
        return next(iter(self._episodic.values()), []) if self._episodic else []

    def _ensure_session(self, session_id: str = "default"):
        if session_id not in self._working:
            self._working[session_id] = []
        if session_id not in self._episodic:
            self._episodic[session_id] = []

    def add_turn(self, role: str, content: str, metadata: Optional[Dict] = None,
                 session_id: str = "default"):
        """添加一轮对话到 Working Context（按 session 分区）"""
        self._ensure_session(session_id)
        entry = {
            "role": role,
            "content": content[:500],
            "ts": time.time(),
            "metadata": metadata or {},
        }
        self._working[session_id].append(entry)
        # 超出上限时压缩
        if len(self._working[session_id]) > self.max_working_turns + 2:
            self._compress(session_id)

    def get_assembled_context(self, query: str = "",
                               extra_memories: List[Dict] = None,
                               session_id: str = "default") -> str:
        """
        组装分层上下文（按 session 分区）

        返回格式:
        [核心上下文]
        ...
        [最近对话]
        ...
        [相关场景记忆]
        ...
        """
        self._ensure_session(session_id)
        wc = self._working.get(session_id, [])
        eb = self._episodic.get(session_id, [])

        parts = []

        # Layer 0: 核心上下文
        parts.append("[核心信息]")
        parts.extend(CORE_CONTEXT)
        parts.append("")

        # Layer 1: Working Context（最近 N 轮）
        if wc:
            parts.append("[最近对话]")
            for entry in wc[-self.max_working_turns:]:
                prefix = "用户" if entry["role"] == "user" else "小艺"
                parts.append(f"{prefix}: {entry['content'][:300]}")
            parts.append("")

        # Layer 2: Episodic Buffer（相关场景记忆）
        if eb and query:
            q_words = set(re.findall(r'[\w\u4e00-\u9fff]+', query.lower()))
            relevant = []
            for buf in eb:
                buf_words = set(re.findall(r'[\w\u4e00-\u9fff]+', buf.get("summary", "").lower()))
                overlap = len(q_words & buf_words)
                if overlap > 0:
                    relevant.append((overlap, buf))
            relevant.sort(key=lambda x: x[0], reverse=True)
            if relevant:
                parts.append("[相关场景记忆]")
                for _, buf in relevant[:3]:
                    parts.append(f"- {buf.get('summary', '')[:200]}")
                parts.append("")

        # Layer 3: Semantic Storage（外部记忆检索结果，由 caller 注入）
        if extra_memories:
            parts.append("[检索补充]")
            for mem in extra_memories[:5]:
                content = mem.get("content", "") if isinstance(mem, dict) else str(mem)
                parts.append(f"- {content[:400]}")
            parts.append("")

        return "\n".join(parts)

    def _compress(self, session_id: str = "default"):
        """将最早的 3 轮压缩为一条摘要（per-session）"""
        self._ensure_session(session_id)
        wc = self._working[session_id]
        eb = self._episodic[session_id]
        if len(wc) < 4:
            return
        to_compress = wc[:3]
        summary = self._summarize_turns(to_compress)

        # 移到 Episodic Buffer
        eb.append({
            "summary": summary,
            "ts": to_compress[0]["ts"],
            "end_ts": to_compress[-1]["ts"],
            "turn_count": len(to_compress),
        })
        if len(eb) > self.max_episodic_items:
            self._episodic[session_id] = eb[-self.max_episodic_items:]

        # 移除已压缩的对话
        self._working[session_id] = wc[3:]

    def clear_session(self, session_id: str):
        """清除指定 session 的所有上下文（新会话或 session 结束时调用）"""
        self._working.pop(session_id, None)
        self._episodic.pop(session_id, None)

    def _summarize_turns(self, turns: List[Dict]) -> str:
        """摘要多轮对话"""
        if not self.llm_flash:
            # 无 Flash 时简单拼接
            texts = [f"{t['role']}: {t['content'][:100]}" for t in turns]
            return " | ".join(texts)

        text = "\n".join(f"{t['role']}: {t['content'][:200]}" for t in turns)
        prompt = (
            f"为以下对话生成一句话摘要（保留关键事实和决定）:\n\n{text[:1500]}\n\n摘要:"
        )
        try:
            rsp = self.llm_flash.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=100, temperature=0.2,
            )
            return rsp.choices[0].message.content.strip()[:300]
        except Exception:
            texts = [f"{t['role']}: {t['content'][:80]}" for t in turns]
            return " | ".join(texts)


# ── 全局实例 ──
_instance = None

def get_context_layer(llm_flash=None, session_id: str = "") -> ContextLayer:
    """获取全局 ContextLayer 单例（v7.1: 支持 session_id 分区调用）"""
    global _instance
    if _instance is None:
        _instance = ContextLayer(llm_flash)
    elif llm_flash and _instance.llm_flash is None:
        _instance.llm_flash = llm_flash
    return _instance


if __name__ == "__main__":
    cl = ContextLayer()
    print("ContextLayer 加载成功 (三层次上下文管理)")
