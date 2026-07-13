#!/usr/bin/env python3
"""
DAG 上下文管理器 Shim �?claw-bootstrap hook 调用入口

用法:
  python3 dag_shim.py init --session <key>
  python3 dag_shim.py add --session <key> --msg <text>  (role=user|assistant)
  python3 dag_shim.py assemble --session <key>
  python3 dag_shim.py summarize --session <key>
"""

import sys
import os
import json
import time
import argparse
from pathlib import Path
from galaxyos.shared.paths import workspace

CORE_DIR = Path(__file__).parent.parent / "skills/galaxyos-engine/skills/llm-memory-integration/core"
sys.path.insert(0, str(CORE_DIR))

try:
    from dag_context_manager import DAGContextManager, PriorityLevel
    CORE_OK = True
except ImportError as e:
    print(json.dumps({"error": f"模块导入失败: {e}"}))
    CORE_OK = False


def _get_dag():
    ws = workspace()
    return DAGContextManager(
        db_path=os.path.expanduser("~/.openclaw/dag_context.db"),
        max_context_tokens=240000,
        fresh_tail_count=10,
        leaf_chunk_tokens=8000,
    )


def init_dag(session_key: str) -> dict:
    """初始�?DAG + 注入人格节点"""
    ws = workspace()
    dag = _get_dag()

    persona_text = ""
    for fname in ["IDENTITY.md", "SOUL.md"]:
        fp = os.path.join(ws, fname)
        if os.path.exists(fp):
            with open(fp, "r", encoding="utf-8") as f:
                lines = f.readlines()
                chunk = "".join(lines[:15]).strip()
                if chunk:
                    persona_text += f"[{fname}]\n{chunk}\n\n"

    if persona_text:
        dag.add_persona_node(session_key, persona_text, tokens=len(persona_text) // 2)

    return {"dag_db": dag.db_path, "persona_tokens": len(persona_text) // 2}


def add_message(session_key: str, content: str, role: str = "user") -> dict:
    """添加消息�?DAG"""
    dag = _get_dag()
    node_id = dag.add_message(
        session_key=session_key,
        role=role,
        content=content,
        priority=PriorityLevel.NORMAL,
    )
    summary = dag.auto_summarize(session_key, batch_size=8)
    return {"node_id": node_id, "summary": summary}


def assemble_context(session_key: str) -> dict:
    """组装上下�?""
    dag = _get_dag()
    text, stats = dag.assemble_context(session_key)
    return {
        "assembled_text": text,
        "stats": {
            "total_tokens": stats.get("total_tokens", 0),
            "max_tokens": stats.get("max_tokens", 240000),
            "critical_nodes": stats.get("critical_nodes", 0),
            "recent_messages": stats.get("recent_messages", 0),
            "summary_nodes_used": stats.get("summary_nodes_used", 0),
            "from_cache": stats.get("from_cache", False),
        }
    }


def summarize(session_key: str) -> dict:
    """触发增量摘要"""
    dag = _get_dag()
    result = dag.auto_summarize(session_key, batch_size=8)
    return {"summarized": result.get("summarized", 0), "reason": result.get("reason", "")}


if __name__ == "__main__":
    if not CORE_OK:
        sys.exit(1)

    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["init", "add", "assemble", "summarize"])
    parser.add_argument("--session", default="default")
    parser.add_argument("--msg", default="")
    parser.add_argument("--role", default="user")

    args = parser.parse_args()

    try:
        if args.action == "init":
            result = init_dag(args.session)
        elif args.action == "add":
            result = add_message(args.session, args.msg, args.role)
        elif args.action == "assemble":
            result = assemble_context(args.session)
        elif args.action == "summarize":
            result = summarize(args.session)
        print(json.dumps(result))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
