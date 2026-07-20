"""GalaxyOS 融合守卫系统 — 检测并防止同名能力重复定义。

本模块提供融合替换机制的核心基础设施：

- **FusionRegistry**: 单例注册表，记录每个能力名到唯一实现模块路径的映射。
- **register_implementation()**: 注册能力实现，重复注册抛出 FusionConflictError。
- **assert_no_duplicates()**: 扫描注册表，返回所有冲突列表。
- **scan_fusion_conflicts()**: CI 级全量扫描，AST 遍历所有 .py 文件，
  找出同名 class/function 定义冲突，并自动排除白名单中的设计允许冲突。
- **@fusion_replace()**: 装饰器，标记新实现替换旧实现，自动注册到 FusionRegistry。

白名单机制
~~~~~~~~~~
scan_fusion_conflicts() 内置多层白名单，自动排除设计允许的冲突：

1. **STRUCTURAL_WHITELIST** — PEP 562 懒加载钩子（``__getattr__`` / ``__dir__``）
   及结构性重复（``__all__`` / ``__init__``），每个包都需要独立定义。
2. **CLI 入口函数** — ``main`` / ``_main`` / ``demo`` 是各模块独立 CLI 入口，
   同名是设计意图。
3. **REEXPORT_WHITELIST** — re-export 壳函数 + re-export 类
   （如 ``MemoryType`` / ``MemoryTypeClassifier``），旧位置模块 re-export from
   shared/ 或其他真相源，同名是桥接兼容需要。
4. **STRATEGY_WHITELIST** — 策略模式实现 + 策略模式类
   （如 ``CircuitBreaker`` / ``ResilienceSystem`` / ``PriorityLevel``），
   不同后端/策略的同名方法由运行时选择。
5. **PRIVATE_WHITELIST** — 私有辅助函数，模块内部 ``_`` 前缀实现，
   不同上下文互不干扰。
6. **测试类重复** — 不同测试文件中的同名 Test* 类是独立测试套件。
7. **设计允许的类** — re-export 类或策略模式类，同名是架构需要。
8. **跨技能/扩展重复** — skills/ 和 extensions/ 下的同名函数是独立部署单元。

可通过 ``get_whitelist()`` 查询当前白名单，通过 ``add_to_whitelist()`` 动态添加。

仅依赖 stdlib，不引入任何 GalaxyOS 内部模块。可通过以下命令执行 CI 扫描::

    python -m galaxyos.shared.fusion_guard [project_root]
    python -m galaxyos.shared.fusion_guard --whitelist

若未指定 project_root，默认扫描当前工作目录。
"""

from __future__ import annotations

import ast
import datetime
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, TypeVar

# ---------------------------------------------------------------------------
# 冲突白名单 — 设计允许的冲突，scan_fusion_conflicts() 将跳过它们
# ---------------------------------------------------------------------------

# 1. PEP 562 懒加载钩子 — 每个 __init__.py 都需要 __getattr__/__dir__
#    扩展为 STRUCTURAL_WHITELIST: 包含 __all__（re-export 列表）和 __init__（包初始化）
_PEP562_HOOKS: frozenset[str] = frozenset({
    "__getattr__", "__dir__",    # PEP 562 lazy loading
    "__all__",                   # re-export list
    "__init__",                  # package init
})

# 2. CLI 入口函数 — 各模块独立 CLI
_CLI_ENTRIES: frozenset[str] = frozenset({
    "main", "_main", "demo",
})

# 3. Re-export 壳 — 旧位置 re-export from shared/ 或其他真相源
_REEXPORT_SHELLS: frozenset[str] = frozenset({
    "classify", "batch_classify", "get_all_metadata", "get_default_classifier",
    "get_precision", "get_ttl",
    "safe_eval", "safe_load_extension", "sanitize_exception",
    "append_to_file", "write_file", "read_file",
    "ensure_dir", "validate_url", "is_safe_url",
    "cosine_similarity", "extract_keywords",
    "workspace", "get_db_connection",
    "connect", "connect_with_extension",
    "find_vec0_extension", "get_sqlite_module",
    "auto_bootstrap", "record_feedback",
    "load_config", "get_vec_extension_path",
    "calculate_file_hash", "check_coverage",
    "install_hook",                                     # version_guard re-export
    # ── search_strategies re-export shells (reasoning层10组同名类冲突修复) ──
    "BM25Index",                                        # hybrid_search → search_strategies
    "SmartVectorFusion",                                # hybrid_search → search_strategies
    "HybridRRFFusion",                                  # hybrid_search → search_strategies
    "ReasoningQueryRewriter",                           # hybrid_search → search_strategies
    "MangoOptimizer",                                   # hybrid_search → search_strategies
    "ResolutionLevel",                                  # multiresolution_search → search_strategies
    "MultiResolutionIndex",                             # multiresolution_search → search_strategies
    "QueryComplexityEstimator",                         # multiresolution_search → search_strategies
    "DistributedParallelSearcher",                      # multiresolution_search → search_strategies
    "IncrementalCache",                                 # ultimate_search → search_strategies
    "QueryComplexityAnalyzer",                          # ultimate_search → search_strategies
})

