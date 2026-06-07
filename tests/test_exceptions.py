"""
测试统一异常体系
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from services.exceptions import (
    SkillError, EmbeddingError, LLMError, CacheError,
    SafetyError, SearchError, AuthenticationError,
    DependencyError, SerializationError,
)


class TestSkillError:
    """基础异常测试"""

    def test_basic_creation(self):
        err = SkillError("test message")
        assert err.message == "test message"
        assert err.code == "SKILL_ERROR"
        assert err.details == {}

    def test_with_details(self):
        err = SkillError("failed", code="CUSTOM", details={"retry": 3})
        assert err.message == "failed"
        assert err.code == "CUSTOM"
        assert err.details == {"retry": 3}

    def test_to_dict(self):
        err = SkillError("oops", details={"line": 42})
        d = err.to_dict()
        assert d["error"] is True
        assert d["code"] == "SKILL_ERROR"
        assert d["message"] == "oops"
        assert d["details"] == {"line": 42}

    def test_is_exception(self):
        err = SkillError("test")
        assert isinstance(err, Exception)
        # 确保能正常 raise/catch
        with pytest.raises(SkillError):
            raise err

    def test_empty_details_default(self):
        err = SkillError("msg")
        assert err.details == {}


class TestExceptionHierarchy:
    """子类异常测试"""

    def test_embedding_error_code(self):
        err = EmbeddingError("embed failed")
        assert err.code == "EMBEDDING_ERROR"
        assert isinstance(err, SkillError)

    def test_llm_error_code(self):
        err = LLMError("api timeout")
        assert err.code == "LLM_ERROR"
        assert isinstance(err, SkillError)

    def test_cache_error_code(self):
        err = CacheError("cache miss")
        assert err.code == "CACHE_ERROR"
        assert isinstance(err, SkillError)

    def test_safety_error_code(self):
        err = SafetyError("unsafe content")
        assert err.code == "SAFETY_ERROR"
        assert isinstance(err, SkillError)

    def test_search_error_code(self):
        err = SearchError("search timeout")
        assert err.code == "SEARCH_ERROR"

    def test_auth_error_code(self):
        err = AuthenticationError("invalid token")
        assert err.code == "AUTH_ERROR"

    def test_dependency_error_code(self):
        err = DependencyError("missing module")
        assert err.code == "DEP_ERROR"

    def test_serialization_error_code(self):
        err = SerializationError("json decode")
        assert err.code == "SERIALIZATION_ERROR"

    def test_subclass_to_dict_includes_correct_code(self):
        err = LLMError("llm failed", details={"model": "gpt"})
        d = err.to_dict()
        assert d["code"] == "LLM_ERROR"
        assert d["details"]["model"] == "gpt"

    def test_catch_by_parent(self):
        """所有子类应能被 SkillError 捕获"""
        for err_cls in [EmbeddingError, LLMError, CacheError,
                        SafetyError, SearchError, AuthenticationError,
                        DependencyError, SerializationError]:
            with pytest.raises(SkillError):
                raise err_cls("test")

    def test_str_representation(self):
        err = SkillError("hello world")
        assert str(err) == "hello world"
