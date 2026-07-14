"""
四篇论文方向实现 — 一次性整合到 Worker 后台
RAPTOR(2401.18059) + GraphRAG(2404.16130) + Generative Agents(2304.03442) + Toolformer(2302.04761)

用法：由 claw_worker.py 在启动时自动加载
"""

import os
import json
import sqlite3
import logging
import time
import threading
import re
import hashlib
from datetime import datetime
from collections import defaultdict
from typing import Optional, List, Dict, Any
from galaxyos.shared.paths import workspace

logger = logging.getLogger(__name__)

WORKSPACE = os.environ.get('OPENCLAW_WORKSPACE', workspace())
DAG_DB = os.path.expanduser("~/.openclaw/dag_context.db")

# 全局 Flash 客户端（共享）
_flash_client = None

def _get_flash():
    """获取 DeepSeek Flash API 客户端（共享单例）"""
    global _flash_client
    if _flash_client:
        return _flash_client

    try:
        from openai import OpenAI
        cfg_path = os.path.join(WORKSPACE, 'skills', 'galaxyos-engine',
                                'skills', 'llm-memory-integration', 'config', 'llm_config.json')
        if os.path.exists(cfg_path):
            with open(cfg_path) as f:
                cfg = json.load(f)
            llm_cfg = cfg.get('llm', {})
            api_key = llm_cfg.get('api_key', '') or os.environ.get('DEEPSEEK_API_KEY', '')
            base_url = llm_cfg.get('base_url', 'https://api.deepseek.com')
            _flash_client = OpenAI(api_key=api_key, base_url=base_url)
            return _flash_client
    except Exception as e:
        logger.warning(f"Flash init failed: {e}")
    return None

def _flash_chat(prompt: str, max_tokens: int = 300) -> str:
    """调 Flash 对话"""
    client = _get_flash()
    if not client:
        return ""
    try:
        resp = client.chat.completions.create(
            model='deepseek-chat',
            messages=[{'role': 'user', 'content': prompt}],
            max_tokens=max_tokens
        )
        return resp.choices[0].message.content or ""
    except Exception as e:
        logger.warning(f"Flash call error: {e}")
        return ""


# ===== 1. RAPTOR — 分层摘要树 =====
class RAPTOREngine:
    """arXiv:2401.18059 — 将 DAG 节点聚类成多层摘要树"""

    def __init__(self):
        self._tree_built = False
        self._summaries = {}
        self._lock = threading.Lock()

    def build_tree(self) -> dict:
        """扫描 DAG 库，建摘要树"""
        if not os.path.exists(DAG_DB):
            return {"status": "skipped", "reason": "no dag db"}

        with self._lock:
            try:
                conn = sqlite3.connect(DAG_DB)
                cursor = conn.cursor()
                cursor.execute("SELECT node_id, content, importance_score FROM dag_nodes ORDER BY timestamp DESC LIMIT 200")
                rows = cursor.fetchall()
                conn.close()

                if not rows:
                    return {"status": "skipped", "reason": "empty"}

                # 按 importance 分组聚类
                clusters = defaultdict(list)
                for nid, content, importance in rows:
                    imp_level = 'high' if (importance or 0) > 0.7 else 'medium' if (importance or 0) > 0.4 else 'low'
                    clusters[imp_level].append((nid, content))

                for level, nodes in clusters.items():
                    texts = [c for _, c in nodes if c]
                    if not texts:
                        continue
                    batch_text = "\n---\n".join(texts[:10])
                    summary = _flash_chat(f"用中文概括以下内容的核心主题，限制100字内：\n{batch_text[:2000]}")
                    if summary:
                        self._summaries[level] = summary
                    else:
                        self._summaries[level] = f"[RAPTOR-{level}] {len(nodes)}个节点"

                self._tree_built = True
                return {"status": "ok", "clusters": {k: len(v) for k, v in clusters.items()}, "summaries": list(self._summaries.keys())}
            except Exception as e:
                logger.warning(f"RAPTOR build error: {e}")
                return {"status": "error", "reason": str(e)}

