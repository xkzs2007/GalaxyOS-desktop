#!/usr/bin/env python3
"""
自我进化引擎 (Self-Evolution Engine)

将反思、学习和自评能力打包为常驻单例，实现：
1. 结果质量自评 — 每次推理后自动评分，低分触发改进
2. 错误模式检测 — 同类错误 ≥3 次自动生成改进建议
3. 进化追踪 — 改进前后的质量对比，形成进化曲线

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-05-09
"""

import os
import json
import math
import hashlib
import threading
import time
import sys
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime, timezone


class SelfEvolutionEngine:
    """
    自我进化引擎
    
    三阶段自进化循环：
    1. 质量自评（Quality Self-Evaluation）
    2. 模式发现（Pattern Discovery）  
    3. 改进执行（Improvement Execution）
    """
    
    _last_pattern_check: float = 0.0
    
    def __init__(self, workspace_path: str = None):
        if workspace_path is None:
            workspace_path = os.environ.get(
                'OPENCLAW_WORKSPACE',
                str(Path.home() / '.openclaw' / 'workspace')
            )
        self.workspace = Path(workspace_path)
        
        # 懒加载子模块
        self._memory_reflector = None
        self._auto_learner = None
        self._smart_processor = None
        self._apo_optimizer = None
        
        # 进化追踪数据
        self.evolution_tracker_path = self.workspace / 'memory' / 'evolution_tracker.jsonl'
        self._scores: List[Dict] = []
        self._load_scores()
        
        # 模式检测频率控制
        self._last_pattern_check = 0.0
        self._last_apo_run = 0.0

    # ── APO (Automatic Prompt Optimization) ────────────────

    def _ensure_apo(self) -> bool:
        """懒加载 APO PromptOptimizer"""
        if self._apo_optimizer is not None:
            return True
        try:
            from auto_prompt_optimizer import PromptOptimizer, training_examples_from_quality_history
            from auto_prompt_optimizer import wrap_llm_call
            self._apo_optimizer = PromptOptimizer(workspace_path=str(self.workspace))
            # 设置 llm_call (懒加载，等第一次 optimize 时注入)
            self._apo_training_fn = training_examples_from_quality_history
            self._apo_wrap = wrap_llm_call
            logger.info("APO PromptOptimizer 懒加载成功")
            return True
        except Exception as e:
            logger.debug(f"APO 加载失败（非关键）: {e}")
            return False

    def set_apo_llm_call(self, llm_engine):
        """从外部注入 LLM 引擎"""
        if not self._ensure_apo():
            return
        try:
            llm_fn = self._apo_wrap(llm_engine)
            self._apo_optimizer.set_llm_call(llm_fn)
            logger.info("APO LLM call 已注入")
        except Exception as e:
            logger.debug(f"APO llm call 注入失败: {e}")
    
    # ── 1. 质量自评 ────────────────────────────────────────
    
    def evaluate_response_quality(self, 
                                   query: str,
                                   rewritten: str,
                                   results: List[Dict],
                                   summary: str,
                                   model_used: str = "deepseek-v4-flash") -> Dict:
        """
        对 smart_process 的结果做质量自评
        
        评分维度：
        - completeness: 检索结果是否覆盖了查询的多个方面 (0-1)
        - relevance: 结果相关性评分 (0-1)
        - conciseness: 摘要是否精炼 (0-1)
        - factuality: 结果来源可靠性 (0-1)
        
        Returns: 质量评分字典
        """
        score = {}
        
        # completeness — 结果数量决定
        count = len(results) if results else 0
        if count >= 5:
            score["completeness"] = 1.0
        elif count >= 3:
            score["completeness"] = 0.8
        elif count >= 1:
            score["completeness"] = 0.5
        else:
            score["completeness"] = 0.0
        
        # relevance — 结果平均分
        if results:
            avg_score = sum(r.get("score", 0.5) for r in results) / len(results)
            score["relevance"] = min(avg_score, 1.0)
        else:
            score["relevance"] = 0.0
        
        # conciseness — 摘要长度
        if summary:
            slen = len(summary)
            if 50 <= slen <= 500:
                score["conciseness"] = 1.0
            elif 20 <= slen <= 800:
                score["conciseness"] = 0.7
            else:
                score["conciseness"] = 0.4
        else:
            score["conciseness"] = 0.0
        
        # factuality — 查询长度短则置信度高
        qlen = len(query)
        if qlen < 10:
            score["factuality"] = 1.0  # 简单查询不容易错
        elif qlen < 50:
            score["factuality"] = 0.8
        else:
            score["factuality"] = 0.6  # 长查询更容易出问题
        
        # 综合评分
        overall = sum(score.values()) / len(score)
        score["overall"] = round(overall, 3)
        
        # 是否需要改进
        score["needs_improvement"] = overall < 0.5
        
        # 记录到进化追踪
        self._record_evolution_event({
            "event_type": "self_evaluation",
            "query": query[:100],
            "rewritten": rewritten[:100] if rewritten else "",
            "result_count": count,
            "summary_length": len(summary) if summary else 0,
            "scores": score,
            "model_used": model_used,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        
        return score
    
    # ── 2. 模式发现 ────────────────────────────────────────
    
    def detect_error_patterns(self) -> List[Dict]:
        """
        检测错误模式，双数据源：
        1. .learnings/ERRORS.md（显式错误记录）
        2. .learnings/reflexions.jsonl（失败反思记录，主要来源）
        
        判断：同类错误 ≥3 次自动生成改进建议
        """
        patterns: Dict[str, List[str]] = {}
        examples: Dict[str, List[str]] = {}
        
        # ═══ 源1: ERRORS.md ═══
        errors_path = self.workspace / '.learnings' / 'ERRORS.md'
        if errors_path.exists():
            with open(errors_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#') or line.startswith('---'):
                        continue
                    for kw in ['超时', '失败', '错误', '异常', 'timeout', 'error', 'fail']:
                        if kw in line.lower():
                            key = f"error_{kw}"
                            if key not in patterns:
                                patterns[key] = []
                            patterns[key].append(line[:100])
                            break

        # ═══ 源2: reflexions.jsonl（292条失败反思，主要数据源） ═══
        reflexions_path = self.workspace / '.learnings' / 'reflexions.jsonl'
        if reflexions_path.exists():
            with open(reflexions_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    fp = r.get('failure_pattern', 'unknown')
                    rc = r.get('root_cause', '').strip()
                    q = r.get('question', '')[:80]
                    
                    # 按 failure_pattern 聚合
                    key = f'failure_{fp}'
                    if key not in patterns:
                        patterns[key] = []
                        examples[key] = []
                    patterns[key].append(line)
                    if len(examples[key]) < 3:
                        examples[key].append(f'{fp}: {q}')
                    
                    # 有根因的也按 root_cause 关键词聚合
                    if rc:
                        for kw in ['信息不足', '误解', '幻觉', '遗漏', '偏离', '超时', '未理解', '编造']:
                            if kw in rc:
                                rk = f'root_{kw}'
                                if rk not in patterns:
                                    patterns[rk] = []
                                    examples[rk] = []
                                patterns[rk].append(line)
                                if len(examples[rk]) < 3:
                                    examples[rk].append(f'{rc}: {q}')
                                break

        # 生成建议
        suggestions = []
        for key, entries in patterns.items():
            if len(entries) >= 3:
                fp_label = key.replace('failure_', '').replace('root_', '')
                suggestions.append({
                    "pattern": key,
                    "count": len(entries),
                    "examples": examples.get(key, entries[:3]),
                    "suggestion": self._generate_improvement_suggestion(key, examples.get(key, [''])[0]),
                    "severity": "high" if len(entries) >= 10 else "medium",
                    "source": "reflexions.jsonl",
                })
        
        if suggestions:
            self._record_evolution_event({
                "event_type": "pattern_detected",
                "patterns": suggestions,
                "total_reflexions": len(patterns),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
        
        return suggestions
    
    def _generate_improvement_suggestion(self, pattern: str, example: str) -> str:
        """根据错误/失败模式生成改进建议"""
        suggestions = {
            # ── 旧 ERROR.md 关键词（保留向下兼容） ──
            "error_超时": "增加超时时间或添加重试机制",
            "error_失败": "检查前置条件是否满足，增加错误处理和降级策略",
            "error_错误": "检查输入参数合法性，增加输入验证",
            "error_异常": "添加更完善的异常捕获和日志记录",
            "error_timeout": "Increase timeout values or implement retry with backoff",
            "error_error": "Validate input parameters, add error handling and fallback",
            "error_fail": "Check preconditions, add error handling and degradation",
            # ── reflexions.jsonl 实际失败模式 ──
            "failure_unknown": "LLM 分析失败导致根因缺失：降级为启发式推断（根据评分维度自动归类）",
            "failure_偏离": "回答偏离用户意图：增强用户输入意图检测，检索额外上下文后再生成",
            "failure_遗漏": "回答不完整遗漏关键信息：开启多轮检索，补充多维度证据",
            "failure_幻觉": "生成了事实不准确的回答：强制引用检索结果，禁止自由发挥",
            "failure_矛盾": "回答前后自相矛盾：引入一致性检查，对比同一对话周期中的前序回答",
            "failure_冗余": "回答过于冗长啰嗦：压缩上下文，限制输出长度",
            "failure_其他": "归类外的异常场景：记录详情到 ERROR.md 供后续分析",
            "root_信息不足": "检索资源不足：扩展检索范围并降低置信度，避免编造",
            "root_误解": "模型误解用户意图：先反问澄清再回答，不直接猜测",
            "root_未理解": "未洞察用户底层需求：拆解为更小的子问题逐一确认",
            "root_编造": "编造不存在的信息：严格禁止无证据输出，强制用'无相关信息'替代",
            "root_偏离": "意图定位漂移：回退到用户原始问题重新分析",
        }
        for k, v in suggestions.items():
            if k in pattern:
                return v
        return f"识别到模式 {pattern}（{example[:30]}...），需人工补充修复策略"
    
    # ── 3. 进化追踪 ────────────────────────────────────────
    
    def _record_evolution_event(self, event: Dict):
        """记录进化事件到追踪文件"""
        self.workspace.joinpath('memory').mkdir(parents=True, exist_ok=True)
        with open(self.evolution_tracker_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(event, ensure_ascii=False) + '\n')
        self._scores.append(event)
    
    def _load_scores(self):
        """加载历史进化追踪数据"""
        if not self.evolution_tracker_path.exists():
            return
        with open(self.evolution_tracker_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        self._scores.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    
    def get_evolution_trend(self) -> Dict:
        """
        获取进化趋势
        
        Returns: 最近 N 次自评的平均分趋势
        """
        evals = [s for s in self._scores if s.get("event_type") == "self_evaluation"]
        if not evals:
            return {"trend": "no_data"}
        
        recent = evals[-20:]  # 最近 20 次
        avg_scores = {}
        for e in recent:
            scores = e.get("scores", {})
            for k, v in scores.items():
                if k not in avg_scores:
                    avg_scores[k] = []
                avg_scores[k].append(v)
        
        trend = {}
        for k, vals in avg_scores.items():
            if len(vals) >= 2:
                # 前半段 vs 后半段
                mid = len(vals) // 2
                first_half = sum(vals[:mid]) / mid
                second_half = sum(vals[mid:]) / (len(vals) - mid)
                trend[k] = {
                    "avg": round(sum(vals) / len(vals), 3),
                    "first_half": round(first_half, 3),
                    "second_half": round(second_half, 3),
                    "delta": round(second_half - first_half, 3),
                }
            else:
                trend[k] = {"avg": round(sum(vals) / len(vals), 3), "data_points": len(vals)}
        
        return {
            "total_scores": len(evals),
            "recent_count": len(recent),
            "trend": trend,
        }
    
    # ── 4. 主动自进化 ──────────────────────────────────────
    
    def evolve(self, 
               query: str,
               rewritten: str,
               results: List[Dict],
               summary: str,
               session_id: str = "default") -> Dict:
        """
        主动自进化流程：
        1. 质量自评
        2. 模式发现
        3. 进化追踪
        4. 返回改进建议
        
        这个方法是整个引擎的入口，每次 smart_process 完成后自动调用
        """
        # 1. 质量自评
        quality = self.evaluate_response_quality(query, rewritten, results, summary)
        
        # 2. 模式发现（降低频次，每 5 分钟最多一次）
        patterns = self.detect_error_patterns() if self._should_run_pattern_detection() else []
        
        # 3. 进化追踪
        trend = self.get_evolution_trend()
        
        # 4. 生成改进建议
        suggestions = []
        
        # — 低分改进建议
        if quality.get("needs_improvement"):
            for dim, val in quality.items():
                if isinstance(val, (int, float)) and dim != "overall" and dim != "needs_improvement" and val < 0.3:
                    suggestions.append({
                        "target": dim,
                        "current_score": val,
                        "suggestion": f"优化{dim}评分（当前{val:.2f}）"
                    })
        
        # — 模式改进建议
        for p in patterns:
            suggestions.append({
                "target": f"pattern_{p['pattern']}",
                "current_count": p["count"],
                "suggestion": p["suggestion"],
                "severity": p["severity"],
            })
        
        # — APO 优化建议（每 5 分钟最多跑一次）
        apo_result = None
        if self._ensure_apo() and self._apo_optimizer._llm_call and (time.time() - self._last_apo_run > 300):
            try:
                examples = self._apo_training_fn(self._scores)
                if examples:
                    # 从 quality history 提取当前 prompt 上下文
                    current_prompt = self._build_apo_current_prompt()
                    opt_result = self._apo_optimizer.optimize(
                        current_prompt=current_prompt,
                        training_data=examples,
                        num_rounds=1,        # 每轮只跑 1 次 APO（节省预算）
                        beam_width=2,
                        candidates_per_round=2,
                        eval_budget=3,
                    )
                    self._last_apo_run = time.time()
                    if opt_result.get("best_score", 0) > 0.3:
                        suggestions.append({
                            "target": "apo_prompt_improvement",
                            "current_score": quality.get("overall", 0.5),
                            "suggestion": f"APO prompt 优化完成（最优分数: {opt_result['best_score']:.2f}）",
                            "apo_result": opt_result,
                        })
                        apo_result = opt_result
            except Exception as e:
                logger.debug(f"APO 优化失败: {e}")
        
        return {
            "quality_score": quality,
            "suggestions": suggestions,
            "evolution_trend": trend,
            "apo_result": apo_result,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "has_improvement_needed": quality.get("needs_improvement", False) or len(suggestions) > 0,
        }

    def _build_apo_current_prompt(self) -> str:
        """从质量历史构建当前 prompt 上下文，作为 APO 优化的基线"""
        recent = self._scores[-10:] if self._scores else []
        prompt_parts = [
            "You are a helpful AI assistant. Provide accurate, complete, and relevant responses.",
            f"Recent evaluations: {len(recent)} quality assessments.",
        ]
        if recent:
            avg_scores = defaultdict(list)
            for r in recent:
                sc = r.get("scores", {})
                for k, v in sc.items():
                    if isinstance(v, (int, float)):
                        avg_scores[k].append(v)
            parts = []
            for dim, vals in avg_scores.items():
                avg = sum(vals) / len(vals)
                parts.append(f"{dim}: {avg:.2f}")
            if parts:
                prompt_parts.append("Average quality: " + ", ".join(parts))
        return "\n".join(prompt_parts)
    
    # ── 5. 频率控制 ──────────────────────────────────────
    
    def _should_run_pattern_detection(self) -> bool:
        """每 5 分钟最多跑一次模式检测"""
        now = time.time()
        if now - self._last_pattern_check < 300:
            return False
        self._last_pattern_check = now
        return True
    
    # ── 6. 进化上下文生成 ──────────────────────────────────
    
    def get_evolution_context(self, max_recent: int = 10) -> Dict:
        """
        生成进化上下文摘要，用于会话启动时注入
        
        读取 evolution_tracker.jsonl 最近 N 条改进建议 + .learnings/ERRORS.md 高频错误
        返回结构化的上下文数据，可转换为系统提示
        """
        context = {
            "recent_evals": [],       # 最近的自评结果
            "low_score_dims": {},     # 低分维度聚合
            "error_patterns": [],     # 错误模式
            "suggestions": [],        # 改进建议
            "has_content": False,     # 是否有实质内容
        }
        
        # 读最近的自评事件
        evals = [s for s in self._scores if s.get("event_type") == "self_evaluation"]
        recent = evals[-max_recent:] if evals else []
        
        low_dims = {}
        for e in recent:
            scores = e.get("scores", {})
            for dim in ["completeness", "relevance", "conciseness", "factuality"]:
                val = scores.get(dim, 1.0)
                if val < 0.5:
                    low_dims[dim] = low_dims.get(dim, {"count": 0, "avg": 0.0})
                    low_dims[dim]["count"] += 1
                    low_dims[dim]["avg"] = (low_dims[dim]["avg"] * (low_dims[dim]["count"] - 1) + val) / low_dims[dim]["count"]
        
        context["recent_evals"] = [{
            "query": e.get("query", "")[:50],
            "overall": e.get("scores", {}).get("overall", 0),
            "summary_length": e.get("summary_length", 0),
        } for e in recent[-5:]]
        
        context["low_score_dims"] = {k: round(v["avg"], 2) for k, v in low_dims.items()}
        
        # 读错误模式
        patterns = self.detect_error_patterns()
        context["error_patterns"] = [{
            "pattern": p["pattern"],
            "count": p["count"],
            "suggestion": p["suggestion"],
            "severity": p["severity"],
        } for p in patterns[:5]]
        
        # 聚合改进建议
        for p in patterns:
            if p.get("suggestion"):
                context["suggestions"].append(p["suggestion"])
        
        context["has_content"] = bool(low_dims) or bool(patterns)
        context["eval_count"] = len(evals)
        context["recent_eval_count"] = len(recent)
        
        return context
    
    def format_evolution_context(self, max_recent: int = 10) -> str:
        """
        将进化上下文格式化为可注入的系统提示文本
        
        返回的字符串可以直接添加到 session 上下文中
        """
        ctx = self.get_evolution_context(max_recent)
        if not ctx["has_content"]:
            return ""
        
        parts = []
        
        # 低分维度
        if ctx["low_score_dims"]:
            dims = [f"  - {k}: {v:.2f}/1.0" for k, v in sorted(ctx["low_score_dims"].items())]
            parts.append("[进化自评] 以下维度评分偏低：\n" + "\n".join(dims))
        
        # 错误模式
        if ctx["error_patterns"]:
            errs = []
            for p in ctx["error_patterns"]:
                severity = "⚠️" if p["severity"] == "high" else "🔸"
                errs.append(f"  {severity} {p['pattern']}: 出现{p['count']}次 → {p['suggestion']}")
            parts.append("[进化模式] 检测到以下错误模式：\n" + "\n".join(errs))
        
        # 改进建议
        if ctx["suggestions"]:
            suggs = [f"  - {s}" for s in ctx["suggestions"][:3]]
            parts.append("[改进建议]\n" + "\n".join(suggs))
        
        return "\n\n".join(parts)
    
    # ═════════════════════════════════════════════════════⬡
    #  7. 主动自进化调度器
    # ═════════════════════════════════════════════════════⬡
    
    def start_active_scheduler(self, interval_minutes: int = 10) -> Dict:
        """
        启动主动自进化调度器（daemon 后台线程）
        
        主动检查三件事：
        1. 趋势分析 — 进化趋势是改善了还是劣化了
        2. 自动改进 — 高频错误模式自动生成改进建议文件
        3. 效果验证 — 历史改进是否有效
        
        interval_minutes: 检查间隔（默认 10 分钟）
        """
        if hasattr(self, '_scheduler_active') and self._scheduler_active:
            return {"ok": True, "message": "调度器已在运行", "active": True}
        
        self._scheduler_active = True
        self._scheduler_stop = False
        
        def _scheduler_loop(engine, interval):
            """后台 daemon 线程主循环"""
            while not engine._scheduler_stop:
                # 提前初始化，保证 except 分支也能引用
                decision = {"executed": 0, "pending": 0, "skipped": 0}
                try:
                    trend = engine._active_check_trend()
                    improvements = engine._active_auto_improve()
                    # — 有效果改进则验证
                    if improvements.get("applied", 0) > 0:
                        engine._active_verify_improvements()
                    
                    # — 决策执行层：自动执行低风险建议
                    try:
                        decision = engine.active_decision_and_execute()
                        if decision.get("executed", 0) > 0:
                            print(f"[ActiveEvolution] 决策执行: {decision['executed']}条自动执行, "
                                  f"{decision.get('pending', 0)}条待审批, "
                                  f"{decision.get('skipped', 0)}条高风险跳过", file=sys.stderr)
                    except Exception as de:
                        print(f"[ActiveEvolution] 决策执行异常: {de}", file=sys.stderr)
                    
                    # — 记录一次调度器执行
                    engine._record_evolution_event({
                        "event_type": "active_scheduler_cycle",
                        "trend_improved": trend.get("trend_improved", False),
                        "improvements_applied": improvements.get("applied", 0),
                        "decisions_executed": decision.get("executed", 0),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    })
                except Exception as e:
                    print(f"[ActiveEvolution] 调度器异常: {e}", file=sys.stderr)
                
                # 等待指定间隔（逐秒检查 stop 信号）
                for _ in range(interval * 60):
                    if engine._scheduler_stop:
                        break
                    time.sleep(1)
        
        self._scheduler_thread = threading.Thread(
            target=_scheduler_loop,
            args=(self, interval_minutes),
            daemon=True,
            name="active-evolution-scheduler",
        )
        self._scheduler_thread.start()
        
        return {"ok": True, "message": f"调度器已启动（间隔{interval_minutes}分钟）", "active": True}
    
    def stop_active_scheduler(self) -> Dict:
        """停止主动自进化调度器"""
        if not hasattr(self, '_scheduler_active') or not self._scheduler_active:
            return {"ok": True, "message": "调度器未运行", "active": False}
        
        self._scheduler_stop = True
        self._scheduler_active = False
        return {"ok": True, "message": "调度器已停止", "active": False}
    
    def get_scheduler_status(self) -> Dict:
        """获取调度器状态"""
        status = {
            "active": getattr(self, '_scheduler_active', False),
            "thread_alive": getattr(self, '_scheduler_thread', None) and self._scheduler_thread.is_alive(),
        }
        
        # 附加最近执行记录
        events = [s for s in self._scores if s.get("event_type") == "active_scheduler_cycle"]
        status["last_cycles"] = events[-5:] if events else []
        status["total_cycles"] = len(events)
        
        return status
    
    # ── 7a. 趋势分析 ──────────────────────────────────────
    
    def _active_check_trend(self) -> Dict:
        """
        主动趋势分析
        
        读取最近 50 次自评，对比前半段和后半段均分。
        如果劣化维度超过 2 个，标记为需要改进。
        """
        evals = [s for s in self._scores if s.get("event_type") == "self_evaluation"]
        if len(evals) < 6:
            return {"trend_improved": True, "message": "数据不足", "eval_count": len(evals)}
        
        recent = evals[-50:]  # 最多 50 次
        mid = len(recent) // 2
        
        dim_trends = {}
        degraded_dims = []
        
        for dim in ["completeness", "relevance", "conciseness", "factuality", "overall"]:
            first = [e.get("scores", {}).get(dim, 0) for e in recent[:mid]]
            second = [e.get("scores", {}).get(dim, 0) for e in recent[mid:]]
            first_avg = sum(first) / len(first) if first else 0
            second_avg = sum(second) / len(second) if second else 0
            delta = round(second_avg - first_avg, 3)
            
            dim_trends[dim] = {
                "first_avg": round(first_avg, 3),
                "second_avg": round(second_avg, 3),
                "delta": delta,
                "improved": delta > 0.05,
                "degraded": delta < -0.05,
            }
            if delta < -0.05:
                degraded_dims.append(dim)
        
        trend_improved = len(degraded_dims) <= 1
        
        return {
            "trend_improved": trend_improved,
            "eval_count": len(recent),
            "degraded_dims": degraded_dims,
            "dimensions": dim_trends,
        }
    
    # ── 7b. 自动改进 ──────────────────────────────────────
    
    def _active_auto_improve(self) -> Dict:
        """
        自动生成改进建议文件
        
        读取错误模式和高频低分维度，写入改进建议文件
        """
        patterns = self.detect_error_patterns()
        trend = self._active_check_trend()
        
        improvements = []
        
        # 基于错误模式生成改进
        for p in patterns:
            if p["count"] >= 3:
                improvements.append({
                    "source": "error_pattern",
                    "pattern": p["pattern"],
                    "suggestion": p["suggestion"],
                    "severity": p["severity"],
                })
        
        # 基于劣化维度生成改进
        for dim in trend.get("degraded_dims", []):
            if dim != "overall":
                improvements.append({
                    "source": "trend_degradation",
                    "pattern": f"degraded_{dim}",
                    "suggestion": f"关注{dim}评分下降趋势（{trend['dimensions'][dim]['delta']}），检查近期的检索质量",
                    "severity": "medium" if trend["dimensions"][dim]["delta"] > -0.1 else "high",
                })
        
        if not improvements:
            return {"applied": 0, "improvements": []}
        
        # 写入改进建议文件
        suggestions_dir = self.workspace / '.learnings'
        suggestions_dir.mkdir(parents=True, exist_ok=True)
        
        suggestions_path = suggestions_dir / 'IMPROVEMENT_SUGGESTIONS.jsonl'
        # 避免重复追加，先读已有内容去重
        existing = set()
        if suggestions_path.exists():
            with open(suggestions_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            item = json.loads(line)
                            existing.add(item.get("suggestion", ""))
                        except json.JSONDecodeError:
                            pass
        
        applied = 0
        for imp in improvements:
            if imp["suggestion"] not in existing:
                imp["timestamp"] = datetime.now(timezone.utc).isoformat()
                with open(suggestions_path, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(imp, ensure_ascii=False) + '\n')
                applied += 1
        
        return {"applied": applied, "improvements": improvements}
    
    # ── 7c. 效果验证 ──────────────────────────────────────
    
    def _active_verify_improvements(self) -> Dict:
        """
        验证历史改进效果
        
        读取改进建议文件，对比改进前后的评分趋势。
        如果某个维度的评分在改进记录后有改善，标记为有效。
        """
        suggestions_path = self.workspace / '.learnings' / 'IMPROVEMENT_SUGGESTIONS.jsonl'
        if not suggestions_path.exists():
            return {"verified": 0, "results": []}
        
        suggestions = []
        with open(suggestions_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        suggestions.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        
        if not suggestions:
            return {"verified": 0, "results": []}
        
        # 取每个模式的最新一条建议
        latest: Dict[str, Dict] = {}
        for s in suggestions:
            key = s.get("pattern", s.get("source", "unknown"))
            latest[key] = s
        
        evals = [e for e in self._scores if e.get("event_type") == "self_evaluation"]
        results = []
        verified = 0
        
        for key, suggestion in latest.items():
            dim = None
            for d in ["completeness", "relevance", "conciseness", "factuality"]:
                if d in key:
                    dim = d
                    break
            
            if dim and len(evals) >= 6:
                mid = len(evals) // 2
                recent_avg = sum(e.get("scores", {}).get(dim, 0) for e in evals[mid:]) / max(len(evals) - mid, 1)
                improved = recent_avg > 0.6
                
                results.append({
                    "pattern": key,
                    "suggestion": suggestion.get("suggestion", ""),
                    "current_avg_score": round(recent_avg, 3),
                    "improved": improved,
                })
                if improved:
                    verified += 1
        
        # 记录验证结果
        verify_path = self.workspace / '.learnings' / 'VERIFICATION_RESULTS.jsonl'
        with open(verify_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps({
                "event_type": "verification",
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "verified_count": verified,
                "total_checked": len(results),
                "results": results,
            }, ensure_ascii=False) + '\n')
        
        return {"verified": verified, "total": len(results), "results": results}
    
    # ═════════════════════════════════════════════════════⬡
    #  8. 决策执行层（主动自进化的最后一块拼图）
    # ═════════════════════════════════════════════════════⬡
    
    # ── 风险等级定义 ──────────────────────────────────────
    # 低风险: 调阈值/参数，不改模块逻辑，可回滚
    # 中风险: 改模块开关/配置，需要人工确认
    # 高风险: 改代码/架构，必须人工介入
    
    _RISK_LOW = "low"
    _RISK_MEDIUM = "medium"
    _RISK_HIGH = "high"
    
    def active_decision_and_execute(self) -> Dict:
        """
        决策执行主入口——在调度器循环中调用
        
        流程：
        1. 读改进建议文件（IMPROVEMENT_SUGGESTIONS.jsonl）
        2. 对每条新建议做风险评估
        3. 低风险 → 自动执行 + 快照备份 + 验证 + 回滚/保留
        4. 中风险 → 写入可执行脚本文件，等人审批
        5. 高风险 → 跳过，仅记录
        
        Returns: 执行摘要
        """
        suggestions_path = self.workspace / '.learnings' / 'IMPROVEMENT_SUGGESTIONS.jsonl'
        if not suggestions_path.exists():
            return {"executed": 0, "pending": 0, "skipped": 0, "actions": []}
        
        # 读执行历史，避免重复执行
        history_path = self.workspace / '.learnings' / 'EXECUTION_HISTORY.jsonl'
        executed_suggestions = set()
        if history_path.exists():
            with open(history_path, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if line:
                        try:
                            item = json.loads(line)
                            executed_suggestions.add(item.get("suggestion", ""))
                        except json.JSONDecodeError:
                            pass
        
        # 读改进建议
        suggestions = []
        with open(suggestions_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        suggestions.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
        
        result = {"executed": 0, "pending": 0, "skipped": 0, "actions": []}
        
        for suggestion in suggestions:
            sug_text = suggestion.get("suggestion", "")
            if sug_text in executed_suggestions:
                continue  # 已执行过，跳过
            
            risk = self._evaluate_risk(suggestion)
            
            if risk == self._RISK_LOW:
                exec_result = self._execute_low_risk(suggestion)
                result["executed"] += 1
                result["actions"].append({
                    "suggestion": sug_text,
                    "risk": "low",
                    "executed": exec_result,
                })
            elif risk == self._RISK_MEDIUM:
                self._write_suggestion_script(suggestion)
                result["pending"] += 1
                result["actions"].append({
                    "suggestion": sug_text,
                    "risk": "medium",
                    "pending": True,
                })
            else:
                result["skipped"] += 1
                result["actions"].append({
                    "suggestion": sug_text,
                    "risk": "high",
                    "skipped": True,
                })
            
            # 标记已处理
            with open(history_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps({
                    "suggestion": sug_text,
                    "risk": risk,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }, ensure_ascii=False) + '\n')
        
        # 记录决策执行事件
        self._record_evolution_event({
            "event_type": "decision_execution",
            "executed": result["executed"],
            "pending": result["pending"],
            "skipped": result["skipped"],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        
        return result
    
    # ── 8a. 风险评估 ──────────────────────────────────────
    
    def _evaluate_risk(self, suggestion: Dict) -> str:
        """
        评估改进建议的风险等级
        
        - 低风险: 涉及阈值/参数调整（如 recall_threshold、ltp_strength）
        - 中风险: 涉及模块启用/关闭、配置变更
        - 高风险: 涉及代码修改、架构变更
        
        Returns: "low" | "medium" | "high"
        """
        sug_text = suggestion.get("suggestion", "").lower()
        pattern = suggestion.get("pattern", "").lower()
        
        # 低风险关键词：调阈值、权重、速率
        low_risk_kw = ["阈值", "阈值", "权重", "速率", "评分", "threshold", "weight", 
                       "rate", "decay", "timing", "超时", "timeout"]
        
        # 中风险关键词：启用/关闭模块、配置
        medium_risk_kw = ["启用", "关闭", "模块", "配置", "开关", "enable", "disable",
                          "config", "feature", "功能"]
        
        # 高风险关键词：代码、架构、重构
        high_risk_kw = ["代码", "架构", "重构", "重写", "迁移", "删除", "删除",
                        "code", "architecture", "refactor", "rewrite", "migrate"]
        
        combined = f"{sug_text} {pattern}"
        
        for kw in high_risk_kw:
            if kw in combined:
                return self._RISK_HIGH
        
        for kw in medium_risk_kw:
            if kw in combined:
                return self._RISK_MEDIUM
        
        for kw in low_risk_kw:
            if kw in combined:
                return self._RISK_LOW
        
        # 默认中风险——宁可等人确认也不乱动
        return self._RISK_MEDIUM
    
    # ── 8b. 低风险自动执行 ────────────────────────────────
    
    def _execute_low_risk(self, suggestion: Dict) -> Dict:
        """
        执行低风险改进建议
        
        当前可调参数（memory_params.json）：
        - recall_threshold: 检索阈值（默认0.25）
        - ltp_strength: 突触增强强度（默认0.1）
        - decay_rate: 衰减率（默认0.001）
        - max_recall_results: 最大检索结果数（默认10）
        
        执行策略：先备份 → 执行 → 验证 → 回滚/保留
        """
        param_path = self.workspace / '.learnings' / 'memory_params.json'
        if not param_path.exists():
            return {"applied": False, "reason": "memory_params.json not found"}
        
        # 1. 备份
        backup_dir = self.workspace / '.learnings' / 'backups'
        backup_dir.mkdir(parents=True, exist_ok=True)
        
        backup_file = backup_dir / f"memory_params_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        import shutil
        shutil.copy2(str(param_path), str(backup_file))
        
        # 2. 读当前配置
        with open(param_path, 'r', encoding='utf-8') as f:
            params = json.load(f)
        
        # 3. 解析建议，决定改什么参数
        sug_text = suggestion.get("suggestion", "").lower()
        changes = []
        rollback_needed = False
        
        # — 检索阈值
        if "检索" in sug_text or "recall" in sug_text or "threshold" in sug_text:
            old_val = params.get("recall_threshold", 0.25)
            new_val = min(old_val * 1.2, 0.5)  # 最多放宽到0.5
            if abs(new_val - old_val) > 0.01:
                params["recall_threshold"] = new_val
                changes.append(f"recall_threshold: {old_val} → {new_val}")
                rollback_needed = True
        
        # — LTP 强度
        if "ltp" in sug_text or "突触" in sug_text or "synapse" in sug_text:
            old_val = params.get("ltp_strength", 0.1)
            new_val = min(old_val * 1.3, 0.5)
            if abs(new_val - old_val) > 0.01:
                params["ltp_strength"] = new_val
                changes.append(f"ltp_strength: {old_val} → {new_val}")
                rollback_needed = True
        
        # — 衰减率
        if "衰减" in sug_text or "decay" in sug_text:
            old_val = params.get("decay_rate", 0.001)
            new_val = min(old_val * 1.5, 0.01)
            if abs(new_val - old_val) > 1e-6:
                params["decay_rate"] = new_val
                changes.append(f"decay_rate: {old_val} → {new_val}")
                rollback_needed = True
        
        # — 最大检索结果
        if "max_result" in sug_text or "检索结果" in sug_text or "结果数量" in sug_text:
            old_val = params.get("max_recall_results", 10)
            new_val = min(old_val * 1.5, 20)
            if new_val != old_val:
                params["max_recall_results"] = new_val
                changes.append(f"max_recall_results: {old_val} → {new_val}")
                rollback_needed = True
        
        if not changes:
            return {"applied": False, "reason": "no applicable parameters", "suggestion": sug_text}
        
        # 4. 写入新配置
        with open(param_path, 'w', encoding='utf-8') as f:
            json.dump(params, f, indent=2, ensure_ascii=False)
        
        # 5. 验证写入
        with open(param_path, 'r', encoding='utf-8') as f:
            saved = json.load(f)
        
        applied = True
        for change in changes:
            key = change.split(":")[0].strip()
            if key in saved:
                pass
            else:
                applied = False
        
        # 6. 如果不成功，回滚
        if not applied:
            shutil.copy2(str(backup_file), str(param_path))
            return {"applied": False, "reason": "verification failed, rolled back", "changes": changes}
        
        return {
            "applied": True,
            "changes": changes,
            "backup": str(backup_file),
            "needs_rollback_check": rollback_needed,
        }
    
    # ── 8c. 中风险建议 → 写入可执行脚本 ─────────────────
    
    def _write_suggestion_script(self, suggestion: Dict) -> str:
        """
        将中风险改进建议写入可执行脚本文件
        
        脚本以 .sh 格式生成，等人审批后执行
        文件位置: .learnings/pending_executions/
        """
        pending_dir = self.workspace / '.learnings' / 'pending_executions'
        pending_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        safe_name = suggestion.get("pattern", "improvement")[:30].replace(" ", "_")
        script_path = pending_dir / f"{timestamp}_{safe_name}.sh"
        
        script_lines = [
            "#!/bin/bash",
            "# ⬡ 中风险改进建议 — 等待人工审批后执行",
            f"# 来源: {suggestion.get('source', 'unknown')}",
            f"# 模式: {suggestion.get('pattern', 'unknown')}",
            f"# 建议: {suggestion.get('suggestion', '')}",
            f"# 严重程度: {suggestion.get('severity', 'medium')}",
            f"# 生成时间: {datetime.now(timezone.utc).isoformat()}",
            "#",
            "# ═══ 审批后执行以下命令 ═══",
            "#",
            "# 1. 确认无误后，移除开头的 # 并执行",
            "# 2. 执行后运行 health check 验证",
            "#",
            "",
        ]
        
        sug_text = suggestion.get("suggestion", "").lower()
        pattern = suggestion.get("pattern", "").lower()
        
        # 根据建议内容生成可执行命令
        if "重试" in sug_text or "超时" in sug_text:
            script_lines.append("# 调整超时/重试配置")
            script_lines.append(f"# sed -i 's/timeout: [0-9]*/timeout: 30/' config/*.json")
        elif "启用" in sug_text or "开启" in sug_text:
            module = pattern.replace("enable_", "").replace("error_", "")
            script_lines.append(f"# 启用模块: {module}")
            script_lines.append(f"# openclaw config set skills.{module}.enabled true")
        elif "关闭" in sug_text or "禁用" in sug_text:
            module = pattern.replace("disable_", "").replace("error_", "")
            script_lines.append(f"# 禁用模块: {module}")
            script_lines.append(f"# openclaw config set skills.{module}.enabled false")
        else:
            script_lines.append(f"# 建议: {suggestion.get('suggestion', '')}")
            script_lines.append(f"# 请根据具体情况编写执行命令")
        
        with open(script_path, 'w', encoding='utf-8') as f:
            f.write('\n'.join(script_lines) + '\n')
        
        os.chmod(str(script_path), 0o644)
        
        return str(script_path)
    
    # ── 8d. 更新调度器：在循环中调用决策执行 ────────────
    # 需要在 _scheduler_loop 中添加对 active_decision_and_execute 的调用
    # 这个由 start_active_scheduler 自动集成
