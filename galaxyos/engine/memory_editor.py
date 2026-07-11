#!/usr/bin/env python3
"""
记忆编辑引擎 (Memory Editing)

核心功能:
1. 选择性遗忘 — 基于重要性/时效性修剪低价值记忆
2. 记忆合并 — 将同主题多条记忆合并为一条综合记忆（减少冗余）
3. 冲突检测 — 检测同一主题的不同记忆矛盾版本
4. 重要性重排 — 根据访问频率和新反馈调整重要性

设计:
- 不直接修改已有的存储器（XiaoyiMemoryV2/向量库）
- 通过生成"编辑建议"JSON 供外部消费
- 持久化到 .learnings/memory_edits.jsonl
"""

import json
import os
import sys
import time
import re
import math
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from collections import defaultdict
from galaxyos.shared.paths import workspace

logger = logging.getLogger(__name__)

EDIT_PATH = os.path.join(
    os.environ.get("WORKSPACE", workspace()),
    ".learnings", "memory_edits.jsonl"
)
MERGED_PATH = os.path.join(
    os.environ.get("WORKSPACE", workspace()),
    ".learnings", "merged_memories.jsonl"
)


class MemoryEditor:
    """记忆编辑引擎"""

    def __init__(self, llm_flash=None):
        self.llm_flash = llm_flash

    # ─────────────────── 对外接口 ───────────────────

    def amend_memory(self, old_id: str, old_content: str, new_content: str) -> Dict:
        """
        ROME 风格记忆修正: 创建修订版, 标记原版为 superseded

        增强版: 如果没有提供 old_id 或为 'auto', 自动调用 locate() 找到旧记忆

        不删除/覆盖, 而是:
        1. 创建新节点 (revision version)
        2. 旧节点标记 status: superseded
        3. 检索时 superseded 的节点加权降级 (-0.3 score)

        Returns: {"ok": bool, "amended_id": str, "superseded_id": str}
        """
        # 增强: 如果没有提供 old_id, 自动尝试 locate
        if not old_id or old_id == 'auto':
            try:
                located = self.locate(old_content, new_content)
                if located.get('located_id'):
                    old_id = located['located_id']
                    old_content = located.get('located_content', old_content)
                    logger.info(f"amend: locate 自动找到候选记忆 {old_id}, "
                                f"cosine_similarity={located.get('cosine_similarity', 'N/A')}")
            except Exception as e:
                logger.warning(f"amend: locate 失败，使用传入 id: {e}")

        amended = {
            "id": f"{old_id}_v{int(time.time())}",
            "content": new_content,
            "source": "memory_amend",
            "supersedes": old_id,
            "importance": 0.8,
            "status": "active",
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "old_content": old_content[:200],
        }
        # 保存修订记录
        try:
            os.makedirs(os.path.dirname(EDIT_PATH), exist_ok=True)
            with open(EDIT_PATH, "a") as f:
                f.write(json.dumps(amended, ensure_ascii=False) + "\n")
            return {"ok": True, "amended_id": amended["id"], "superseded_id": old_id}
        except Exception as e:
            logger.warning(f"Amend memory failed: {e}")
            return {"ok": False, "error": str(e)}

    def forget_low_importance(self, memories: List[Dict], threshold: float = 0.2) -> List[Dict]:
        """
        艾宾浩斯遗忘曲线驱动的选择性遗忘

        每条记忆维护衰减参数 S（记忆稳定性）：
        - 初始 S = S0 + importance × k  （重要记忆天然衰减慢）
        - 每次检索命中（hit_count）时 S 增长（复习强化效应）
        - 遗忘分 = 1 - e^(-age_days / S)  （指数衰减）

        Args:
            memories: 记忆列表，每个需有 'id', 'importance' (0-1) 字段
            threshold: 重要性阈值（低于此值建议遗忘）

        Returns: 建议遗忘的记忆 ID 列表

        参数说明（内部常量）：
            S0 = 1.5 天   — 基线稳定性（全新普通记忆 1.5 天后 ~37% 留存）
            k = 10       — 重要性→稳定性乘数（imp=1.0 时 S ≈ 11.5 天）
            delta = 0.2  — 每次检索 S 增长 20%（复习强化）
            forget_threshold = 0.85 — 遗忘分 > 85% 时建议删除
        """
        S0 = 1.5          # 基线稳定性（天），全新记忆 ~37% 留存耗时
        k = 10            # 重要性→稳定性乘数
        delta = 0.2       # 每次检索 S 增长系数
        forget_threshold = 0.85  # 遗忘分阈值

        to_forget = []
        for mem in memories:
            imp = mem.get("importance", 0.5)

            # ─── 计算记忆稳定性 S ───
            hit_count = mem.get("hit_count", mem.get("access_count", 0))
            existing_S = mem.get("stability", 0)
            if existing_S > 0:
                S = existing_S
            else:
                # 从重要性推算初始稳定性
                S = S0 + imp * k
            # 检索效应：每次命中增加稳定性（复习使记忆更强）
            if hit_count > 0:
                S *= (1 + delta) ** hit_count

            # ─── 计算时间衰减 ───
            age_days = -1
            ts = mem.get("timestamp", mem.get("created_at", ""))
            if ts:
                try:
                    if isinstance(ts, str):
                        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                    else:
                        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                    age_days = (datetime.now(timezone.utc) - dt).days
                except Exception:
                    pass

            if age_days >= 0:
                # 艾宾浩斯指数衰减：R = e^(-t/S)
                retention = math.exp(-age_days / S)
                forget_score = 1 - retention
            else:
                retention = 1.0
                forget_score = 0.3  # 无时间戳时保守

            logger.debug(
                f"forget: imp={imp:.2f} S={S:.1f}d age={age_days}d "
                f"retention={retention:.3f} forget={forget_score:.3f}"
            )

            if forget_score > forget_threshold or imp < threshold:
                to_forget.append(mem.get("id", ""))

        # 标记 superseded 的记忆也加入（供 generative_replay 消费）
        for mem in memories:
            if mem.get('status') == 'superseded' and mem.get('id'):
                to_forget.append(mem.get('id'))

        return [fid for fid in to_forget if fid]

    def merge_similar(self, memories: List[Dict], query: str = "", max_merged: int = 5) -> List[Dict]:
        """
        记忆合并: 将同主题记忆合并

        Args:
            memories: 记忆列表
            query: 当前查询（可选，用于聚类）
            max_merged: 最多合并条数

        Returns: 合并后的记忆列表
        """
        if len(memories) < 3:
            return memories

        # 关键词分组
        groups = defaultdict(list)
        q_words = set(re.findall(r'[\w\u4e00-\u9fff]+', query.lower())) if query else set()

        for mem in memories:
            content = (mem.get("content", "") or str(mem)).lower()[:200]
            words = set(re.findall(r'[\w\u4e00-\u9fff]+', content))
            overlap = len(words & q_words) if q_words else 0
            key_word = list(words - q_words)[:3] if not q_words else list(words)[:3]
            key = "_".join(sorted(key_word[:3])) if key_word else "general"
            groups[key].append(mem)

        merged_list = []
        for key, group in groups.items():
            if len(group) >= 2:
                merged = self._merge_group(group)
                if merged:
                    merged_list.append(merged)
            else:
                merged_list.extend(group)

        return merged_list[:max_merged]

    def detect_conflicts(self, memories: List[Dict]) -> List[Dict]:
        """
        冲突检测: 找出同一主题的矛盾记忆

        Returns: [{topic, conflicting_ids, descriptions}]
        """
        if len(memories) < 2:
            return []

        # 关键词聚类后检测矛盾
        clusters = defaultdict(list)
        for mem in memories:
            content = (mem.get("content", "") or str(mem))[:200]
            words = set(re.findall(r'[\w\u4e00-\u9fff]+', content.lower()))
            # 取主要名词为键
            key = " ".join(sorted(list(words)[:3]))
            clusters[key].append({"id": mem.get("id", ""), "content": content})

        conflicts = []
        for key, group in clusters.items():
            if len(group) < 2:
                continue
            if self.llm_flash:
                conflict = self._detect_conflict_llm(key, group)
                if conflict:
                    conflicts.append(conflict)

        return conflicts

    def reorder_importance(self, memories: List[Dict], feedback: str = "") -> List[Dict]:
        """
        重要性重排: 根据反馈调整记忆重要性

        Returns: 更新后的记忆列表（带新 importance）
        """
        if not self.llm_flash or not feedback:
            return memories

        f_lower = feedback.lower()
        f_words = set(re.findall(r'[\w\u4e00-\u9fff]+', f_lower))

        updated = []
        for mem in memories:
            content = (mem.get("content", "") or str(mem)).lower()[:200]
            mem_words = set(re.findall(r'[\w\u4e00-\u9fff]+', content))
            overlap = len(mem_words & f_words)

            imp = mem.get("importance", 0.5)
            if overlap >= 2:
                mem["importance"] = min(1.0, imp + 0.1 * overlap)
                mem["reorder_reason"] = "positive_feedback_match"
            elif overlap > 0 and "不" in feedback or "错" in feedback:
                mem["importance"] = max(0.1, imp - 0.05)
                mem["reorder_reason"] = "negative_feedback_match"

            updated.append(mem)

        return updated

    # ─────────────────── 内部 ───────────────────

    def _merge_group(self, group: List[Dict]) -> Optional[Dict]:
        """用 Flash 合并一组记忆"""
        if not self.llm_flash:
            # 无 Flash 时取最新一条
            return group[0]

        parts = "\n".join(
            f"- {m.get('content', '')[:200]}"
            for m in group[:5]
        )
        prompt = (
            f"整合以下多条相关信息，合并为一条精炼的综合描述。\n\n"
            f"原始信息:\n{parts[:2000]}\n\n"
            f"整合后的综合描述（保留所有关键事实）:"
        )
        try:
            rsp = self.llm_flash.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500, temperature=0.2,
            )
            merged_content = rsp.choices[0].message.content.strip()
            if merged_content and len(merged_content) > 20:
                merged = {
                    "id": f"merged_{int(time.time())}",
                    "content": merged_content,
                    "source": "memory_merge",
                    "merged_from": len(group),
                    "importance": max(m.get("importance", 0.5) for m in group),
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                self._save_merged(merged)
                return merged
        except Exception as e:
            logger.warning(f"合并失败: {e}")

        return group[0]

    def _detect_conflict_llm(self, key: str, group: List[Dict]) -> Optional[Dict]:
        """用 Flash 检测矛盾"""
        parts = "\n".join(
            f"[记忆 {i+1}] {m['content'][:200]}"
            for i, m in enumerate(group[:4])
        )
        prompt = (
            f"检测以下多条记忆中是否存在事实矛盾（即同一件事的描述不一致）:\n\n"
            f"{parts[:2000]}\n\n"
            f"如有矛盾，返回: {{\"has_conflict\": true, \"topic\": \"矛盾主题\", "
            f"\"details\": \"矛盾描述\", \"conflicting_ids\": [索引号(1-based)]}}\n"
            f"如无矛盾: {{\"has_conflict\": false}}"
        )
        try:
            rsp = self.llm_flash.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=300, temperature=0.1,
            )
            text = rsp.choices[0].message.content.strip()
            jm = re.search(r'\{[^}]+\}', text)
            if jm:
                data = json.loads(jm.group())
                if data.get("has_conflict"):
                    return {
                        "topic": data.get("topic", key),
                        "conflicting_ids": [group[i-1]["id"] for i in data.get("conflicting_ids", [])
                                            if 0 < i <= len(group)],
                        "details": data.get("details", ""),
                    }
        except Exception as e:
            logger.warning(f"冲突检测失败: {e}")

        return None

    # ─────────────────── ROME 风格 Locate ───────────────────

    def locate(self, old_content: str, new_content: str) -> Dict:
        """
        ROME 风格 locate 阶段: 在向量记忆库中找到最匹配的旧记忆

        Args:
            old_content: 旧内容（用于生成查询向量）
            new_content: 新内容（用于对比确认）

        Returns:
            {"located_id": str, "located_content": str,
             "cosine_similarity": float}
        """
        result = {
            "located_id": "",
            "located_content": "",
            "cosine_similarity": 0.0,
        }
        query_vector = None
        try:
            sys.path.insert(0, os.path.dirname(__file__))
            from xiaoyi_claw_api import get_xiaoyi_claw as _get_xiayi
            inst = _get_xiayi()
            if inst and hasattr(inst, 'embedding') and inst.embedding:
                resp = inst.embedding.embeddings.create(
                    input=[old_content],
                    model=getattr(inst, 'embedding_model', 'bge-m3'),
                )
                query_vector = resp.data[0].embedding
        except Exception as e:
            logger.warning(f"locate: embedding client 不可用: {e}")
        candidates = []
        try:
            ws = os.environ.get("WORKSPACE", workspace())
            sys.path.insert(0, os.path.join(ws,
                "skills/xiaoyi-claw-omega-final/skills/llm-memory-integration/core"))
            from unified_vector_store import get_vector_store
            store = get_vector_store()
            if query_vector and store:
                results = store.search(query_vector=query_vector, top_k=5)
                for r in results:
                    candidates.append({
                        'id': r.get('id', ''),
                        'content': r.get('content', ''),
                        'score': r.get('score', 0),
                    })
        except Exception as e:
            logger.warning(f"locate: 向量搜索失败: {e}")
        if not candidates:
            try:
                dag_db = os.path.expanduser("~/.openclaw/dag_context.db")
                if os.path.exists(dag_db):
                    import sqlite3
                    conn = sqlite3.connect(dag_db)
                    rows = conn.execute(
                        "SELECT content, node_id FROM rccam_nodes "
                        "WHERE content IS NOT NULL AND content != '' "
                        "ORDER BY timestamp DESC LIMIT 200"
                    ).fetchall()
                    conn.close()
                    q_words = set(re.findall(r'[\w\u4e00-\u9fff]+', old_content.lower()))
                    for c, nid in rows:
                        c = c or ''
                        c_words = set(re.findall(r'[\w\u4e00-\u9fff]+', c.lower()))
                        overlap = len(q_words & c_words)
                        if overlap > 0:
                            score = overlap / max(len(q_words), 1)
                            candidates.append({
                                'id': nid or c[:50],
                                'content': c[:1500],
                                'score': score,
                            })
                    candidates.sort(key=lambda x: -x['score'])
                    candidates = candidates[:5]
            except Exception as e:
                logger.warning(f"locate: 文本兜底失败: {e}")
        if candidates:
            best = candidates[0]
            result['located_id'] = best['id']
            result['located_content'] = best['content'][:200]
            result['cosine_similarity'] = round(float(best['score']), 4)
        return result

    # ─────────────────── Generative Replay ───────────────────

    def generative_replay(self, memories: List[Dict]) -> Dict:
        """
        Generative Replay: 对 superseded/低分记忆做 LLM 摘要重写

        Args:
            memories: 记忆列表

        Returns:
            {"replayed": int, "summary": str}
        """
        replay_candidates = []
        for mem in memories:
            content = mem.get('content', '') or str(mem)
            imp = mem.get('importance', 0.5)
            status = mem.get('status', 'active')
            if status == 'superseded' or imp < 0.3:
                replay_candidates.append({
                    'id': mem.get('id', ''),
                    'content': content[:500],
                    'importance': imp,
                    'status': status,
                })
        if not replay_candidates:
            return {"replayed": 0, "summary": ""}
        batch = replay_candidates[:10]
        parts = "\n---\n".join(
            f"[记忆 {i+1}] 重要性={m['importance']:.2f} 状态={m['status']}\n内容: {m['content']}"
            for i, m in enumerate(batch)
        )
        summary_text = ""
        replayed_count = 0
        if self.llm_flash:
            prompt = (
                f"以下是一批 superseded 或低重要性（<0.3）的记忆。请：\n"
                f"1. 将它们的内容压缩为一条精炼的综合摘要，保留所有关键事实\n"
                f"2. 评估综合摘要的'重要性'（0~1，基于内容的重要程度）\n"
                f"3. 以 JSON 格式返回："
                f"{{\"summary\": \"综合摘要\", \"new_importance\": 0.x}}\n\n"
                f"{parts[:3000]}"
            )
            try:
                rsp = self.llm_flash.chat.completions.create(
                    model="deepseek-v4-flash",
                    messages=[{"role": "user", "content": prompt}],
                    max_tokens=800,
                    temperature=0.3,
                )
                text = rsp.choices[0].message.content.strip()
                jm = re.search(r'\{[^}]+\}', text)
                if jm:
                    data = json.loads(jm.group())
                    summary_text = data.get('summary', text)[:1000]
                    new_imp = float(data.get('new_importance', 0.5))
                    replayed_count = len(batch)
                    replay_record = {
                        "id": f"replay_{int(time.time())}",
                        "content": summary_text,
                        "source": "generative_replay",
                        "replayed_from": replayed_count,
                        "importance": new_imp,
                        "status": "active",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    try:
                        os.makedirs(os.path.dirname(EDIT_PATH), exist_ok=True)
                        with open(EDIT_PATH, "a") as f:
                            f.write(json.dumps(replay_record, ensure_ascii=False) + "\n")
                    except Exception as e:
                        logger.warning(f"保存 replay 记录失败: {e}")
            except Exception as e:
                logger.warning(f"generative_replay LLM 调用失败: {e}")
        if not summary_text:
            contents = [m['content'][:200] for m in batch]
            summary_text = "；".join(contents)[:1000]
            replayed_count = len(batch)
        return {
            "replayed": replayed_count,
            "summary": summary_text[:1000],
        }

    # ─────────────────── 内部 ───────────────────

    def _save_merged(self, merged: Dict):
        """持久化合并记录"""

        try:
            os.makedirs(os.path.dirname(MERGED_PATH), exist_ok=True)
            with open(MERGED_PATH, "a") as f:
                f.write(json.dumps(merged, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"保存合并记录失败: {e}")


# ── 全局实例 ──
_instance = None

def get_memory_editor(llm_flash=None) -> MemoryEditor:
    global _instance
    if _instance is None:
        _instance = MemoryEditor(llm_flash)
    elif llm_flash and _instance.llm_flash is None:
        _instance.llm_flash = llm_flash
    return _instance


if __name__ == "__main__":
    me = MemoryEditor()
    print("MemoryEditor 加载成功 (遗忘/合并/冲突检测/重排/locate/replay)")
