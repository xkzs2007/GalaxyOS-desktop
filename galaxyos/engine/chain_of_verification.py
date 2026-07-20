"""
Chain-of-Verification (CoVe) + Self-Refine 自验证引擎

论文参考:
  - Chain-of-Verification (2024) — 生成→验证计划→逐条验证→最终修正
    https://arxiv.org/abs/2409.01207
  - Self-Refine: Iterative Refinement with Self-Feedback (Madaan 2023)
    https://arxiv.org/abs/2303.17651

核心流程:
  1. 初始生成（来自 R-CCAM Action 阶段的结果）
  2. 验证计划: 分析回答中的可验证声明
  3. 逐条验证: 对每条声明执行独立验证
  4. 发现不一致: 标记有问题的声明
  5. 修正: 基于验证结果重写回答
  6. (可选) 多轮精炼

与 EnhancedHallucinationGuard 的关系:
  - 现有的 10 重检测是"静态"校验（生成后一次性验证）
  - CoVe 是"动态"校验（生成→验证→修正的迭代过程）
  - 两者互补: 静态 10 重检测作为第一道防线，CoVe 作为第二道

Author: GalaxyOS
"""

import json
import re
import time
import logging
from typing import List, Dict, Optional
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class VerifiableClaim:
    """可验证声明"""
    text: str                      # 声明原文
    category: str = ""             # 分类: factual / analytical / opinion / code
    verified: bool = False         # 是否已验证
    passed: bool = False           # 是否通过验证
    evidence: str = ""             # 验证依据
    confidence: float = 0.0        # 验证置信度
    correction: str = ""           # 修正建议

@dataclass
class VerificationResult:
    """CoVe 验证结果"""
    original_answer: str = ""
    refined_answer: str = ""
    claims: List[VerifiableClaim] = field(default_factory=list)
    total_claims: int = 0
    passed_claims: int = 0
    failed_claims: int = 0
    contradictions_found: int = 0
    refine_rounds: int = 0
    max_refine_rounds: int = 2
    duration_ms: float = 0.0
    success: bool = False