# 4. 策略模式实现 — 不同后端/策略的同名方法
_STRATEGY_IMPLEMENTATIONS: frozenset[str] = frozenset({
    "search_vector", "search_fts",                       # 不同搜索后端
    "get_embedding", "get_embedding_config",             # 不同嵌入获取
    "store",                                             # 不同存储后端
    "forget", "remember", "recall",                      # API层委托到memory层
    "get_entity", "get_xiaoyi_claw",                     # 不同层级的实体获取
    "get_engine",                                        # 不同引擎获取
    "get_cache_key", "get_cached", "set_cache",          # 不同缓存策略
    "tokenize", "analyze_sentiment", "process_text",     # NLP不同实现
    "save_config",                                       # 不同配置保存
    "get_logger",                                        # 不同日志器
    "get_missing_dependencies",                          # 不同依赖检查
    "merge_results",                                     # 不同搜索结果合并
    "learn",                                             # 不同学习入口
})

# 5. 私有辅助函数 — 模块内部实现，不同上下文
_PRIVATE_HELPERS: frozenset[str] = frozenset({
    "_char_ngrams", "_extract_existing_items", "_is_container",
    "_jaccard_similarity", "_load_latest_evolved_capabilities",
    "_rci_async_criticism", "_read_proc_lines", "_resolve_var_path",
    "_run_self_test", "_run_self_tests", "_scan_file",
    "_validate_extension_path", "_find_elements",
})

# 6. 测试类重复 — 不同测试文件中的同名 Test* 类
_TEST_DUPLICATES: frozenset[str] = frozenset({
    "TestCRAG", "TestContextCompressor", "TestConversation",
    "TestDependencyChecker", "TestIntegration", "TestMemoryIntegration",
    "TestReranker", "TestRetrievalEvaluator", "TestSelfRAG",
    "run_tests",
})

# 7. 设计允许的类 — re-export 类或策略模式类
_DESIGN_ALLOWED_CLASSES: frozenset[str] = frozenset({
    "CircuitBreaker", "ResilienceSystem",               # 策略模式: 弹性系统
    "MemGPTMemoryType", "Memory",                        # re-export: 内存类型
    "MemoryType", "MemoryTypeClassifier",                # re-export: 分类器
    "NlpEntity",                                         # re-export: NLP实体
    "PriorityLevel",                                     # 枚举重复定义
    "RequestHeaders",                                    # 技能间数据类重复
})

# 8. 跨技能/扩展重复 — skills/ 和 extensions/ 下的同名函数
_CROSS_SKILL_ENTRIES: frozenset[str] = frozenset({
    "detect_platform", "enhance", "ocr_preprocess", "resize", "vector_cosine",
    "generate_image", "calculate_sha256", "upload_file",
    "format_result", "read_xiaoyienv", "parse_args",
    "health_check", "status",
})

# ---------------------------------------------------------------------------
# 公开白名单别名 — 供外部查询和 scan_fusion_conflicts() 过滤使用
# ---------------------------------------------------------------------------

# STRUCTURAL_WHITELIST: PEP 562 懒加载钩子 + __all__/__init__ 结构性重复
STRUCTURAL_WHITELIST: frozenset[str] = _PEP562_HOOKS

# REEXPORT_WHITELIST: re-export 壳 + re-export 类（已指向唯一真相源）
REEXPORT_WHITELIST: frozenset[str] = _REEXPORT_SHELLS | frozenset({
    "MemGPTMemoryType",          # memory._memory_types → memory.storage re-export
    "MemoryType",                # memory.xiaoyi_memory → shared re-export
    "MemoryTypeClassifier",      # memory.xiaoyi_memory → shared re-export
})

