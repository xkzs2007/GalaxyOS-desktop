#!/usr/bin/env python3
"""
动态置信度校准 — 自适应评分模块 (Self-RAG / CRAG / Adaptive-RAG)

核心功能:
1. Self-RAG — 自反思评分，返回 (faithfulness, relevance, completeness)
2. CRAG — 纠错式检索，低分时触发补充检索 + 修正
3. Adaptive-RAG — 自适应路由：根据 query 复杂性选择 depth 1/2/3
4. 历史校准 — 根据历史评分分布动态调整阈值

设计原则:
- 不依赖 Pro（全部 Flash 搞定）
- 所有评分附带原始原始输出，方便调试
- 历史校准数据持久化到 .learnings/dynamic_confidence.jsonl
"""

import json
import os
import time
import re
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple, Any
from collections import defaultdict

logger = logging.getLogger(__name__)

CALIBRATION_PATH = os.path.join(
    os.environ.get("WORKSPACE", os.path.expanduser("~/.openclaw/workspace")),
    ".learnings", "dynamic_confidence.jsonl"
)

# ── 默认阈值（会被历史校准覆盖） ──
_DEFAULT_THRESHOLDS = {
    "high_confidence": 7.0,      # 三项平均 ≥ 7 → 直接回答
    "medium_confidence": 5.0,    # 5-7 → 触发 Self-Refine
    "low_confidence": 3.0,       # < 5 → 触发 CRAG 补充检索
    "faithfulness_critical": 5.0,  # faithful < 5 → 必须修正
}


