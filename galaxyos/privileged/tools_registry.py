"""
OpenClaw Tools Registry - 横向能力注册中心

将私有包的各个模块注册为独立的工具，实现模块化调用。
"""

import sys
from typing import Dict, Any, Callable, Optional
from dataclasses import dataclass


@dataclass
class ToolDefinition:
    """工具定义"""
    name: str
    description: str
    category: str  # core, api, search, native, system
    handler: Callable
    parameters: Dict[str, Any]
    returns: Dict[str, Any]
    platform: Optional[list] = None
    requires: Optional[list] = None


class ToolsRegistry:
    """工具注册中心"""

    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}
        self._categories: Dict[str, list] = {
            'core': [],      # 核心 LLM/Embedding
            'api': [],       # 网络 API
            'search': [],    # 搜索增强
            'native': [],    # 原生扩展
            'system': []     # 系统优化
        }
        self._platform = sys.platform

    def register(self, tool: ToolDefinition, skip_dep_check: bool = False) -> bool:
        """注册工具

        Args:
            tool: 工具定义
            skip_dep_check: 是否跳过依赖检查（仍注册但标记不可用）

        平台不满足时不注册；依赖缺失时默认跳过，
        但 skip_dep_check=True 时仍注册（标记为不可用），
        方便用户安装依赖后启用。
        """
        # 平台检查（不可恢复，直接跳过）
        if tool.platform and self._platform not in tool.platform:
            return False

        # 依赖检查
        if tool.requires:
            missing = [r for r in tool.requires if not self._check_dependency(r)]
            if missing:
                if not skip_dep_check:
                    return False
                # 标记缺失依赖但仍注册
                tool._missing_deps = missing  # type: ignore[attr-defined]
        else:
            tool._missing_deps = []  # type: ignore[attr-defined]

        self._tools[tool.name] = tool
        self._categories[tool.category].append(tool.name)
        return True

    def get(self, name: str) -> Optional[ToolDefinition]:
        """获取工具"""
        return self._tools.get(name)

    def list_tools(self, category: Optional[str] = None) -> list:
        """列出工具"""
        if category:
            return self._categories.get(category, [])
        return list(self._tools.keys())

    def list_available_tools(self, category: Optional[str] = None) -> list:
        """列出可用工具（依赖满足的）"""
        available = []
        for name, tool in self._tools.items():
            if not getattr(tool, '_missing_deps', []):
                if category is None or tool.category == category:
                    available.append(name)
        return available

    def list_unavailable_tools(self, category: Optional[str] = None) -> list:
        """列出不可用工具（依赖缺失的）"""
        unavailable = []
        for name, tool in self._tools.items():
            if getattr(tool, '_missing_deps', []):
                if category is None or tool.category == category:
                    unavailable.append(name)
        return unavailable

    def get_missing_deps(self, tool_name: str) -> list:
        """获取工具缺失的依赖"""
        tool = self._tools.get(tool_name)
        if not tool:
            return []
        return getattr(tool, '_missing_deps', [])

    def execute(self, name: str, **kwargs) -> Any:
        """执行工具"""
        tool = self.get(name)
        if not tool:
            raise ValueError(f"Tool not found: {name}")

        # 检查缺失依赖
        missing = getattr(tool, '_missing_deps', [])
        if missing:
            raise ValueError(
                f"Tool '{name}' is unavailable - missing dependencies: {', '.join(missing)}. "
                f"Install them with: pip install {' '.join(missing)}"
            )

        # 参数验证
        for param, schema in tool.parameters.items():
            if schema.get('required') and param not in kwargs:
                raise ValueError(f"Missing required parameter: {param}")

        return tool.handler(**kwargs)

    # 仅允许的安全依赖列表
    _SAFE_DEPS = {
        'numpy', 'scipy', 'pandas', 'sklearn', 'torch',
        'mkl', 'pysqlite3', 'sqlite3', 'numba',
        'hashlib', 'json', 'sqlite_vec',
    }

    def _check_dependency(self, dep: str) -> bool:
        """检查依赖（仅允许白名单中的模块）"""
        if dep not in self._SAFE_DEPS:
            return False
        try:
            __import__(dep)
            return True
        except ImportError:
            return False

    def to_openclaw_tools(self) -> list:
        """转换为 OpenClaw 工具格式"""
        tools = []
        for name, tool in self._tools.items():
            tools.append({
                'name': name,
                'description': tool.description,
                'parameters': tool.parameters,
                'returns': tool.returns
            })
        return tools