# ===== 2. GraphRAG — 自动实体/社区提取 =====
class GraphRAGEngine:
    """arXiv:2404.16130 — 从记忆/文档中自动提取实体关系并检测社区"""

    def __init__(self):
        self._nlp = None
        self._entities = {}
        self._lock = threading.Lock()

    def _lazy_nlp(self):
        if self._nlp: return True
        try:
            from nlp_enhanced import EnhancedNLP
            self._nlp = EnhancedNLP()
            return True
        except Exception:
            return False

    def extract_from_dag(self) -> dict:
        """从 DAG 节点中提取实体三元组"""
        if not self._lazy_nlp():
            return {"status": "skipped", "reason": "no nlp"}

        if not os.path.exists(DAG_DB):
            return {"status": "skipped", "reason": "no dag"}

        with self._lock:
            try:
                conn = sqlite3.connect(DAG_DB)
                cursor = conn.cursor()
                cursor.execute("SELECT content FROM dag_nodes ORDER BY timestamp DESC LIMIT 100")
                rows = cursor.fetchall()
                conn.close()

                for (content,) in rows:
                    if not content:
                        continue
                    try:
                        result = self._nlp.analyze(content[:1000])
                        entities = result.get('entities', [])
                        for ent in entities:
                            name = ent.get('text', '') if isinstance(ent, dict) else str(ent)
                            if not name or len(name) < 2:
                                continue
                            if name not in self._entities:
                                self._entities[name] = {'count': 0, 'relations': [], 'contexts': []}
                            self._entities[name]['count'] += 1
                            if len(self._entities[name]['contexts']) < 3:
                                self._entities[name]['contexts'].append(content[:200])
                    except Exception:
                        continue

                communities: Dict[str, List[str]] = {}
                for ent_name, ent_data in self._entities.items():
                    if ent_data['count'] > 1:
                        comm = hashlib.md5(ent_name.encode()).hexdigest()[:8]
                        if comm not in communities:
                            communities[comm] = []
                        communities[comm].append(ent_name)

                return {"status": "ok", "entities": len(self._entities), "communities": len(communities)}
            except Exception as e:
                logger.warning(f"GraphRAG extract error: {e}")
                return {"status": "error", "reason": str(e)}

# ===== 3. Generative Agents — 反思层 =====
class ReflectionEngine:
    """arXiv:2304.03442 — 记忆流 + 反思 + 规划"""

    def __init__(self):
        self._reflections = []
        self._reflections_path = os.path.join(WORKSPACE, '.learnings', 'reflections_ga.jsonl')

    def reflect(self, recent_memories: list) -> dict:
        """分析近期记忆，提炼高层次反思"""
        if not recent_memories:
            return {"status": "skipped"}

        texts = [m if isinstance(m, str) else m.get('content', str(m)) for m in recent_memories[:20]]
        if not texts:
            return {"status": "skipped"}

        batch = "\n".join(f"- {t[:200]}" for t in texts[-10:])
        prompt = f"分析以下近期记忆，提炼3个高层次反思（每点不超过50字）：\n{batch}"

        result = _flash_chat(prompt)
        if not result:
            return {"status": "no_flash"}

        reflection = {
            "time": datetime.now().isoformat(),
            "summary": result[:500],
            "memory_count": len(texts)
        }
        self._reflections.append(reflection)

        os.makedirs(os.path.dirname(self._reflections_path), exist_ok=True)
        with open(self._reflections_path, 'a') as f:
            f.write(json.dumps(reflection, ensure_ascii=False) + '\n')

        return {"status": "ok", "reflection": result[:200]}

# ===== 4. Toolformer — 智能工具路由 =====
class ToolformerEngine:
    """arXiv:2302.04761 — 让模型自动判断用哪个工具"""

    def route(self, query: str) -> dict:
        """关键词匹配路由（简化实现）"""
        tools = [
            {"name": "memory_recall", "desc": "检索历史和记忆", "keywords": ["上次", "之前", "我记得", "回忆", "retrieval"]},
            {"name": "memory_store", "desc": "保存新记忆", "keywords": ["记住", "保存", "存储", "记下"]},
            {"name": "web_search", "desc": "联网搜索", "keywords": ["搜索", "查询", "找一下", "搜一下", "最新", "天气", "新闻"]},
            {"name": "code_analysis", "desc": "代码分析", "keywords": ["代码", "bug", "报错", "错误", "修复"]},
            {"name": "health_check", "desc": "系统健康检查", "keywords": ["健康", "状态", "检查", "status"]},
        ]

        query_lower = query.lower()
        for tool in tools:
            if any(kw in query_lower for kw in tool['keywords']):
                return {"tool": tool['name'], "reason": f"匹配: {tool['name']}"}

        return {"tool": "general", "reason": "常规回答"}