# STRATEGY_WHITELIST: 策略模式实现 + 策略模式类（不同后端/策略的同名实现）
STRATEGY_WHITELIST: frozenset[str] = _STRATEGY_IMPLEMENTATIONS | frozenset({
    "CircuitBreaker",            # operations vs workflow — 子类委托
    "ResilienceSystem",          # operations vs workflow — 子类委托
    "PriorityLevel",             # memory.dag vs operations — 不同上下文枚举
    "Memory",                    # memory_stream vs storage — 不同 Memory 基类
})

# PRIVATE_WHITELIST: 私有辅助函数（不同模块允许同名 _ 前缀函数）
PRIVATE_WHITELIST: frozenset[str] = _PRIVATE_HELPERS

# 合并后的完整白名单（用于快速查找）
# 包含所有内部白名单 + 公开别名中额外添加的条目
_ALL_WHITELIST_NAMES: frozenset[str] = (
    _PEP562_HOOKS
    | _CLI_ENTRIES
    | _REEXPORT_SHELLS
    | _STRATEGY_IMPLEMENTATIONS
    | _PRIVATE_HELPERS
    | _TEST_DUPLICATES
    | _DESIGN_ALLOWED_CLASSES
    | _CROSS_SKILL_ENTRIES
    | REEXPORT_WHITELIST      # 包含额外 re-export 类
    | STRATEGY_WHITELIST      # 包含额外策略模式类
)

# 白名单分类标签（用于 get_whitelist() 输出）
_WHITELIST_CATEGORIES: dict[str, frozenset[str]] = {
    "structural_whitelist": STRUCTURAL_WHITELIST,
    "pep562_hooks": _PEP562_HOOKS,
    "cli_entries": _CLI_ENTRIES,
    "reexport_whitelist": REEXPORT_WHITELIST,
    "reexport_shells": _REEXPORT_SHELLS,
    "strategy_whitelist": STRATEGY_WHITELIST,
    "strategy_implementations": _STRATEGY_IMPLEMENTATIONS,
    "private_whitelist": PRIVATE_WHITELIST,
    "private_helpers": _PRIVATE_HELPERS,
    "test_duplicates": _TEST_DUPLICATES,
    "design_allowed_classes": _DESIGN_ALLOWED_CLASSES,
    "cross_skill_entries": _CROSS_SKILL_ENTRIES,
}


def _is_allowed_conflict(capability_name: str) -> bool:
    """判断冲突是否在白名单中（设计允许）。

    Args:
        capability_name: 冲突条目的能力名，格式为 ``"kind:name"``，
            如 ``"function:__getattr__"`` 或 ``"class:CircuitBreaker"``。

    Returns:
        bool: 若该冲突在白名单中返回 True。
    """
    # 解析 "kind:name" 格式，提取纯名
    if ":" in capability_name:
        _, name = capability_name.split(":", 1)
    else:
        name = capability_name
    return name in _ALL_WHITELIST_NAMES


def get_whitelist() -> dict[str, frozenset[str]]:
    """返回当前白名单配置（按分类）。

    Returns:
        dict[str, frozenset[str]]: 分类名 → 该分类下的白名单名称集合。
    """
    return dict(_WHITELIST_CATEGORIES)


def add_to_whitelist(name: str, category: str = "custom") -> None:
    """动态添加名称到白名单。

    Args:
        name: 要添加的函数名或类名。
        category: 白名单分类标签，默认 ``"custom"``。
    """
    global _ALL_WHITELIST_NAMES
    _ALL_WHITELIST_NAMES = _ALL_WHITELIST_NAMES | frozenset({name})
    if category in _WHITELIST_CATEGORIES:
        _WHITELIST_CATEGORIES[category] = _WHITELIST_CATEGORIES[category] | frozenset({name})
    else:
        _WHITELIST_CATEGORIES[category] = frozenset({name})


# ---------------------------------------------------------------------------
# 异常定义
# ---------------------------------------------------------------------------