# 全局注册中心
registry = ToolsRegistry()


def register_tool(
    name: str,
    description: str,
    category: str,
    parameters: Dict[str, Any],
    returns: Dict[str, Any],
    platform: Optional[list] = None,
    requires: Optional[list] = None,
    skip_dep_check: bool = True
):
    """工具注册装饰器

    Args:
        skip_dep_check: 依赖缺失时仍注册（默认True），用户安装依赖后即可使用
    """
    def decorator(func: Callable):
        tool = ToolDefinition(
            name=name,
            description=description,
            category=category,
            handler=func,
            parameters=parameters,
            returns=returns,
            platform=platform,
            requires=requires
        )
        registry.register(tool, skip_dep_check=skip_dep_check)
        return func
    return decorator


# ============ 注册核心工具 ============

@register_tool(
    name="embedding.encode",
    description="将文本转换为向量表示（依赖: scripts_core.embedding）",
    category="core",
    parameters={
        "text": {"type": "string", "required": True, "description": "输入文本"},
        "model": {"type": "string", "required": False, "default": "text-embedding-3-small"}
    },
    returns={"type": "array", "description": "向量数组"}
)
def embedding_encode(text: str, model: str = "text-embedding-3-small"):
    """Embedding 编码（失败时抛出 EmbeddingError 而非返回 None）

    Dependencies: scripts_core.embedding.EmbeddingEngine, exceptions.EmbeddingError
    """
    from .scripts_core.embedding import EmbeddingEngine
    from .exceptions import EmbeddingError

    engine = EmbeddingEngine(model=model)
    result = engine.encode(text)
    if result is None:
        raise EmbeddingError("Embedding 编码失败: API 不可用或文本为空", details={"text": text[:100], "model": model})
    return result


@register_tool(
    name="llm.chat",
    description="LLM 对话生成（支持流式输出）（依赖: scripts_core.llm, llm_streaming）",
    category="core",
    parameters={
        "prompt": {"type": "string", "required": True, "description": "提示词"},
        "model": {"type": "string", "required": False, "default": "gpt-4"},
        "stream": {"type": "boolean", "required": False, "default": False, "description": "是否启用流式输出"}
    },
    returns={"type": "string", "description": "生成的文本"}
)
def llm_chat(prompt: str, model: str = "gpt-4", stream: bool = False):
    """LLM 对话（支持流式）

    Dependencies: scripts_core.llm.LLMEngine, llm_streaming.StreamingHandler
    """
    from .scripts_core.llm import LLMEngine
    from .llm_streaming import StreamingHandler  # type: ignore[attr-defined]

    engine = LLMEngine(model=model)

    if stream:
        # 流式模式：返回生成器
        handler = StreamingHandler(engine)
        return handler.stream_chat(prompt)
    else:
        # 非流式模式：直接返回完整结果
        return engine.chat(prompt, use_cache=True)


@register_tool(
    name="search.hybrid",
    description="混合搜索（向量 + FTS）（依赖: scripts_core.rrf, scripts_core.router）",
    category="search",
    parameters={
        "query": {"type": "string", "required": True, "description": "查询文本"},
        "top_k": {"type": "integer", "required": False, "default": 10},
        "vector_weight": {"type": "number", "required": False, "default": 0.6}
    },
    returns={"type": "array", "description": "搜索结果列表"}
)
def search_hybrid(query: str, top_k: int = 10, vector_weight: float = 0.6):
    """混合搜索

    Dependencies: scripts_core.rrf.RRFFusion, scripts_core.router.QueryRouter
    """
    from .scripts_core.rrf import RRFFusion
    from .scripts_core.router import QueryRouter

    router = QueryRouter()
    fusion = RRFFusion()

    # 路由查询
    route = router.route(query)

    # 执行搜索
    results = fusion.fuse(
        vector_results=route.get('vector', []),
        fts_results=route.get('fts', []),
        vector_weight=vector_weight,
        top_k=top_k
    )

    return results


