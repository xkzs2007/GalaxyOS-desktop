#!/usr/bin/env python3
"""
DAG 上下文管理器 Shim — claw-bootstrap hook 调用入口

用法:
  python3 dag_shim.py init --session <key>            # 初始化 DAG + 注入人格节点
  python3 dag_shim.py add --session <key> --msg <text>  # 添加消息
  python3 dag_shim.py assemble --session <key>         # 组装上下文
  python3 dag_shim.py summarize --session <key>        # 增量摘要
"""

import sys
import os
import json
import time
import argparse
from pathlib import Path

# 添加核心模块路径
CORE_DIR = Path(__file__).parent.parent / "skills/xiaoyi-claw-omega-final/skills/llm-memory-integration/core"
sys.path.insert(0, str(CORE_DIR))

try:
    from dag_context_manager import DAGContextManager, DAGIntegration, PriorityLevel
    from xiaoyi_memory import XiaoyiMemoryV2
    CORE_OK = True
except ImportError as e:
    print(json.dumps({"error": f"模块导入失败: {e}"}))
    CORE_OK = False


def init_dag(session_key: str) -> dict:
    """初始化 DAG + 注入人格节点"""
    ws = os.path.expanduser("~/.openclaw/workspace")
    
    # 读取人格定义
    persona_text = ""
    for fname in ["IDENTITY.md", "SOUL.md"]:
        fp = os.path.join(ws, fname)
        if os.path.exists(fp):
            with open(fp, "r", encoding="utf-8") as f:
                lines = f.readlines()
                # 取前15行作为核心人格定义
                chunk = "".join(lines[:15]).strip()
                if chunk:
                    persona_text += f"[{fname}]\n{chunk}\n\n"
    
    # 初始化 DAG
    dag_db = os.path.join(ws, ".dag_context.db")
    dag = DAGContextManager(
        db_path=dag_db,
        max_context_tokens=240000,
        fresh_tail_count=10,
        leaf_chunk_tokens=8000,
    )
    
    # 注入人格节点（priority: CRITICAL，永不压缩）
    if persona_text:
        tokens = len(persona_text) // 2
        dag.add_persona_node(session_key, persona_text, tokens=tokens)
    
    # 集成适配器
    memory = XiaoyiMemoryV2()
    integration = DAGIntegration(dag, memory=memory)
    
    return {"dag_db": dag_db, "persona_tokens": len(persona_text) // 2}


def add_message(session_key: str, content: str, role: str = "user") -> dict:
    """添加消息到 DAG"""
    ws = os.path.expanduser("~/.openclaw/workspace")
    dag_db = os.path.join(ws, ".dag_context.db")
    
    dag = DAGContextManager(
        db_path=dag_db,
        max_context_tokens=240000,
        fresh_tail_count=10,
        leaf_chunk_tokens=8000,
    )
    
    node_id = integration.add_message_with_scene(
        session_key=session_key,
        role=role,
        content=content,
    )
    
    # 如果消息较多，触发增量摘要
    summary = integration.auto_summarize(session_key, batch_size=8)
    
    return {"node_id": node_id, "summary": summary}


def assemble_context(session_key: str) -> dict:
    """组装上下文（走语义缓存）"""
    ws = os.path.expanduser("~/.openclaw/workspace")
    dag_db = os.path.join(ws, ".dag_context.db")
    
    dag = DAGContextManager(
        db_path=dag_db,
        max_context_tokens=240000,
        fresh_tail_count=10,
        leaf_chunk_tokens=8000,
    )
    
    memory = XiaoyiMemoryV2()
    integration = DAGIntegration(dag, memory=memory)
    
    text, stats = integration.assemble_with_cache(session_key)
    
    return {
        "assembled_text": text,
        "stats": {
            "total_tokens": stats.get("total_tokens", 0),
            "max_tokens": stats.get("max_tokens", 240000),
            "critical_nodes": stats.get("critical_nodes", 0),
            "recent_messages": stats.get("recent_messages", 0),
            "summary_nodes_used": stats.get("summary_nodes_used", 0),
            "from_cache": stats.get("from_cache", False),
            "cached": stats.get("cached", False),
        }
    }


def restore_context(session_key: str = "default", recent_days: int = 3) -> dict:
    """跨会话记忆恢复（bootstrap 增强）
    
    用 Flash 开推理汇总最近几天的关键记忆，注入到会话启动。
    """
    ws = os.path.expanduser("~/.openclaw/workspace")
    dag_db = os.path.join(ws, ".dag_context.db")
    
    dag = DAGContextManager(
        db_path=dag_db,
        max_context_tokens=240000,
        fresh_tail_count=10,
        leaf_chunk_tokens=8000,
    )
    
    memory = XiaoyiMemoryV2()
    integration = DAGIntegration(dag, memory=memory)
    
    summary = integration.cross_session_memory_restore(session_key, recent_days)
    
    return {"restored_text": summary or "", "recent_days": recent_days}


if __name__ == "__main__":
    if not CORE_OK:
        sys.exit(1)
    
    parser = argparse.ArgumentParser(description="DAG Context Manager Shim")
    parser.add_argument("action", choices=["init", "add", "assemble", "summarize", "restore"])
    parser.add_argument("--session", default="default")
    parser.add_argument("--msg", default="")
    parser.add_argument("--role", default="user")
    parser.add_argument("--days", type=int, default=3, help="跨会话回溯天数")
    
    args = parser.parse_args()
    
    try:
        if args.action == "init":
            result = init_dag(args.session)
        elif args.action == "add":
            result = add_message(args.session, args.msg, args.role)
        elif args.action == "assemble":
            result = assemble_context(args.session)
        elif args.action == "summarize":
            result = add_message(args.session, "", "")  # 触发 auto_summarize
            result = {"message": "summarize triggered"}
        elif args.action == "restore":
            result = restore_context(args.session, args.days)
        
        print(json.dumps(result))
    except Exception as e:
        print(json.dumps({"error": str(e)}))
        sys.exit(1)