class DynamicConfidence:
    """动态置信度校准引擎"""

    def __init__(self, llm_flash=None):
        self.llm_flash = llm_flash
        self.thresholds = dict(_DEFAULT_THRESHOLDS)
        self._history: List[Dict] = []  # 最近 200 条评分
        self._load_calibration()

    # ─────────────────── 对外接口 ───────────────────

    def judge(self, question: str, answer: str, extra_context: str = "") -> Dict[str, Any]:
        """
        Self-RAG 风格自评分

        Returns:
            {
                "faithfulness": float, "relevance": float, "completeness": float,
                "avg": float, "raw": str, "passed": bool, "trigger_crag": bool
            }
        """
        result = {"faithfulness": 5.0, "relevance": 5.0, "completeness": 5.0, "avg": 5.0,
                  "raw": "", "passed": True, "trigger_crag": False}

        if not self.llm_flash or not question or not answer:
            return result

        try:
            # ── 双维度评分: 忠实度 + 相关性/完整性 ──
            j_prompt = f"""请对以下AI回答进行质量评估，返回严格JSON。

用户问题: {question[:400]}

AI回答: {answer[:1200]}

请打分(1-10):
- "faithfulness": 回答是否忠实于知识库/事实，有无虚构或幻觉
- "relevance": 回答是否直接、完整地覆盖了用户问题
- "completeness": 回答是否提供了充分的细节和证据

评分规则：
  faithfulness: 完全基于事实无虚构=9-10，少量推测=7-8，部分虚构=5-6，明显幻觉=1-4
  relevance: 完全切题=9-10，部分相关=6-8，偏离=3-5，完全偏题=1-2
  completeness: 信息充分=9-10，基本完整=7-8，缺关键信息=5-6，信息不足=1-4
  extra_context: {extra_context[:300]}

返回格式JSON: {{"faithfulness":8,"relevance":7,"completeness":6}}"""

            rsp = self.llm_flash.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content": j_prompt}],
                max_tokens=200, temperature=0.1,
            )
            raw = rsp.choices[0].message.content.strip()
            result["raw"] = raw

            # 解析 JSON（容错）
            scores = {}
            jm = re.search(r'\{[^}]+\}', raw)
            if jm:
                try:
                    scores = json.loads(jm.group())
                except json.JSONDecodeError:
                    pass
            # fallback regex
            if not scores:
                _f = re.search(r'faithfulness["\s:]+(\d+)', raw)
                _r = re.search(r'relevance["\s:]+(\d+)', raw)
                _c = re.search(r'completeness["\s:]+(\d+)', raw)
                scores['faithfulness'] = int(_f.group(1)) if _f else 5
                scores['relevance'] = int(_r.group(1)) if _r else 5
                scores['completeness'] = int(_c.group(1)) if _c else 5

            result["faithfulness"] = min(max(float(scores.get("faithfulness", 5)), 1), 10)
            result["relevance"] = min(max(float(scores.get("relevance", 5)), 1), 10)
            result["completeness"] = min(max(float(scores.get("completeness", 5)), 1), 10)
            result["avg"] = round((result["faithfulness"] + result["relevance"] + result["completeness"]) / 3, 1)

            # ── 判断是否通过 ──
            thresholds = self.thresholds
            passed = (
                result["faithfulness"] >= thresholds["faithfulness_critical"]
                and result["avg"] >= thresholds["high_confidence"]
            )
            result["passed"] = passed
            result["trigger_crag"] = (
                result["faithfulness"] < thresholds["faithfulness_critical"]
                or result["avg"] < thresholds["medium_confidence"]
            )

            # 记录历史
            self._record(result["faithfulness"], result["relevance"], result["completeness"])

        except Exception as e:
            logger.warning(f"DynamicConfidence.judge 失败: {e}")

        return result

    def adaptive_depth(self, question: str) -> int:
        """
        Adaptive-RAG: 自适应路由深度

        Returns: 1 (简单) | 2 (中等) | 3 (复杂)
        """
        if not self.llm_flash:
            return 2  # 保守

        try:
            rsp = self.llm_flash.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content":
                    f"判断问题的复杂度，只返回数字(1/2/3):\n"
                    f"1=简单(问候/单事实查询/短回答即可)\n"
                    f"2=中等(需要检索+推理)\n"
                    f"3=复杂(多维度分析/对比/方案设计/需要多轮)\n\n"
                    f"问题: {question[:300]}"}],
                max_tokens=5, temperature=0.1,
            )
            text = rsp.choices[0].message.content.strip()
            d = int(re.search(r'\d', text).group()) if re.search(r'\d', text) else 2
            return max(1, min(3, d))
        except Exception:
            return 2

    def crag_correction(self, question: str, answer: str, memories: List[Dict]) -> Optional[str]:
        """
        CRAG: 低分时用补充资料修正回答

        Returns: 修正后的回答 or None
        """
        if not self.llm_flash or not memories:
            return None

        ctx = "\n".join(
            f"[{m.get('source','?')}] {m.get('content','')[:400]}"
            for m in memories[:6]
        )
        if not ctx.strip():
            return None

        try:
            rsp = self.llm_flash.chat.completions.create(
                model="deepseek-v4-flash",
                messages=[{"role": "user", "content":
                    f"基于参考资料修正以下回答中的不准确之处。\n\n"
                    f"问题: {question[:300]}\n\n"
                    f"当前回答:\n{answer[:1000]}\n\n"
                    f"参考资料:\n{ctx[:3000]}\n\n"
                    f"修正后的回答(忠实于参考资料,直接输出修正结果):"}],
                max_tokens=1500, temperature=0.2,
            )
            corrected = rsp.choices[0].message.content.strip()
            if corrected and len(corrected) > 20 and corrected != answer.strip():
                return corrected
        except Exception as e:
            logger.warning(f"CRAG 修正失败: {e}")

        return None

    def get_calibrated_threshold(self, key: str, default: float = 7.0) -> float:
        """获取校准后的阈值"""
        return self.thresholds.get(key, default)

    # ─────────────────── 内部 ───────────────────

    def _record(self, f: float, r: float, c: float):
        """记录评分到历史"""
        entry = {
            "ts": time.time(),
            "faithfulness": f, "relevance": r, "completeness": c,
            "avg": round((f + r + c) / 3, 1),
        }
        self._history.append(entry)
        # 只保留 200 条
        if len(self._history) > 200:
            self._history = self._history[-200:]
            self._recalibrate()

    def _recalibrate(self):
        """基于历史分布重新校准阈值"""
        if len(self._history) < 20:
            return

        scores = [e["avg"] for e in self._history]
        avg_s = sum(scores) / len(scores)
        std_s = (sum((s - avg_s) ** 2 for s in scores) / len(scores)) ** 0.5

        # 动态阈值：平均分 - 0.5 * 标准差
        self.thresholds["high_confidence"] = round(max(5.0, avg_s - 0.3 * std_s), 1)
        self.thresholds["medium_confidence"] = round(max(3.0, avg_s - 0.8 * std_s), 1)
        self.thresholds["faithfulness_critical"] = round(max(4.0, avg_s - 0.6 * std_s - 1.0), 1)

        self._save_calibration()

    def _load_calibration(self):
        """加载历史校准数据"""
        if not os.path.exists(CALIBRATION_PATH):
            return
        try:
            with open(CALIBRATION_PATH, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            self._history.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
            if len(self._history) >= 20:
                self._recalibrate()
        except Exception as e:
            logger.warning(f"加载校准数据失败: {e}")

    def _save_calibration(self):
        """持久化校准数据"""
        try:
            os.makedirs(os.path.dirname(CALIBRATION_PATH), exist_ok=True)
            # 只保存最近 100 条
            with open(CALIBRATION_PATH, "w") as f:
                for entry in self._history[-100:]:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"保存校准数据失败: {e}")


# ── 全局实例 ──
_instance = None

def get_dynamic_confidence(llm_flash=None) -> DynamicConfidence:
    global _instance
    if _instance is None:
        _instance = DynamicConfidence(llm_flash)
    elif llm_flash and _instance.llm_flash is None:
        _instance.llm_flash = llm_flash
    return _instance


if __name__ == "__main__":
    dc = DynamicConfidence()
    print(f"默认阈值: {dc.thresholds}")
    print(f"校准后的 high_confidence: {dc.get_calibrated_threshold('high_confidence')}")
    print("模块加载成功")