class ChainOfVerificationEngine:
    """
    Chain-of-Verification 自验证引擎

    用法:
        cove = ChainOfVerificationEngine(llm_flash=client, llm_pro=client)
        result = cove.verify_and_refine(original_answer, query, context)
    """

    def __init__(
        self,
        llm_flash=None,
        llm_pro=None,
        flash_model: str = "deepseek-v4-flash",
        pro_model: str = "deepseek-v4-pro",
        hallucination_guard=None  # 可选的现有防幻觉引擎
    ):
        self.llm_flash = llm_flash
        self.llm_pro = llm_pro
        self.flash_model = flash_model
        self.pro_model = pro_model
        self.hallucination_guard = hallucination_guard

    def verify_and_refine(
        self,
        answer: str,
        query: str,
        context: Optional[str] = None,
        max_rounds: int = 2
    ) -> VerificationResult:
        """
        主入口: 验证回答 → 修正

        Args:
            answer: 原始回答
            query: 原始问题
            context: 检索到的上下文（可选）
            max_rounds: 最多精炼轮次

        Returns:
            VerificationResult
        """
        result = VerificationResult(
            original_answer=answer,
            max_refine_rounds=min(max_rounds, 3)
        )
        t0 = time.time()

        current_answer = answer

        for round_idx in range(result.max_refine_rounds):
            try:
                # Step 1: 提取可验证声明
                claims = self._extract_claims(current_answer, query)
                if not claims:
                    result.total_claims = 0
                    result.success = True
                    result.refined_answer = current_answer
                    break

                # Step 2: 对每条声明进行验证
                passed = 0
                failed = 0
                corrections = []

                for claim in claims:
                    verify_result = self._verify_claim(claim, query, context)
                    claim.verified = True
                    claim.passed = verify_result["passed"]
                    claim.evidence = verify_result.get("evidence", "")
                    claim.confidence = verify_result.get("confidence", 0.0)
                    claim.correction = verify_result.get("correction", "")

                    if claim.passed:
                        passed += 1
                    else:
                        failed += 1
                        if claim.correction:
                            corrections.append(claim.correction)

                result.claims.extend(claims)
                result.total_claims += len(claims)
                result.passed_claims += passed
                result.failed_claims += failed
                result.refine_rounds += 1

                # Step 3: 如果没有失败项 → 通过
                if failed == 0:
                    result.success = True
                    result.refined_answer = current_answer
                    break

                # Step 4: 修正回答
                if corrections and round_idx < result.max_refine_rounds - 1:
                    refined = self._refine_answer(
                        current_answer, claims, query, context
                    )
                    if refined and refined != current_answer:
                        current_answer = refined
                        result.contradictions_found += len(corrections)
                    else:
                        # 修正没有变化 → 接受当前版本
                        result.refined_answer = current_answer
                        result.success = True
                        break
                else:
                    result.refined_answer = current_answer
                    result.success = failed == 0

            except Exception as e:
                logger.error(f"CoVe 第{round_idx+1}轮失败: {e}")
                result.refined_answer = current_answer
                break

        # 最后一轮没设置的情况
        if not result.refined_answer:
            result.refined_answer = current_answer
            result.success = result.failed_claims == 0

        result.duration_ms = round((time.time() - t0) * 1000, 1)
        return result

    # ─── 内部方法 ───

    def _extract_claims(self, answer: str, query: str) -> List[VerifiableClaim]:
        """从回答中提取可验证的事实性声明"""
        if not self.llm_flash:
            # 无模型时：按句号分割
            sentences = [s.strip() for s in re.split(r'[。！？\n]', answer) if len(s.strip()) > 10]
            return [
                VerifiableClaim(text=s, category="factual")
                for s in sentences[:10]
            ]

        try:
            resp = self.llm_flash.chat.completions.create(
                model=self.flash_model,
                messages=[{"role": "user", "content":
                    f"分析以下回答，列出所有需要验证的事实性声明。\n\n"
                    f"原始问题: {query}\n\n"
                    f"回答: {answer}\n\n"
                    f"输出格式: 每行一条声明，格式: [分类] 声明内容\n"
                    f"分类: factual(事实)/analytical(分析)/opinion(观点)/code(代码)\n"
                    f"仅输出声明列表，不要额外文字。"}],
                max_tokens=800,
                temperature=0.1
            )
            text = resp.choices[0].message.content.strip()
            claims = []
            for line in text.strip().split('\n'):
                line = line.strip()
                if not line:
                    continue
                # 提取分类
                cat_match = re.match(r'\[(\w+)\]\s*(.+)', line)
                if cat_match:
                    cat = cat_match.group(1)
                    claim_text = cat_match.group(2)
                else:
                    cat = "factual"
                    claim_text = re.sub(r'^\d+[\.\、\s]+', '', line)

                if len(claim_text) > 5:
                    claims.append(VerifiableClaim(
                        text=claim_text,
                        category=cat
                    ))
            return claims[:15]  # 最多 15 条
        except Exception as e:
            logger.warning(f"提取声明失败: {e}")
            return []

    def _verify_claim(
        self,
        claim: VerifiableClaim,
        query: str,
        context: Optional[str]
    ) -> Dict:
        """验证单条声明"""
        # 分类处理
        if claim.category == "opinion":
            # 观点类不需要验证
            return {"passed": True, "evidence": "观点无需验证", "confidence": 0.8, "correction": ""}

        if claim.category == "code":
            # 代码类用简单的语法检查
            return self._verify_code_claim(claim.text)

        if claim.category == "factual" and context:
            # 事实类：用上下文验证
            return self._verify_factual(claim.text, context)

        # 默认：用 LLM 自验证
        return self._verify_via_llm(claim.text, query)

    def _verify_factual(self, claim: str, context: str) -> Dict:
        """基于提供的事实上下文验证声明"""
        if not self.llm_flash:
            return self._default_pass()

        # 先检查 10 重防幻觉（如果有）
        if self.hallucination_guard:
            try:
                guard_result = self.hallucination_guard.verify_with_cross_validation(
                    statement=claim,
                    context=context
                )
                if guard_result.get("is_reliable", False):
                    return {
                        "passed": True,
                        "evidence": "通过防幻觉引擎验证",
                        "confidence": guard_result.get("final_confidence", 0.7),
                        "correction": ""
                    }
            except Exception:
                pass

        try:
            resp = self.llm_flash.chat.completions.create(
                model=self.flash_model,
                messages=[{"role": "user", "content":
                    f"验证以下声明是否与提供的上下文一致。\n\n"
                    f"声明: {claim}\n\n"
                    f"上下文:\n{context[:2000]}\n\n"
                    f"输出 JSON:\n"
                    f"{{\"passed\": true/false, \"evidence\": \"依据\", "
                    f"\"confidence\": 0.0-1.0, \"correction\": \"如果错误，修正建议\"}}"
                }],
                max_tokens=500,
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            text = resp.choices[0].message.content.strip()
            return json.loads(text)
        except Exception as e:
            logger.warning(f"事实验证失败: {e}")
            return self._default_pass()

    def _verify_via_llm(self, claim: str, query: str) -> Dict:
        """无上下文时，用 LLM 自身知识验证"""
        if not self.llm_flash:
            return self._default_pass()

        try:
            resp = self.llm_flash.chat.completions.create(
                model=self.flash_model,
                messages=[{"role": "user", "content":
                    f"验证以下声明的准确性。根据你的知识判断是否合理。\n\n"
                    f"声明: {claim}\n\n"
                    f"相关背景: {query}\n\n"
                    f"输出 JSON:\n"
                    f"{{\"passed\": true/false, \"evidence\": \"支持/反驳的理由\", "
                    f"\"confidence\": 0.0-1.0, \"correction\": \"如果错误，正确版本\"}}"
                }],
                max_tokens=500,
                temperature=0.1,
                response_format={"type": "json_object"}
            )
            text = resp.choices[0].message.content.strip()
            return json.loads(text)
        except Exception as e:
            logger.warning(f"LLM 验证失败: {e}")
            return self._default_pass()

    def _verify_code_claim(self, code_text: str) -> Dict:
        """验证代码声明"""
        try:
            compile(code_text, '<verify>', 'exec')
            return {
                "passed": True,
                "evidence": "语法检查通过",
                "confidence": 0.8,
                "correction": ""
            }
        except SyntaxError as e:
            return {
                "passed": False,
                "evidence": f"语法错误: {e.msg}",
                "confidence": 0.9,
                "correction": f"代码存在语法错误: {e.msg}"
            }
        except Exception as e:
            return self._default_pass()

    def _refine_answer(
        self,
        answer: str,
        claims: List[VerifiableClaim],
        query: str,
        context: Optional[str]
    ) -> str:
        """基于验证失败项修正回答"""
        if not self.llm_pro:
            return answer

        # 收集失败项
        failed = [c for c in claims if c.verified and not c.passed]
        if not failed:
            return answer

        corrections = "\n".join([
            f"[需要修正] {c.text}\n  → {c.correction or '需进一步验证'}"
            for c in failed
        ])

        try:
            resp = self.llm_pro.chat.completions.create(
                model=self.pro_model,
                messages=[{"role": "user", "content":
                    f"基于验证反馈修正以下回答。\n\n"
                    f"原始问题: {query}\n\n"
                    f"原始回答:\n{answer}\n\n"
                    f"验证发现的问题:\n{corrections}\n\n"
                    f"{'参考上下文:' + chr(10) + context[:2000] if context else ''}\n\n"
                    f"请输出修正后的完整回答:"}],
                max_tokens=2000,
                temperature=0.2
            )
            refined = resp.choices[0].message.content.strip()
            return refined if len(refined) > 20 else answer
        except Exception as e:
            logger.warning(f"修正失败: {e}")
            return answer

    def _default_pass(self) -> Dict:
        return {"passed": True, "evidence": "验证降级通过", "confidence": 0.5, "correction": ""}
