#!/usr/bin/env python3
"""
Multi-Agent Debate — 多智能体辩论引擎

核心机制:
1. 3 Agent 并行辩论（正面论证 + 反面质疑 + 中立评估）
2. 裁决 Agent（Judge）综合各 Agent 意见，修正或确认回答
3. 持续辩论：当反方指出实质性问题时触发第二轮
4. 辩论历史持久化到 .learnings/debates.jsonl

设计:
- 所有 Agent 共享同一 Flash 客户端（串行调用不互斥）
- Judge 输出包含置信度修正和修正建议
- 辩论上下文自动注入到回答
"""

import json
import os
import time
import logging
from typing import Dict, List, Optional, Any
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from galaxyos.shared.paths import workspace

logger = logging.getLogger(__name__)

DEBATE_PATH = os.path.join(
    os.environ.get("WORKSPACE", workspace()),
    ".learnings", "debates.jsonl"
)


class DebateEngine:
    """多智能体辩论引擎"""

    def __init__(self, llm_flash=None, max_workers: int = 3):
        self.llm_flash = llm_flash
        self.pool = ThreadPoolExecutor(max_workers=max_workers)

    def debate(self, question: str, answer: str, cycle: int = 1, previous_criticism: str = "") -> Dict[str, Any]:
        """
        启动一轮辩论

        Args:
            question: 用户原始问题
            answer: 当前回答
            cycle: 辩论轮次（1 或 2）
            previous_criticism: 上一轮批评（cycle 2 时注入）

        Returns:
            {
                "verdict": "confirmed"|"needs_refine"|"conflict",
                "refined_answer": str or None,
                "confidence_delta": float (-0.3 ~ +0.2),
                "support_score": int (0-10),
                "criticism_score": int (0-10),
                "neutral_findings": str,
                "criticism": str,
                "support": str,
            }
        """
        result = {
            "verdict": "confirmed",
            "refined_answer": None,
            "confidence_delta": 0.0,
            "support_score": 0,
            "criticism_score": 0,
            "neutral_findings": "",
            "criticism": "",
            "support": "",
        }

        if not self.llm_flash or not answer:
            return result

        try:
            # ── 3 Agent 并行 ──
            futures = {}
            futures["positive"] = self.pool.submit(
                self._agent_positive, question, answer, cycle, previous_criticism
            )
            futures["negative"] = self.pool.submit(
                self._agent_negative, question, answer, cycle, previous_criticism
            )
            futures["neutral"] = self.pool.submit(
                self._agent_neutral, question, answer, cycle, previous_criticism
            )

            results = {}
            for name, fut in futures.items():
                try:
                    results[name] = fut.result(timeout=10)
                except Exception as e:
                    logger.warning(f"辩论 {name} agent 超时/失败: {e}")
                    results[name] = {"text": "", "issues": [], "score": 5}

            support_text = results.get("positive", {}).get("text", "")
            criticism_text = results.get("negative", {}).get("text", "")
            neutral_text = results.get("neutral", {}).get("text", "")

            support_score = results.get("positive", {}).get("score", 5)
            criticism_score = results.get("negative", {}).get("score", 5)
            issues = results.get("negative", {}).get("issues", [])
            neutral_findings = results.get("neutral", {}).get("findings", "")

            result["support"] = support_text[:300]
            result["criticism"] = criticism_text[:300]
            result["neutral_findings"] = neutral_findings[:300]
            result["support_score"] = support_score
            result["criticism_score"] = criticism_score

            # ── Judge 裁决 ──
            judge_result = self._judge(question, answer, support_text, criticism_text, neutral_findings)
            result["verdict"] = judge_result.get("verdict", "confirmed")
            result["confidence_delta"] = judge_result.get("confidence_delta", 0.0)
            result["refined_answer"] = judge_result.get("refined_answer")

            # ── 持久化 ──
            self._save(question, answer, results, judge_result)

        except Exception as e:
            logger.warning(f"辩论引擎异常: {e}")

        return result

    # ─────────────────── Agent 函数 ───────────────────

    def _agent_positive(self, question: str, answer: str, cycle: int, prev_criticism: str) -> Dict:
        """正面 Agent：论证回答正确性"""
        ctx = f"\n上一轮批评:\n{prev_criticism[:300]}" if prev_criticism else ""
        prompt = (
            f"你是一个严格的验证者。请评估以下AI回答的{'' if cycle == 1 else '修正后'}版本。\n\n"
            f"用户问题: {question[:300]}\n\n"
            f"AI回答:\n{answer[:1000]}\n\n"
            f"请进行:\n"
            f"1. [评估] 这个回答正确吗？证据充分吗？\n"
            f"2. [评分] 正确度 0-10（10=完全正确）\n"
            f"3. [理由] 列出支持理由\n"
            f"如果完全正确，回答以'确认正确'开头。"
            f"{ctx}"
        )
        try:
            rsp = self.llm_flash.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400, temperature=0.3,
            )
            text = rsp.choices[0].message.content.strip()
            score = 5
            m = __import__('re').search(r'(\d+)/?10|评分[：:]\s*(\d+)', text)
            if m:
                score = int(m.group(1) or m.group(2))
            return {"text": text, "score": min(10, max(0, score)), "issues": []}
        except Exception as e:
            return {"text": "", "score": 5, "issues": []}

    def _agent_negative(self, question: str, answer: str, cycle: int, prev_criticism: str) -> Dict:
        """反面 Agent：质疑回答的漏洞"""
        ctx = f"\n请重点检查上一轮指出的问题是否已修正:\n{prev_criticism[:400]}" if prev_criticism else ""
        prompt = (
            f"你是一个严格的批判者。请仔细审查以下AI回答。\n\n"
            f"用户问题: {question[:300]}\n\n"
            f"AI回答:\n{answer[:1000]}\n\n"
            f"请检查:\n"
            f"1. [事实错误] 有没有与常识/事实不符的地方？\n"
            f"2. [逻辑漏洞] 推理过程有没有跳跃或不一致？\n"
            f"3. [遗漏] 有没有关键信息被忽略？\n"
            f"4. [评分] 质疑严重度 0-10（10=严重问题）\n"
            f"5. [问题清单] 具体列出每个问题\n\n"
            f"如果确实没有问题，回答以'确认正确'开头。"
            f"{ctx}"
        )
        try:
            rsp = self.llm_flash.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500, temperature=0.4,
            )
            text = rsp.choices[0].message.content.strip()
            score = 0
            m = __import__('re').search(r'(\d+)/?10|质疑严重度[：:]\s*(\d+)', text)
            if m:
                score = int(m.group(1) or m.group(2))

            # 提取问题清单
            issues = []
            for line in text.split('\n'):
                if line.strip().startswith('-') or line.strip().startswith('*') or line.strip().startswith('·'):
                    issues.append(line.strip())
            if not issues and '问题' in text:
                issues = [text[:200]]

            return {"text": text, "score": min(10, max(0, score)), "issues": issues}
        except Exception as e:
            return {"text": "", "score": 0, "issues": []}

    def _agent_neutral(self, question: str, answer: str, cycle: int, prev_criticism: str) -> Dict:
        """中立 Agent：找出回答中可改善的地方"""
        prompt = (
            f"你是一个中立的质量分析师。请评估以下AI回答。\n\n"
            f"用户问题: {question[:300]}\n\n"
            f"AI回答:\n{answer[:1000]}\n\n"
            f"请提供:\n"
            f"1. [准确度] 事实性判断（正确/部分正确/有误）\n"
            f"2. [完整度] 是否覆盖了用户核心问题\n"
            f"3. [改善建议] 具体如何改进（如果有的话）\n"
            f"4. [关键发现] 最需要注意的一点是什么"
        )
        try:
            rsp = self.llm_flash.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=400, temperature=0.3,
            )
            text = rsp.choices[0].message.content.strip()
            return {"text": text, "findings": text[:300], "score": 5}
        except Exception as e:
            return {"text": "", "findings": "", "score": 5}

    def _judge(self, question: str, answer: str, support: str, criticism: str, neutral: str) -> Dict:
        """裁决 Agent：综合意见，输出最终裁决"""
        prompt = (
            f"你是一个最终仲裁者。以下是对AI回答的三方评估。\n\n"
            f"用户问题: {question[:200]}\n\n"
            f"AI回答:\n{answer[:500]}\n\n"
            f"[正方论证] {support[:400]}\n\n"
            f"[反方质疑] {criticism[:400]}\n\n"
            f"[中立分析] {neutral[:400]}\n\n"
            f"请裁决:\n"
            f"1. verdict: \"confirmed\"(直接确认) | \"needs_refine\"(需要修正) | \"conflict\"(存在争议)\n"
            f"2. confidence_delta: 对当前回答置信度的调整值 (-0.3 ~ +0.2)\n"
            f"3. refined_answer: 如果 verdict 不是 confirmed，给出修正后的回答（直接输出，否则空字符串）\n\n"
            f"返回JSON: {{\"verdict\":\"confirmed\",\"confidence_delta\":0.0,\"refined_answer\":\"\"}}"
        )
        try:
            rsp = self.llm_flash.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=600, temperature=0.15,
            )
            text = rsp.choices[0].message.content.strip()

            # 解析 JSON
            import re
            jm = re.search(r'\{[^}]+\}', text)
            if jm:
                try:
                    data = json.loads(jm.group())
                    return {
                        "verdict": data.get("verdict", "confirmed"),
                        "confidence_delta": max(-0.3, min(0.2, float(data.get("confidence_delta", 0)))),
                        "refined_answer": data.get("refined_answer", ""),
                    }
                except (json.JSONDecodeError, ValueError):
                    pass

            # fallback: 关键词判断
            if "确认正确" in text or "confirmed" in text:
                return {"verdict": "confirmed", "confidence_delta": 0.05, "refined_answer": ""}
            elif "refine" in text or "修正" in text:
                return {"verdict": "needs_refine", "confidence_delta": -0.1, "refined_answer": answer}
            else:
                return {"verdict": "conflict", "confidence_delta": -0.05, "refined_answer": ""}
        except Exception as e:
            logger.warning(f"裁决失败: {e}")
            return {"verdict": "confirmed", "confidence_delta": 0, "refined_answer": ""}

    def _save(self, question: str, answer: str, agent_results: Dict, judge_result: Dict):
        """持久化辩论记录"""
        try:
            entry = {
                "ts": time.time(),
                "question": question[:200],
                "answer": answer[:300],
                "positive_score": agent_results.get("positive", {}).get("score", 0),
                "negative_score": agent_results.get("negative", {}).get("score", 0),
                "verdict": judge_result.get("verdict", ""),
                "confidence_delta": judge_result.get("confidence_delta", 0),
            }
            os.makedirs(os.path.dirname(DEBATE_PATH), exist_ok=True)
            with open(DEBATE_PATH, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"保存辩论记录失败: {e}")


# ── 全局实例 ──
_instance = None

def get_debate_engine(llm_flash=None) -> DebateEngine:
    global _instance
    if _instance is None:
        _instance = DebateEngine(llm_flash)
    elif llm_flash and _instance.llm_flash is None:
        _instance.llm_flash = llm_flash
    return _instance


if __name__ == "__main__":
    de = DebateEngine()
    print("DebateEngine 加载成功 (3 Agent 并行辩论)")
