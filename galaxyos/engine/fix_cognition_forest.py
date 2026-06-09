#!/usr/bin/env python3
"""
修复 Cognition Forest 四棵子树注入逻辑

问题:
- _cog_subtree_user: 存的是 AI answer 文本，不是用户画像
- _cog_subtree_self: 存的是 Q&A 输出，不是系统能力
- _cog_subtree_env: 存的是策略 JSON，没有运行环境信息
- _cog_subtree_meta: 空架子

修复:
1. 读取真实文件注入子树
2. 修 xiaoyi_claw_api.py 注入逻辑
3. 清理历史错误数据
"""

import os
import json
import sys
import time
import sqlite3
import argparse
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

# ── 路径 ──
WORKSPACE = os.path.expanduser("~/.openclaw/workspace")
DIST_SCRIPTS = os.path.expanduser("~/.openclaw/extensions/claw-core/dist/scripts")
DAG_DB = os.path.expanduser("~/.openclaw/dag_context.db")
SKILLS_BASE = os.path.join(WORKSPACE, "skills")
OMEGA_DIR = os.path.join(SKILLS_BASE, "xiaoyi-claw-omega-final")


def clear_subtree(conn: sqlite3.Connection, forest_type: str):
    """清空指定子树"""
    session_key = f"_cog_subtree_{forest_type}"
    conn.execute("DELETE FROM dag_nodes WHERE session_key=?", (session_key,))
    try:
        conn.execute("DELETE FROM dag_fts WHERE rowid IN (SELECT rowid FROM dag_nodes WHERE session_key=?)", (session_key,))
    except Exception:
        pass
    conn.commit()
    logger.info(f"  ✅ 已清空 _cog_subtree_{forest_type}")


def build_user_profile() -> str:
    """构建用户画像数据"""
    parts = []
    
    # IDENTITY.md
    identity_path = os.path.join(WORKSPACE, "IDENTITY.md")
    if os.path.exists(identity_path):
        with open(identity_path, "r", encoding="utf-8") as f:
            content = f.read(1500)
            parts.append(f"[身份定义]\n{content.strip()}")
    
    # USER.md
    user_path = os.path.join(WORKSPACE, "USER.md")
    if os.path.exists(user_path):
        with open(user_path, "r", encoding="utf-8") as f:
            content = f.read(2000)
            parts.append(f"[用户画像]\n{content.strip()}")
    
    # SOUL.md 核心身份部分
    soul_path = os.path.join(WORKSPACE, "SOUL.md")
    if os.path.exists(soul_path):
        with open(soul_path, "r", encoding="utf-8") as f:
            lines = f.readlines()
        core_lines = [l for l in lines if l.startswith("#") or l.startswith("**") or "Core Truths" in l or "Boundaries" in l or "Vibe" in l]
        if core_lines:
            parts.append("[人格核心]\n" + "".join(core_lines[:20]))
    
    # scene blocks 索引
    scene_dir = os.path.join(WORKSPACE, "scene_blocks")
    if os.path.exists(scene_dir):
        scenes = []
        for f in sorted(os.listdir(scene_dir)):
            if f.endswith(".md"):
                path = os.path.join(scene_dir, f)
                st = os.stat(path)
                scenes.append(f"{f} ({st.st_size}b)")
        if scenes:
            parts.append(f"[场景索引]\n{chr(10).join(scenes[:10])}")
    
    # MEMORY.md 核心偏好
    memory_path = os.path.join(WORKSPACE, "MEMORY.md")
    if os.path.exists(memory_path):
        with open(memory_path, "r", encoding="utf-8") as f:
            content = f.read(3000)
        # 提取核心偏好的前三段
        pref_idx = content.find("长期偏好")
        if pref_idx > -1:
            prefs = content[pref_idx:min(pref_idx+600, len(content))]
            parts.append(f"[长期偏好]\n{prefs.strip()}")

    return "\n\n".join(parts)