@register_tool(
    name="query.rewrite",
    description="查询改写优化（依赖: scripts_core.rewriter）",
    category="search",
    parameters={
        "query": {"type": "string", "required": True, "description": "原始查询"},
        "context": {"type": "array", "required": False, "description": "上下文"}
    },
    returns={"type": "string", "description": "改写后的查询"}
)
def query_rewrite(query: str, context: Optional[list] = None):
    """查询改写

    Dependencies: scripts_core.rewriter.QueryRewriter
    """
    from .scripts_core.rewriter import QueryRewriter
    rewriter = QueryRewriter()
    rewritten, corrections = rewriter.rewrite(query, context=context)
    # 返回改写后的查询（忽略 corrections 细节）
    return rewritten


@register_tool(
    name="result.explain",
    description="搜索结果解释（依赖: scripts_core.explainer）",
    category="search",
    parameters={
        "query": {"type": "string", "required": True, "description": "查询"},
        "result": {"type": "object", "required": True, "description": "搜索结果"}
    },
    returns={"type": "string", "description": "解释文本"}
)
def result_explain(query: str, result: dict):
    """结果解释

    Dependencies: scripts_core.explainer.ResultExplainer
    """
    from .scripts_core.explainer import ResultExplainer
    explainer = ResultExplainer()
    return explainer.explain(query, [result])


@register_tool(
    name="cache.get",
    description="缓存查询（依赖: scripts_core.cache）",
    category="api",
    parameters={
        "key": {"type": "string", "required": True, "description": "缓存键"}
    },
    returns={"type": "any", "description": "缓存值"}
)
def cache_get(key: str):
    """获取缓存

    Dependencies: scripts_core.cache.CacheManager
    """
    from .scripts_core.cache import CacheManager
    cache = CacheManager()
    return cache.get(key)


@register_tool(
    name="cache.set",
    description="缓存设置（依赖: scripts_core.cache）",
    category="api",
    parameters={
        "key": {"type": "string", "required": True, "description": "缓存键"},
        "value": {"type": "any", "required": True, "description": "缓存值"},
        "ttl": {"type": "integer", "required": False, "default": 3600}
    },
    returns={"type": "boolean", "description": "是否成功"}
)
def cache_set(key: str, value: Any, ttl: int = 3600):
    """设置缓存

    Dependencies: scripts_core.cache.CacheManager
    """
    from .scripts_core.cache import CacheManager
    cache = CacheManager(ttl=ttl)
    cache.set(key, value)
    return True


# ============ 系统优化工具 ============

@register_tool(
    name="system.optimize_numa",
    description="NUMA 亲和性优化（依赖: numa_optimizer）",
    category="system",
    parameters={},
    returns={"type": "object", "description": "优化结果"},
    platform=['linux']
)
def system_optimize_numa():
    """NUMA 优化

    Dependencies: numa_optimizer.NUMAOptimizer
    """
    try:
        from .numa_optimizer import NUMAOptimizer
        optimizer = NUMAOptimizer()
        return optimizer.optimize()
    except Exception as e:
        return {"error": str(e)}


@register_tool(
    name="system.optimize_hardware",
    description="硬件优化检测（依赖: hardware_optimize）",
    category="system",
    parameters={},
    returns={"type": "object", "description": "硬件信息"}
)
def system_optimize_hardware():
    """硬件优化

    Dependencies: hardware_optimize.HardwareOptimizer
    """
    try:
        from .hardware_optimize import HardwareOptimizer
        optimizer = HardwareOptimizer()
        return optimizer.detect()
    except Exception as e:
        return {"error": str(e)}


# ============ 导出 ============

__all__ = [
    'ToolsRegistry',
    'ToolDefinition',
    'registry',
    'register_tool'
]