# ===== 统一入口 =====
class FourAdvancements:
    """四个论文方向统一管理器"""

    def __init__(self):
        self.raptor = RAPTOREngine()
        self.graphrag = GraphRAGEngine()
        self.reflection = ReflectionEngine()
        self.toolformer = ToolformerEngine()
        self._thread = None
        self._running = False

    def start_background(self, interval: int = 600):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, args=(interval,), daemon=True)
        self._thread.start()
        logger.info(f"FourAdvancements started (interval={interval}s)")

    def _run_loop(self, interval: int):
        while self._running:
            try:
                r = self.raptor.build_tree()
                if r.get('status') == 'ok':
                    logger.info(f"RAPTOR: {r.get('clusters', {})}")
            except Exception:
                pass
            try:
                g = self.graphrag.extract_from_dag()
                if g.get('status') == 'ok':
                    logger.info(f"GraphRAG: {g.get('entities')} entities")
            except Exception:
                pass
            try:
                memories = []
                if os.path.exists(DAG_DB):
                    conn = sqlite3.connect(DAG_DB)
                    cursor = conn.cursor()
                    cursor.execute("SELECT content FROM dag_nodes ORDER BY timestamp DESC LIMIT 20")
                    memories = [row[0] for row in cursor.fetchall()]
                    conn.close()
                if memories:
                    ref = self.reflection.reflect(memories)
                    if ref.get('status') == 'ok':
                        logger.info(f"Reflection: {ref.get('reflection','')[:60]}")
            except Exception:
                pass
            time.sleep(interval)

    def stop(self):
        self._running = False

    def get_status(self) -> dict:
        return {
            "raptor_tree_built": self.raptor._tree_built,
            "graphrag_entities": len(self.graphrag._entities),
            "reflection_count": len(self.reflection._reflections),
        }

    def search(self, query: str, top_k: int = 3) -> list:
        """搜索所有论文引擎的产出作为证据源"""
        results = []

        # RAPTOR 摘要树匹配
        if self.raptor._tree_built and self.raptor._summaries:
            q_lower = query.lower()
            for level, summary in self.raptor._summaries.items():
                if any(word in summary.lower() for word in q_lower.split()):
                    results.append({
                        "content": f"[RAPTOR {level}] {summary}",
                        "score": 0.7,
                        "source": "raptor_tree"
                    })
                    if len(results) >= top_k:
                        break

        # GraphRAG 实体匹配
        if self.graphrag._entities:
            q_lower = query.lower()
            for entity_name, entity_info in self.graphrag._entities.items():
                if entity_name.lower() in q_lower or any(w in entity_name.lower() for w in q_lower.split()):
                    content = entity_info.get('summary', json.dumps(entity_info, ensure_ascii=False))[:500]
                    results.append({
                        "content": f"[GraphRAG {entity_name}] {content}",
                        "score": 0.65,
                        "source": "graphrag"
                    })
                    if len(results) >= top_k:
                        break

        # Reflection 反思经验匹配
        if self.reflection._reflections:
            q_lower = query.lower()
            for ref in self.reflection._reflections[-10:][::-1]:  # 最新的10条
                ref_text = ref.get('reflection', '')
                if any(word in ref_text.lower() for word in q_lower.split()):
                    results.append({
                        "content": f"[反思经验] {ref_text[:300]}",
                        "score": 0.6,
                        "source": "reflection"
                    })
                    if len(results) >= top_k:
                        break

        # Toolformer 工具路由（用于 Control 阶段）
        tool_match = self.toolformer.route(query)
        if tool_match and tool_match.get('tool') and tool_match['tool'] != 'general':
            results.append({
                "content": f"[工具推荐] {tool_match['tool']}: {tool_match.get('reason','')}",
                "score": 0.5,
                "source": "toolformer"
            })

        return results


_advancements = None

def get_advancements():
    global _advancements
    if _advancements is None:
        _advancements = FourAdvancements()
    return _advancements