def build_system_capability() -> str:
    """构建系统能力数据"""
    parts = []
    
    # 可用技能列表
    skills_dir = os.path.join(WORKSPACE, "skills")
    available = []
    if os.path.exists(skills_dir):
        for d in sorted(os.listdir(skills_dir)):
            skill_path = os.path.join(skills_dir, d)
            if os.path.isdir(skill_path):
                sk_md = os.path.join(skill_path, "SKILL.md")
                if os.path.exists(sk_md):
                    with open(sk_md, "r", encoding="utf-8") as f:
                        first_line = f.readline().strip()
                    available.append(f"- {d}: {first_line[:80]}")
                else:
                    available.append(f"- {d}")
    
    if available:
        parts.append("[可用技能]\n" + "\n".join(available))
    
    # IntelligentThinkingTrigger 可用技能
    trigger_path = os.path.join(DIST_SCRIPTS, "intelligent_thinking_trigger.py")
    if os.path.exists(trigger_path):
        from intelligent_thinking_trigger import IntelligentThinkingTrigger
        trigger = IntelligentThinkingTrigger()
        stats = trigger.get_analysis_stats()
        parts.append(f"[智能触发器]\n技能数: 30 | 分析次数: {stats.get('total_analyses', 0)}")
    
    # 思考技能目录
    thinking_skills = [
        "first-principles", "systems-thinking", "critical-thinking",
        "backward-thinking", "analogical-thinking", "feynman-technique",
        "decision-engine", "product-thinking", "diagnose",
        "grill-with-docs", "tdd", "improve-codebase-architecture",
        "prototype", "zoom-out", "grill-me", "caveman",
        "handoff", "write-a-skill",
    ]
    methodology_skills = [
        "investigation-first", "contradiction-analysis", "practice-cognition",
        "concentrate-forces", "overall-planning", "mass-line",
        "criticism-self-criticism", "spark-prairie-fire",
        "arming-thought", "protracted-strategy", "workflows",
    ]
    parts.append(f"[思考技能]\n{', '.join(thinking_skills)}")
    parts.append(f"[方法论技能]\n{', '.join(methodology_skills)}")
    
    # 运行模块
    try:
        import urllib.request
        req = urllib.request.Request(
            "http://127.0.0.1:8765/rpc",
            data=json.dumps({"method": "health", "params": {}, "id": 1}).encode(),
            headers={"Content-Type": "application/json"},
        )
        r = json.loads(urllib.request.urlopen(req, timeout=3).read())
        result = r.get("result", {})
        healthy = result.get("healthy", False)
        hg = result.get("hallucination_guard", {})
        parts.append(f"[Worker状态]\n健康: {healthy} | 防幻觉: {hg.get('status', '?')}")
    except Exception as e:
        parts.append(f"[Worker状态]\n不可用: {e}")
    
    return "\n\n".join(parts)


def build_env_profile() -> str:
    """构建运行环境数据"""
    import platform
    info = []
    
    info.append(f"时间: {time.strftime('%Y-%m-%d %H:%M:%S %z')}")
    info.append(f"系统: {platform.system()} {platform.machine()}")
    info.append(f"Python: {platform.python_version()}")
    info.append(f"时区: Asia/Shanghai (UTC+8)")
    info.append(f"工作目录: {WORKSPACE}")
    
    # 模型信息
    info.append(f"对话模型: LLM_DeepSeekV4_Thinking")
    info.append(f"嵌入模型: BAAI/bge-m3 (1024维)")
    info.append(f"重排序模型: BAAI/bge-reranker-v2-m3")
    
    # DAG 状态
    if os.path.exists(DAG_DB):
        try:
            conn = sqlite3.connect(DAG_DB)
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*) FROM dag_nodes")
            node_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM dag_nodes WHERE node_type='persona'")
            persona_count = cur.fetchone()[0]
            cur.execute("SELECT COUNT(*) FROM dag_nodes WHERE node_type='summary' OR node_type='cognitive_summary'")
            summary_count = cur.fetchone()[0]
            conn.close()
            info.append(f"DAG节点: {node_count} 总 | {persona_count} 人格 | {summary_count} 摘要")
        except Exception:
            pass
    
    return "\n".join(info)


def build_meta_profile() -> str:
    """构建元认知数据"""
    meta = {
        "evolution": {
            "enabled": True,
            "self_evolution": True,
            "system": "xiaoyi-self-evolution",
        },
        "memory": {
            "tier": "L1-L4",
            "engine": "XiaoYiClawLLM + DAG",
            "retrieval": "hybrid (vector + KG + temporal)",
        },
        "framework": {
            "name": "xiaoyi-claw-omega-final",
            "version": "v9.0.1",
            "layers": ["R-CCAM", "CRAG", "Self-RAG", "A-ToM", "RCR-Router"],
            "compression": "DAG (SQLite-based, LCM-inspired)",
        },
        "integrations": {
            "Lobster": True,
            "ClawHub": True,
            "CNB": True,
        },
    }
    return json.dumps(meta, ensure_ascii=False, indent=2)


