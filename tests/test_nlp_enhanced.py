"""测试 nlp_enhanced — NLP 增强处理"""
import sys; sys.path.insert(0, '.')
import pytest
from services.nlp_enhanced import (
    ComparisonDetector, ComparisonResult,
    CoreferenceResolver,
    DependencyParseResult, DependencyRelation,
)


class TestComparisonResult:
    def test_creation(self):
        r = ComparisonResult(has_comparison=True, subject_a="A", subject_b="B")
        assert r.has_comparison is True
        assert r.subject_a == "A"

    def test_no_comparison(self):
        r = ComparisonResult(has_comparison=False)
        assert r.has_comparison is False


class TestDependencyRelation:
    def test_creation(self):
        r = DependencyRelation(
            head_word="喜欢", head_pos="VV",
            dep_word="Python", dep_pos="NN",
            relation="dobj",
        )
        assert r.head_word == "喜欢"
        assert r.dep_word == "Python"


class TestDependencyParseResult:
    def test_creation(self):
        tokens = [("Python", "NN"), ("是", "VC"), ("语言", "NN")]
        relations = [
            DependencyRelation("是", "VC", "Python", "NN", "nsubj"),
        ]
        r = DependencyParseResult(tokens=tokens, relations=relations)
        assert len(r.tokens) == 3
        assert len(r.relations) == 1


class TestComparisonDetector:
    @pytest.fixture
    def detector(self):
        return ComparisonDetector()

    def test_init(self, detector):
        assert detector is not None

    def test_detect_comparison(self, detector):
        result = detector.detect("Python 比 Java 更简洁")
        assert isinstance(result, ComparisonResult)

    def test_detect_general(self, detector):
        # 非比较句可能返回 None 或 ComparisonResult
        result = detector.detect("今天天气很好")
        assert result is None or isinstance(result, ComparisonResult)

    def test_extract_comparison_graph(self, detector):
        result = detector.extract_comparison_graph("A 比 B 好")
        assert result is not None


class TestCoreferenceResolver:
    @pytest.fixture
    def resolver(self):
        return CoreferenceResolver()

    def test_init(self, resolver):
        assert resolver is not None

    def test_resolve_basic(self, resolver):
        result = resolver.resolve("张三说他喜欢Python")
        assert isinstance(result, dict)

    def test_update_context(self, resolver):
        resolver.update_context({"张三": "person"})
        result = resolver.resolve("他写了代码")
        assert isinstance(result, dict)
