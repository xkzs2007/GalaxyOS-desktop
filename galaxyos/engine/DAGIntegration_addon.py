"""
DAGIntegration — 缺失的集成层

给 dag_context_manager.py 补上 DAGIntegration 类，
包含 cross_session_memory_restore 方法。
"""
import time
import json
import logging

logger = logging.getLogger(__name__)


class DAGIntegration:
    """
    DAG 集成层 — Worker/Plugin 调用入口

    提供跨会话记忆恢复、场景编码注入等高层接口。
    原来从未存在过（claw_worker.py import 了但实际代码没有），
    现在是补上的。
    """

    def __init__(self, dag, memory=None):
        self.dag = dag
        self.memory = memory

    def cross_session_memory_restore(self, session_key: str, recent_days: int = 3) -> str:
        """
        跨会话记忆恢复

        从 DAG 数据库中检索 recent_days 天内所有会话的关键节点，
        按每会话分组，先取高质量抽象（cycle_summary / is_summary），
        没有抽象时直接取最近 message 节点的对话摘要。

        设计原则：
        - 动态发现所有活动 session_key（不硬编码、不隔离）
        - 不过滤当前 session_key（跨会话就是要看其他会话的数据）
        - 每会话只取一条最优概括
        - token 预算 ~2000，确保不撑爆 bootstrap 注入窗口
        """
        since = time.time() - recent_days * 86400

        parts = []
        budget = 2000
        used = 0

        db_path = self.dag.db_path

        import sqlite3
        conn = sqlite3.connect(db_path)

        # ── 0. 发现 active_days 内所有有数据的 session_key ──
        active_sessions = set()
        try:
            rows = conn.execute(
                """SELECT DISTINCT session_key FROM dag_nodes
                   WHERE timestamp >= ?
                   UNION
                   SELECT DISTINCT session_key FROM rccam_nodes
                   WHERE timestamp >= ?""",
                (since, since)
            ).fetchall()
            active_sessions = {r[0] for r in rows if r[0] and r[0] != '_cog_subtree_user' and r[0] != '_cog_subtree_self'}
        except Exception as e:
            logger.debug(f"cross_session: 扫描 session_keys 失败: {e}")

        if not active_sessions:
            conn.close()
            return ""

        # ── 对每个 session_key，尝试获取最优概括 ──
        # 查询顺序：rccam_cycle_summary > dag_nodes.is_summary > message 摘要
        for sk in sorted(active_sessions):
            if used >= budget:
                break

            session_label = sk.replace("agent:main:direct:", "")[:40]
            found = False

            # — 1. 该 session 的 rccam_cycle_summary —
            try:
                rows = conn.execute(
                    """SELECT content, cycle_index, timestamp
                       FROM rccam_nodes
                       WHERE node_type='rccam_cycle_summary'
                         AND session_key=?
                       ORDER BY timestamp DESC LIMIT 1""",
                    (sk,)
                ).fetchall()
                if rows:
                    r = rows[0]
                    content_str = r[0]
                    try:
                        obj = json.loads(content_str)
                        if isinstance(obj, dict):
                            intent = obj.get("user_intent", "")[:100]
                            findings = obj.get("key_findings", [])
                            findings_str = "; ".join(str(f)[:80] for f in findings[:2])
                            conclusion = obj.get("conclusion", "")[:100]
                            summary_text = f"意图: {intent}"
                            if findings_str:
                                summary_text += f" | 发现: {findings_str}"
                            if conclusion:
                                summary_text += f" | 结论: {conclusion}"
                        else:
                            summary_text = str(content_str)[:200]
                    except (json.JSONDecodeError, TypeError):
                        summary_text = str(content_str)[:200]

                    text = f"[{session_label}]\n{summary_text}"
                    if used + len(text) <= budget:
                        parts.append(text)
                        used += len(text)
                    found = True
            except Exception:
                pass

            if found:
                continue

            # — 2. 该 session 的 dag_summary —
            try:
                rows = conn.execute(
                    """SELECT content, keywords, timestamp
                       FROM dag_nodes
                       WHERE is_summary=1 AND session_key=?
                       ORDER BY timestamp DESC LIMIT 1""",
                    (sk,)
                ).fetchall()
                if rows:
                    r = rows[0]
                    kw = ""
                    try:
                        kws = json.loads(r[1]) if isinstance(r[1], str) else r[1]
                        if kws:
                            kw = " [" + ", ".join(str(w) for w in kws[:5]) + "]"
                    except Exception:
                        pass
                    summary_text = r[0][:200] + kw

                    text = f"[{session_label}]\n{summary_text}"
                    if used + len(text) <= budget:
                        parts.append(text)
                        used += len(text)
                    found = True
            except Exception:
                pass

            if found:
                continue

            # — 3. 该 session 的 message 节点（无摘要时的兜底） —
            try:
                rows = conn.execute(
                    """SELECT content, timestamp
                       FROM dag_nodes
                       WHERE node_type='message' AND session_key=?
                       ORDER BY timestamp DESC LIMIT 10""",
                    (sk,)
                ).fetchall()
                if not rows:
                    # 也查 rccam_nodes 的 message-like 节点
                    rows = conn.execute(
                        """SELECT content, timestamp
                           FROM rccam_nodes
                           WHERE session_key=? AND phase_name != ''
                           ORDER BY timestamp DESC LIMIT 10""",
                        (sk,)
                    ).fetchall()

                if rows:
                    # 倒序（时间从早到晚）排成对话流
                    messages = list(reversed(rows))
                    # 合并相邻 user/assistant 成对话对
                    dialogue_lines = []
                    for i, r in enumerate(messages):
                        content_text = r[0][:150]
                        dialogue_lines.append(f"· {content_text}")

                    summary_text = "\n".join(dialogue_lines[-6:])  # 最多6条
                    text = f"[{session_label}]\n{summary_text}"
                    if used + len(text) <= budget:
                        parts.append(text)
                        used += len(text)
            except Exception:
                pass

        # ── 4. 人格节点（可选补充，预算有余才加） ──
        remaining = budget - used
        if remaining > 150:
            try:
                rows = conn.execute(
                    """SELECT content, timestamp
                       FROM dag_nodes
                       WHERE node_type='persona'
                       ORDER BY timestamp DESC LIMIT 1"""
                ).fetchall()
                if rows:
                    persona = rows[0][0][:min(200, remaining - 30)]
                    text = f"[人格快照]\n{persona}"
                    parts.append(text)
                    used += len(text)
            except Exception:
                pass

        conn.close()

        if not parts:
            return ""

        restored = "\n\n".join(parts)
        # 硬上限 3000 字符，防止撑爆注入窗口
        return restored[:3000]
