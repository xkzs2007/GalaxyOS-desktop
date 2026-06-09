"""
统一异常体系

为所有模块提供一致的错误处理方式，替代混乱的返回值模式。
"""


class SkillError(Exception):
    """技能包基础异常"""

    def __init__(self, message: str, code: str = "SKILL_ERROR", details: dict = None):
        self.message = message
        self.code = code
        self.details = details or {}
        super().__init__(self.message)

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "error": True,
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


class EmbeddingError(SkillError):
    """Embedding 相关错误"""

    def __init__(self, message: str, details: dict = None):
        super().__init__(message, code="EMBEDDING_ERROR", details=details)


class LLMError(SkillError):
    """LLM 相关错误"""

    def __init__(self, message: str, details: dict = None):
        super().__init__(message, code="LLM_ERROR", details=details)


class CacheError(SkillError):
    """缓存相关错误"""

    def __init__(self, message: str, details: dict = None):
        super().__init__(message, code="CACHE_ERROR", details=details)


class SafetyError(SkillError):
    """安全相关错误"""

    def __init__(self, message: str, details: dict = None):
        super().__init__(message, code="SAFETY_ERROR", details=details)


class SearchError(SkillError):
    """搜索相关错误"""

    def __init__(self, message: str, details: dict = None):
        super().__init__(message, code="SEARCH_ERROR", details=details)


class AuthenticationError(SkillError):
    """认证相关错误"""

    def __init__(self, message: str, details: dict = None):
        super().__init__(message, code="AUTH_ERROR", details=details)


class DependencyError(SkillError):
    """依赖相关错误"""

    def __init__(self, message: str, details: dict = None):
        super().__init__(message, code="DEP_ERROR", details=details)


class SerializationError(SkillError):
    """序列化/反序列化相关错误"""

    def __init__(self, message: str, details: dict = None):
        super().__init__(message, code="SERIALIZATION_ERROR", details=details)


__all__ = [
    'SkillError',
    'EmbeddingError',
    'LLMError',
    'CacheError',
    'SafetyError',
    'SearchError',
    'AuthenticationError',
    'DependencyError',
    'SerializationError',
]