def inject_subtree(conn: sqlite3.Connection, forest_type: str, content: str, source: str):
    """注入子树数据"""
    from dag_context_manager import DAGContextManager
    dag = DAGContextManager(db_path=DAG_DB)
    
    # 分段注入（防止单条过大）
    chunks = []
    chunk_size = 1500
    for i in range(0, len(content), chunk_size):
        chunks.append(content[i:i+chunk_size])
    
    for i, chunk in enumerate(chunks):
        dag.add_cognition_subtree(
            forest_type=forest_type,
            content=chunk,
            tokens=len(chunk) // 4,
            source=f"{source}_{i}",
            metadata={"batch": i, "total": len(chunks)},
        )
    
    logger.info(f"  ✅ {forest_type}: {len(chunks)} 条 ({len(content)} chars)")


def fix_xiaoyi_claw_api():
    """修复 xiaoyi_claw_api.py 的子树注入逻辑"""
    api_path = os.path.join(DIST_SCRIPTS, "xiaoyi_claw_api.py")
    api_bak = api_path + ".bak"
    
    if not os.path.exists(api_path):
        logger.error(f"❌ 未找到 {api_path}")
        return False
    
    # 备份
    if not os.path.exists(api_bak):
        import shutil
        shutil.copy2(api_path, api_bak)
        logger.info(f"📦 备份: {api_bak}")
    
    with open(api_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    # ── 修复 1: _cognition_phase 中的 user 子树注入 ──
    old_user_inject = """        # ── Cognition Forest 写入: 检索结果写入 user 子树 ──
        try:
            if self.dag and hasattr(self.dag, 'add_cognition_subtree'):
                memories = getattr(state, 'retrieved_memories', [])
                if memories:
                    _summary = "\\n".join(f"- {m.get('content','')[:200]}" for m in memories[:5])
                    self.dag.add_cognition_subtree(
                        forest_type="user",
                        content=f"[{state.strategy}] {query[:100]}\\n检索结果:\\n{_summary}",
                        tokens=len(_summary) // 2,
                        source="retrieval_phase",
                    )
        except Exception:
            pass"""
    
    new_user_inject = """        # ── Cognition Forest 写入: 用户画像写入 user 子树 ──
        try:
            if self.dag and hasattr(self.dag, 'add_cognition_subtree'):
                _ctx = state.user_context or {}
                _pref = {k: str(v)[:100] for k, v in _ctx.items() if k in (
                    "tech_preferences", "communication_style", "domain",
                    "task_history", "current_project", "thinking_preferences"
                )}
                if _pref:
                    self.dag.add_cognition_subtree(
                        forest_type="user",
                        content=json.dumps({"type": "preferences", "data": _pref}, ensure_ascii=False),
                        tokens=len(str(_pref)) // 3,
                        source="cognition_phase",
                    )
        except Exception:
            pass"""
    
    if old_user_inject in content:
        content = content.replace(old_user_inject, new_user_inject)
        logger.info("  ✅ _cognition_phase user 子树注入已修复")
    else:
        logger.warning("  ⚠️ _cognition_phase user 注入模式不匹配")
    
    # ── 修复 2: _memory_phase 中 self 子树注入 ──
    old_self_inject = """                    _ft = getattr(state, 'strategy', 'answer')
                    _conf = getattr(state, 'answer_confidence', 0.5)
                    _added = self.dag.add_cognition_subtree(
                        forest_type="self",
                        content=f"[{_ft}] Q:{query[:100]}" +
                                (f"\\nA:{answer[:200]}" if answer else ""),
                        tokens=(len(query) + len(answer or '')) // 4,
                        source="memory_phase",
                        metadata={"strategy": _ft, "confidence": _conf},
                    )"""
    
    new_self_inject = """                    _ft = getattr(state, 'strategy', 'answer')
                    _conf = getattr(state, 'answer_confidence', 0.5)
                    # self 子树：记录能力使用情况，不是 Q&A 原文
                    _skill_used = getattr(state, 'thinking_skills_used', [])
                    _skill_names = [s.value if hasattr(s, 'value') else str(s) for s in _skill_used[:5]]
                    _cap_ctx = {
                        "type": "capability_usage",
                        "strategy": _ft,
                        "confidence": _conf,
                        "think_skills": _skill_names,
                        "complexity": state.analysis.get('complexity', 0),
                        "retrieval_count": getattr(state, 'search_count', 0),
                    }
                    _added = self.dag.add_cognition_subtree(
                        forest_type="self",
                        content=json.dumps(_cap_ctx, ensure_ascii=False),
                        tokens=len(json.dumps(_cap_ctx)) // 3,
                        source="memory_phase",
                        metadata={"strategy": _ft, "confidence": _conf},
                    )"""
    
    # 用更灵活的方式替换
    import re
    # 找到 _added = self.dag.add_cognition_subtree(
    #    forest_type="self",
    # 开头的块
    old_self_block = re.search(
        r'            if hasattr\(self\.dag, \'add_cognition_subtree\'\):.*?'
        r'                    self\.dag\.add_cognition_subtree\(.*?'
        r'                        forest_type="self",.*?'
        r'                    \)',
        content, re.DOTALL
    )
    
    if old_self_block:
        matched = old_self_block.group(0)
        logger.info(f"  🔍 self 注入块找到，长度 {len(matched)}")
        # 替换整个块
        new_block = """            if hasattr(self.dag, 'add_cognition_subtree'):
                    _ft = getattr(state, 'strategy', 'answer')
                    _conf = getattr(state, 'answer_confidence', 0.5)
                    _skill_used = getattr(state, 'thinking_skills_used', [])
                    _skill_names = [s.value if hasattr(s, 'value') else str(s) for s in _skill_used[:5]]
                    _cap_ctx = {
                        "type": "capability_usage",
                        "strategy": _ft,
                        "confidence": _conf,
                        "think_skills": _skill_names,
                        "complexity": state.analysis.get('complexity', 0),
                        "retrieval_count": getattr(state, 'search_count', 0),
                    }
                    _added = self.dag.add_cognition_subtree(
                        forest_type="self",
                        content=json.dumps(_cap_ctx, ensure_ascii=False),
                        tokens=len(json.dumps(_cap_ctx)) // 3,
                        source="memory_phase",
                        metadata={"strategy": _ft, "confidence": _conf},
                    )"""
        content = content.replace(matched, new_block)
        logger.info("  ✅ _memory_phase self 子树注入已修复")
    else:
        logger.warning("  ⚠️ self 注入块未匹配")
    
    # 写入
    with open(api_path, "w", encoding="utf-8") as f:
        f.write(content)
    
    logger.info(f"  ✅ xiaoyi_claw_api.py 已更新")
    return True


def main():
    parser = argparse.ArgumentParser(description="修复 Cognition Forest 子树")
    parser.add_argument("--clear", action="store_true", help="清空所有子树数据")
    parser.add_argument("--inject", action="store_true", help="注入正确数据")
    parser.add_argument("--fix-api", action="store_true", help="修复 xiaoyi_claw_api.py")
    parser.add_argument("--all", action="store_true", help="执行全部修复")
    args = parser.parse_args()
    
    if not any([args.clear, args.inject, args.fix_api, args.all]):
        args.all = True
    
    if args.all or args.clear or args.inject:
        # 加载 DAG
        sys.path.insert(0, DIST_SCRIPTS)
        from dag_context_manager import DAGContextManager
        
        if not os.path.exists(DAG_DB):
            logger.error(f"❌ DAG 数据库不存在: {DAG_DB}")
            return 1
        
        conn = sqlite3.connect(DAG_DB)
        
        forest_types = ["user", "self", "env", "meta"]
        
        # 清空
        if args.all or args.clear:
            logger.info("🧹 清空子树...")
            for ft in forest_types:
                clear_subtree(conn, ft)
        
        # 注入
        if args.all or args.inject:
            logger.info("📦 注入正确数据...")
            
            logger.info("  ── 用户画像 (user) ──")
            user_content = build_user_profile()
            inject_subtree(conn, "user", user_content, "init_profile")
            
            logger.info("  ── 系统能力 (self) ──")
            self_content = build_system_capability()
            inject_subtree(conn, "self", self_content, "init_capability")
            
            logger.info("  ── 运行环境 (env) ──")
            env_content = build_env_profile()
            inject_subtree(conn, "env", env_content, "init_env")
            
            logger.info("  ── 元认知 (meta) ──")
            meta_content = build_meta_profile()
            inject_subtree(conn, "meta", meta_content, "init_meta")
        
        conn.close()
        logger.info("")
    
    # 修 API
    if args.all or args.fix_api:
        logger.info("🔧 修复 xiaoyi_claw_api.py 注入逻辑...")
        fix_xiaoyi_claw_api()
    
    logger.info("")
    logger.info("🎯 完成!")
    logger.info("  后续步骤: python3 -m supervisor.supervisorctl restart claw-worker")
    return 0


if __name__ == "__main__":
    sys.exit(main())
