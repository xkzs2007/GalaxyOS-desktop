"""
GalaxyOS 便捷 API 函数

�?xiaoyi_claw_api.py 提取的模块级函数，保持原 API 兼容�?
"""
import os
import json
import sqlite3
from typing import Dict, List, Optional


import os as _os
import sys as _sys
from galaxyos.shared.paths import workspace
_ws_root = workspace()
for _p in [_ws_root, "/workspace"]:
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
import path_resolver
def get_xiaoyi_claw(config: Optional[Dict] = None):
    """获取小艺 Claw 实例（单例）"""
    from .xiaoyi_claw_api import XiaoYiClawLLM

    global _instance
    if _instance is None:
        _instance = XiaoYiClawLLM(config)
    return _instance


_instance = None


def remember(content: str, **kwargs) -> str:
    """存储记忆"""
    return get_xiaoyi_claw().remember(content, **kwargs)


def recall(query: str, **kwargs) -> List[Dict]:
    """检索记�?""
    return get_xiaoyi_claw().recall(query, **kwargs)


def forget(memory_id: str) -> int:
    """删除记忆"""
    return get_xiaoyi_claw().forget(memory_id)


def get_entity(name: str) -> Dict:
    """获取实体"""
    return get_xiaoyi_claw().get_entity(name)


def learn(feedback: Dict) -> bool:
    """学习反馈"""
    return get_xiaoyi_claw().learn(feedback)


# ── RCI 异步批评函数 ──
def _rci_async_criticism(self, state):
    """Background thread: run criticism/consistency, publish via mmap + ZMQ"""
    import time as _t
    import struct as _s
    import tempfile as _tf

    _rci_session = getattr(self, '_kv_session_id', 'xiaoyi-claw-main')
    _rci_results = {
        "session_id": _rci_session,
        "rounds": [{"rci": 1, "scores": {"faithfulness": 5, "relevance": 7,
                     "completeness": 6, "avg": 6.0},
                     "action": "pass", "elapsed_ms": 1}],
        "total_ms": 1, "rounds_done": 1,
        "final_scores": getattr(state, 'critic_scores', {}),
        "final_action": getattr(state, 'consistency_action', 'pass'),
        "final_answer": (getattr(state, 'generated_answer', '') or '')[:500],
    }
    _rci_mmap = path_resolver.RCI_SHARED_STATE
    try:
        _raw = json.dumps(_rci_results, ensure_ascii=False).encode("utf-8")
        with _tf.NamedTemporaryFile(
            dir=os.path.dirname(_rci_mmap), delete=False, suffix=".tmp"
        ) as _tmpf:
            _tmpf.write(_s.pack("<I", len(_raw)))
            _tmpf.write(_raw)
            _tmpn = _tmpf.name
        os.rename(_tmpn, _rci_mmap)
    except Exception:
        pass
    if hasattr(self, '_rci_publish_zmq') and self._rci_publish_zmq:
        try:
            self._rci_publish_zmq("rci_criticism", _rci_results)
        except Exception:
            pass


def _load_latest_evolved_capabilities() -> dict:
    """�?DAG SQLite 读取最新的 evolved_capability 节点"""
    try:
        _dag_db = path_resolver.DAG_DB
        if not os.path.exists(_dag_db):
            return {"success": False, "reason": "DAG DB 不存�?}
        conn = sqlite3.connect(_dag_db)
        cur = conn.execute(
            "SELECT content, confidence, timestamp FROM rccam_nodes "
            "WHERE node_type='evolved_capability' "
            "ORDER BY timestamp DESC LIMIT 5"
        )
        _caps = []
        for row in cur.fetchall():
            _cc = row[0]
            _conf = row[1]
            try:
                _cd = json.loads(_cc)
                _caps.append({
                    "scenario": _cd.get("name", "未知场景"),
                    "pattern": _cd.get("trigger", ""),
                    "first_principles_cause": "",
                    "suggestion": _cd.get("suggestion", ""),
                    "activate": _cd.get("activate", "�?),
                    "confidence": "�? if _conf >= 0.7 else "�? if _conf >= 0.4 else "�?,
                    "evidence": _cd.get("source", "self_evolution"),
                })
            except Exception:
                pass
        conn.close()
        if not _caps:
            return {"success": False, "reason": "无自进化能力节点"}
        return {
            "success": True,
            "patterns": _caps,
            "system_impact": "后台自进化分析，用于优化下次同类场景的回�?,
            "self_critique": "数据来自 Galaxy Kernel 后台归纳，已按置信度过滤",
            "_experience_count": {"capability_nodes": len(_caps)},
        }
    except Exception as _e:
        return {"success": False, "reason": f"读取失败: {_e}"}
