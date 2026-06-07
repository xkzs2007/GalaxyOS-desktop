"""
测试 Chain of Verification — 自验证引擎
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from services.chain_of_verification import (
    VerifiableClaim, VerificationResult, ChainOfVerificationEngine,
)


class TestVerifiableClaim:
    """可验证声明测试"""

    def test_default_creation(self):
        c = VerifiableClaim(text="Python is a language")
        assert c.text == "Python is a language"
        assert c.category == ""
        assert c.verified is False
        assert c.passed is False
        assert c.evidence == ""
        assert c.confidence == 0.0
        assert c.correction == ""

    def test_full_claim(self):
        c = VerifiableClaim(
            text="2+2=4",
            category="factual",
            verified=True,
            passed=True,
            evidence="basic arithmetic",
            confidence=0.99,
            correction="",
        )
        assert c.verified is True
        assert c.passed is True
        assert c.confidence == 0.99

    def test_failed_claim_with_correction(self):
        c = VerifiableClaim(
            text="2+2=5",
            category="factual",
            verified=True,
            passed=False,
            evidence="mathematical check",
            confidence=0.0,
            correction="2+2=4",
        )
        assert c.passed is False
        assert c.correction == "2+2=4"


class TestVerificationResult:
    """验证结果测试"""

    def test_default_result(self):
        r = VerificationResult()
        assert r.original_answer == ""
        assert r.refined_answer == ""
        assert r.total_claims == 0
        assert r.passed_claims == 0
        assert r.failed_claims == 0
        assert r.success is False

    def test_with_claims(self):
        claims = [
            VerifiableClaim(text="A", verified=True, passed=True),
            VerifiableClaim(text="B", verified=True, passed=False),
        ]
        r = VerificationResult(
            original_answer="A and B",
            claims=claims,
            total_claims=2,
            passed_claims=1,
            failed_claims=1,
            success=True,
        )
        assert len(r.claims) == 2
        assert r.total_claims == 2
        assert r.passed_claims == 1
        assert r.failed_claims == 1
        assert r.success is True


class TestChainOfVerificationEngine:
    """CoVe 引擎测试"""

    def test_init_no_clients(self):
        engine = ChainOfVerificationEngine()
        assert engine.llm_flash is None
        assert engine.llm_pro is None
        assert engine.flash_model == "deepseek-v4-flash"
        assert engine.pro_model == "deepseek-v4-pro"

    def test_init_with_clients(self):
        class MockClient:
            pass
        flash = MockClient()
        pro = MockClient()
        engine = ChainOfVerificationEngine(
            llm_flash=flash,
            llm_pro=pro,
        )
        assert engine.llm_flash is flash
        assert engine.llm_pro is pro

    def test_init_custom_models(self):
        engine = ChainOfVerificationEngine(
            flash_model="custom-flash",
            pro_model="custom-pro",
        )
        assert engine.flash_model == "custom-flash"
        assert engine.pro_model == "custom-pro"

    def test_verify_and_refine_basic(self):
        """无 LLM 时 verify_and_refine 应优雅降级"""
        engine = ChainOfVerificationEngine()
        result = engine.verify_and_refine(
            answer="test answer",
            query="test query",
            context="",
        )
        assert isinstance(result, VerificationResult)

    def test_verify_and_refine_structured(self):
        """有多个声明的回答"""
        engine = ChainOfVerificationEngine()
        result = engine.verify_and_refine(
            answer="Python was created by Guido van Rossum in 1991.",
            query="When was Python created?",
            context="",
        )
        assert isinstance(result, VerificationResult)