class FusionConflictError(Exception):
    """融合冲突异常。

    当同一能力名被多个不同模块路径注册时抛出。

    Attributes:
        capability_name: 发生冲突的能力名。
        existing: 已注册的模块路径列表。
        new_entry: 试图新注册的模块路径。
    """

    def __init__(
        self,
        capability_name: str,
        existing: list[str],
        new_entry: str,
    ) -> None:
        self.capability_name = capability_name
        self.existing = existing
        self.new_entry = new_entry
        super().__init__(
            f"Fusion conflict for '{capability_name}': "
            f"already registered at {existing}, "
            f"duplicate attempt from '{new_entry}'"
        )


# ---------------------------------------------------------------------------
# 冲突记录数据结构
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ConflictEntry:
    """单条冲突记录。

    Attributes:
        capability_name: 冲突的能力名（类名或函数名）。
        locations: 所有定义该能力的模块路径列表。
    """

    capability_name: str
    locations: list[str]

    def __str__(self) -> str:
        locs = ", ".join(self.locations)
        return f"ConflictEntry('{self.capability_name}' defined in: [{locs}])"


# ---------------------------------------------------------------------------
# FusionRegistry — 单例注册表
# ---------------------------------------------------------------------------


class FusionRegistry:
    """融合注册表（单例）。

    记录每个能力名到唯一实现模块路径的映射。使用线程安全的双重检查
    锁定实现单例模式。

    典型用法::

        registry = FusionRegistry.instance()
        registry.register_implementation("AuthService", "galaxyos.auth.v2", layer="service")
    """

    _instance: FusionRegistry | None = None
    _lock: threading.Lock = threading.Lock()

    def __init__(self) -> None:
        # 内部存储：capability_name → list of (module_path, layer)
        self._entries: dict[str, list[tuple[str, str]]] = {}
        # 融合替换记录：capability_name → list of (old_module.old_class, new_module.new_class, timestamp)
        self._replacements: dict[str, list[tuple[str, str, datetime.datetime]]] = {}

    @classmethod
    def instance(cls) -> FusionRegistry:
        """获取 FusionRegistry 单例。

        Returns:
            FusionRegistry: 全局唯一的注册表实例。
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """重置单例（仅用于测试）。"""
        with cls._lock:
            cls._instance = None

    # ---- 注册 API ----

    def register_implementation(
        self,
        capability_name: str,
        module_path: str,
        layer: str = "",
    ) -> None:
        """注册一个能力实现。

        若同一 capability_name 已被不同 module_path 注册，则抛出
        FusionConflictError。若 module_path 与已有条目相同则忽略（幂等）。

        Args:
            capability_name: 能力的唯一标识名（通常是类名或函数名）。
            module_path: 实现该能力的模块路径，如 ``"galaxyos.auth.v2"``。
            layer: 可选的层级标签，如 ``"service"``、``"core"``。

        Raises:
            FusionConflictError: 同一能力名被不同模块路径重复注册。
        """
        entries = self._entries.setdefault(capability_name, [])

        # 幂等：同一路径重复注册不算冲突
        for existing_path, _ in entries:
            if existing_path == module_path:
                return

        # 冲突检测：不同路径注册同一能力名
        if entries:
            existing_paths = [p for p, _ in entries]
            raise FusionConflictError(
                capability_name=capability_name,
                existing=existing_paths,
                new_entry=module_path,
            )

        entries.append((module_path, layer))

    def record_replacement(
        self,
        capability_name: str,
        old_fqn: str,
        new_fqn: str,
    ) -> None:
        """记录一次融合替换。

        Args:
            capability_name: 能力名。
            old_fqn: 旧实现的完全限定名（``module.Class``）。
            new_fqn: 新实现的完全限定名（``module.Class``）。
        """
        repl = self._replacements.setdefault(capability_name, [])
        repl.append((old_fqn, new_fqn, datetime.datetime.now(tz=datetime.timezone.utc)))

    # ---- 查询 API ----

    def get_implementations(self, capability_name: str) -> list[tuple[str, str]]:
        """查询某能力的所有注册实现。

        Args:
            capability_name: 能力名。

        Returns:
            list[tuple[str, str]]: [(module_path, layer), ...] 列表。
        """
        return list(self._entries.get(capability_name, []))

    def get_replacements(self, capability_name: str) -> list[tuple[str, str, datetime.datetime]]:
        """查询某能力的所有替换记录。

        Args:
            capability_name: 能力名。

        Returns:
            list[tuple[str, str, datetime.datetime]]: [(old_fqn, new_fqn, timestamp), ...]
        """
        return list(self._replacements.get(capability_name, []))

    @property
    def all_capabilities(self) -> set[str]:
        """返回所有已注册的能力名集合。"""
        return set(self._entries.keys())

    # ---- 冲突检测 API ----

    def assert_no_duplicates(self) -> list[ConflictEntry]:
        """扫描注册表，返回所有冲突条目。

        如果某能力名有多个不同模块路径的实现，则视为冲突。

        Returns:
            list[ConflictEntry]: 所有冲突条目。空列表表示无冲突。
        """
        conflicts: list[ConflictEntry] = []
        for name, entries in self._entries.items():
            if len(entries) > 1:
                locations = [p for p, _ in entries]
                conflicts.append(ConflictEntry(capability_name=name, locations=locations))
        return conflicts


# ---------------------------------------------------------------------------
# 顶层便捷函数
# ---------------------------------------------------------------------------


def assert_no_duplicates() -> list[ConflictEntry]:
    """便捷入口：检查 FusionRegistry 中是否有重复注册。

    启动时由 galaxyos/__init__.py 调用。
    返回冲突列表，空列表表示无冲突。
    """
    return FusionRegistry.instance().assert_no_duplicates()


# ---------------------------------------------------------------------------
# @fusion_replace 装饰器
# ---------------------------------------------------------------------------

_T = TypeVar("_T")


def fusion_replace(
    old_module: str,
    old_class: str,
    layer: str = "",
) -> Callable[[_T], _T]:
    """装饰器：标记此类/函数替换了 old_module.old_class。

    被装饰的类或函数会自动在 FusionRegistry 中注册，并记录替换关系。
    若旧实现也已注册，注册时将抛出 FusionConflictError —— 这正是
    守卫机制的核心：**不允许两个实现同时存在，必须用替换语义取代**。

    Args:
        old_module: 旧实现所在的模块路径，如 ``"galaxyos.auth.v1"``。
        old_class: 旧实现的类名/函数名，如 ``"AuthService"``。
        layer: 可选的层级标签。

    Returns:
        装饰器函数。

    典型用法::

        @fusion_replace("galaxyos.auth.v1", "AuthService", layer="service")
        class AuthService:
            ...

        @fusion_replace("galaxyos.utils_old", "cosine_similarity")
        def cosine_similarity(a, b):
            ...

    上述声明表示旧实现已被当前新实现替换。
    """

    def decorator(obj: _T) -> _T:
        # 支持类和函数装饰
        if not isinstance(obj, type) and not callable(obj):
            raise TypeError(
                f"@fusion_replace can only decorate classes or functions, "
                f"got {type(obj).__name__}"
            )

        registry = FusionRegistry.instance()
        capability_name = obj.__name__
        new_module = obj.__module__
        new_fqn = f"{new_module}.{capability_name}"
        old_fqn = f"{old_module}.{old_class}"

        # 注册新实现
        registry.register_implementation(capability_name, new_module, layer=layer)

        # 记录替换关系
        registry.record_replacement(
            capability_name=capability_name,
            old_fqn=old_fqn,
            new_fqn=new_fqn,
        )

        # 在对象上附加元数据，方便运行时查询
        setattr(obj, "_fusion_old_fqn", old_fqn)
        setattr(obj, "_fusion_new_fqn", new_fqn)
        setattr(obj, "_fusion_layer", layer)

        return obj

    return decorator


# ---------------------------------------------------------------------------
# CI 级全量扫描
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DefinitionInfo:
    """AST 扫描得到的定义信息。

    Attributes:
        name: 类名或函数名。
        kind: ``"class"`` 或 ``"function"``。
        module_path: 所在模块的路径（从项目根推导的 Python 模块路径）。
        file_path: 源文件绝对路径。
        lineno: 定义在源文件中的行号。
    """

    name: str
    kind: str
    module_path: str
    file_path: str
    lineno: int


def _file_to_module(file_path: Path, project_root: Path) -> str:
    """将文件路径转换为 Python 模块路径。

    Args:
        file_path: 源文件绝对路径。
        project_root: 项目根目录。

    Returns:
        str: Python 模块路径，如 ``"galaxyos.shared.fusion_guard"``。
    """
    try:
        rel = file_path.relative_to(project_root)
    except ValueError:
        rel = file_path
    parts = list(rel.parts)
    # 去掉 .py 后缀
    if parts and parts[-1].endswith(".py"):
        parts[-1] = parts[-1][:-3]
    # __init__.py → 空末段
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _scan_file(file_path: Path, project_root: Path) -> list[DefinitionInfo]:
    """扫描单个 .py 文件，提取所有顶层 class/function 定义。

    Args:
        file_path: 源文件路径。
        project_root: 项目根目录。

    Returns:
        list[DefinitionInfo]: 该文件中所有顶层定义。
    """
    try:
        source = file_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    try:
        tree = ast.parse(source, filename=str(file_path))
    except SyntaxError:
        return []

    module_path = _file_to_module(file_path, project_root)
    defs: list[DefinitionInfo] = []

    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            defs.append(
                DefinitionInfo(
                    name=node.name,
                    kind="class",
                    module_path=module_path,
                    file_path=str(file_path),
                    lineno=node.lineno,
                )
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            defs.append(
                DefinitionInfo(
                    name=node.name,
                    kind="function",
                    module_path=module_path,
                    file_path=str(file_path),
                    lineno=node.lineno,
                )
            )

    return defs


def scan_fusion_conflicts(project_root: str | Path) -> list[ConflictEntry]:
    """CI 级全量扫描：AST 遍历所有 .py 文件，找出同名定义冲突。

    扫描 project_root 下所有 ``*.py`` 文件，收集顶层 class 和 function
    定义，然后按 ``(kind, name)`` 分组，若同一组内有多个不同模块路径
    的定义，则视为冲突。

    Args:
        project_root: 项目根目录路径。

    Returns:
        list[ConflictEntry]: 所有冲突条目。空列表表示无冲突。
    """
    root = Path(project_root).resolve()

    # 收集所有 .py 文件
    all_defs: list[DefinitionInfo] = []
    for py_file in root.rglob("*.py"):
        all_defs.extend(_scan_file(py_file, root))

    # 按 (kind, name) 分组
    groups: dict[tuple[str, str], list[DefinitionInfo]] = {}
    for d in all_defs:
        key = (d.kind, d.name)
        groups.setdefault(key, []).append(d)

    # 找出冲突：同一 (kind, name) 出现在多个不同模块路径
    conflicts: list[ConflictEntry] = []
    for (kind, name), defs in groups.items():
        unique_modules = {d.module_path for d in defs}
        if len(unique_modules) > 1:
            # 按模块路径排序以保证输出稳定
            locations = sorted(unique_modules)
            conflicts.append(
                ConflictEntry(
                    capability_name=f"{kind}:{name}",
                    locations=locations,
                )
            )

    # 按能力名排序
    conflicts.sort(key=lambda c: c.capability_name)

    # 过滤白名单中的设计允许冲突
    conflicts = [c for c in conflicts if not _is_allowed_conflict(c.capability_name)]

    return conflicts


# ---------------------------------------------------------------------------
# __main__ 入口 — CI 扫描
# ---------------------------------------------------------------------------


def _main() -> None:
    """CLI 入口：执行 CI 级全量融合冲突扫描。"""
    args = sys.argv[1:]

    # --whitelist: 显示当前白名单配置
    if "--whitelist" in args:
        wl = get_whitelist()
        total = sum(len(v) for v in wl.values())
        print(f"GalaxyOS Fusion Guard — whitelist ({total} entries, {len(wl)} categories):\n")
        for cat, names in wl.items():
            print(f"  [{cat}] ({len(names)} entries):")
            for n in sorted(names):
                print(f"    • {n}")
            print()
        sys.exit(0)

    project_root = args[0] if args else "."
    root_path = Path(project_root).resolve()

    if not root_path.is_dir():
        print(f"Error: '{project_root}' is not a directory.", file=sys.stderr)
        sys.exit(2)

    print(f"GalaxyOS Fusion Guard — scanning {root_path} ...")
    conflicts = scan_fusion_conflicts(root_path)

    if not conflicts:
        print("✓ No fusion conflicts detected.")
        sys.exit(0)

    print(f"✗ Found {len(conflicts)} fusion conflict(s):\n")
    for c in conflicts:
        print(f"  • {c.capability_name}")
        for loc in c.locations:
            print(f"      — {loc}")
        print()

    sys.exit(1)


if __name__ == "__main__":
    _main()
