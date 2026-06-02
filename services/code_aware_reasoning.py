"""
代码感知推理引擎 —— AST 分析 + 调用图 + 代码理解

论文参考:
  - CodeBERT (2019) / GraphCodeBERT (2020) — 代码的结构化表示
  - SWE-agent (2024) — AI 自主修改代码并验证
  - Self-Debugging (Chen 2024) — 代码执行→错误分析→自动修正

核心能力:
  1. AST 分析: 解析 Python 代码为抽象语法树，提取类/函数/调用关系
  2. 调用图: 构建函数之间的调用关系图
  3. 代码理解: 用 LLM 分析代码意图和逻辑
  4. 缺陷检测: 常见 Python 代码问题识别
  5. 自动修复: 对检测到的问题提供修复建议

与 ReAct 引擎的关系:
  - 当 ReAct 的 Action 类型为 "analyze_code" 时调用此引擎
  - 结果可以作为 Observation 返回

Author: 小艺 Claw
"""

import ast
import inspect
import json
import re
import time
import logging
import textwrap
from typing import List, Dict, Optional, Any, Tuple, Set
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class CodeFunction:
    """函数/方法信息"""
    name: str = ""
    line_start: int = 0
    line_end: int = 0
    docstring: str = ""
    args: List[str] = field(default_factory=list)
    calls: List[str] = field(default_factory=list)       # 调用了哪些函数
    called_by: List[str] = field(default_factory=list)   # 被哪些函数调用
    complexity: int = 0          # 圈复杂度
    is_async: bool = False
    decorators: List[str] = field(default_factory=list)
    source_code: str = ""

@dataclass
class CodeClass:
    """类信息"""
    name: str = ""
    line_start: int = 0
    line_end: int = 0
    bases: List[str] = field(default_factory=list)
    methods: List[CodeFunction] = field(default_factory=list)
    docstring: str = ""
    decorators: List[str] = field(default_factory=list)

@dataclass
class CodeFile:
    """代码文件分析结果"""
    path: str = ""
    classes: List[CodeClass] = field(default_factory=list)
    functions: List[CodeFunction] = field(default_factory=list)
    imports: List[str] = field(default_factory=list)
    lines: int = 0
    issues: List[Dict] = field(default_factory=list)
    call_graph: Dict[str, List[str]] = field(default_factory=dict)


class CodeAwareReasoningEngine:
    """
    代码感知推理引擎

    用法:
        engine = CodeAwareReasoningEngine(llm_flash=client)
        result = engine.analyze_file("path/to/code.py")
    """

    def __init__(
        self,
        llm_flash=None,
        flash_model: str = "deepseek-v4-flash"
    ):
        self.llm_flash = llm_flash
        self.flash_model = flash_model

    # ═══ 公开接口 ═══

    def analyze_file(self, filepath: str) -> CodeFile:
        """分析一个 Python 文件"""
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                source = f.read()
            return self.analyze_source(source, filepath)
        except Exception as e:
            logger.error(f"读取文件失败 {filepath}: {e}")
            cf = CodeFile(path=filepath)
            cf.issues.append({"type": "io_error", "msg": str(e)})
            return cf

    def analyze_source(self, source: str, path: str = "") -> CodeFile:
        """分析 Python 源码"""
        cf = CodeFile(path=path, lines=len(source.splitlines()))

        try:
            tree = ast.parse(source)

            # 1. 提取 imports
            cf.imports = self._extract_imports(tree)

            # 2. 提取类和函数
            self._walk_tree(tree, source, cf)

            # 3. 构建调用图
            cf.call_graph = self._build_call_graph(cf.classes, cf.functions)

            # 4. 圈复杂度
            for func in cf.functions:
                func.complexity = self._calc_complexity(source[func.line_start:func.line_end])

            # 5. 代码缺陷检测
            cf.issues = self._detect_issues(source, tree, cf)

            return cf

        except SyntaxError as e:
            cf.issues.append({"type": "syntax_error", "msg": str(e)})
            return cf

    def analyze_code_snippet(self, code: str, context: str = "") -> Dict:
        """
        分析代码片段，返回自然语言理解结果

        适用于 ReAct 中的 analyze_code Action
        """
        # 1. AST 分析
        ast_result = self.analyze_source(code)

        # 2. LLM 理解（如果有）
        llm_understanding = ""
        if self.llm_flash:
            try:
                resp = self.llm_flash.chat.completions.create(
                    model=self.flash_model,
                    messages=[{"role": "user", "content":
                        f"分析以下代码的功能和意图。\n\n"
                        f"{'上下文:' + context[:500] if context else ''}\n\n"
                        f"代码:\n```python\n{code[:2000]}\n```\n\n"
                        f"请用中文输出分析:"}],
                    max_tokens=500,
                    temperature=0.2
                )
                llm_understanding = resp.choices[0].message.content.strip()
            except Exception:
                pass

        if ast_result.issues:
            issues_detail = "\n".join([
                f"- {i.get('type','')}: {i.get('msg','')}" for i in ast_result.issues[:5]
            ])
        else:
            issues_detail = "未发现明显问题"

        return {
            "functions": [f.name for f in ast_result.functions],
            "classes": [c.name for c in ast_result.classes],
            "imports": ast_result.imports[:10],
            "issues": issues_detail,
            "understanding": llm_understanding,
            "call_graph": ast_result.call_graph
        }

    def detect_bugs(self, source: str) -> List[Dict]:
        """检测 Python 代码中的常见问题"""
        _, issues = self._analyze_raw(source)
        return issues

    def suggest_fix(self, source: str, issue: Dict) -> str:
        """
        对检测到的问题提出修复建议

        可被 ReAct 的 verify Action 调用
        """
        if not self.llm_flash:
            return "需要 LLM 支持才能提供修复建议"

        try:
            resp = self.llm_flash.chat.completions.create(
                model=self.flash_model,
                messages=[{"role": "user", "content":
                    f"以下代码存在如下问题:\n"
                    f"{issue.get('type','')}: {issue.get('msg','')}\n\n"
                    f"代码:\n```python\n{source[:1500]}\n```\n\n"
                    f"请给出修复方案:"}],
                max_tokens=500,
                temperature=0.2
            )
            return resp.choices[0].message.content.strip()
        except Exception:
            return "无法生成修复建议"

    # ═══ 内部方法 ═══

    def _extract_imports(self, tree: ast.AST) -> List[str]:
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                module = node.module or ""
                for alias in node.names:
                    imports.append(f"{module}.{alias.name}" if module else alias.name)
        return imports

    def _walk_tree(self, tree: ast.AST, source: str, cf: CodeFile):
        """遍历 AST，提取类和函数"""
        function_map = {}

        for node in ast.iter_child_nodes(tree):
            if isinstance(node, ast.ClassDef):
                cls = CodeClass(
                    name=node.name,
                    line_start=node.lineno,
                    line_end=node.end_lineno,
                    bases=[self._get_name(b) for b in node.bases],
                    docstring=ast.get_docstring(node) or "",
                    decorators=[self._get_name(d) for d in node.decorator_list]
                )
                # 提取类方法
                for item in ast.iter_child_nodes(node):
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        func = self._extract_function(item, source)
                        cls.methods.append(func)
                        function_map[func.name] = func
                cf.classes.append(cls)

            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func = self._extract_function(node, source)
                cf.functions.append(func)
                function_map[func.name] = func

            # 顶层赋值/表达式不分析

        # 补全调用关系
        for func in cf.functions:
            function_map[func.name] = func
        for cls in cf.classes:
            for method in cls.methods:
                function_map[f"{cls.name}.{method.name}"] = method

        # 反向填充 called_by
        for func in list(function_map.values()):
            for called in func.calls:
                if called in function_map:
                    if isinstance(function_map[called], list):
                        continue
                    callee = function_map[called]
                    if hasattr(callee, 'called_by'):
                        if func.name not in callee.called_by:
                            callee.called_by.append(func.name)

    def _extract_function(self, node: ast.AST, source: str) -> CodeFunction:
        """从 AST 节点提取函数信息"""
        name = node.name if hasattr(node, 'name') else "?"
        line_start = node.lineno if hasattr(node, 'lineno') else 0
        line_end = node.end_lineno if hasattr(node, 'end_lineno') else line_start

        args = []
        if hasattr(node, 'args') and node.args:
            for arg in node.args.args:
                if hasattr(arg, 'arg'):
                    args.append(arg.arg)

        calls = []
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                if hasattr(child.func, 'attr'):
                    calls.append(child.func.attr)
                elif hasattr(child.func, 'id'):
                    calls.append(child.func.id)

        decorators = []
        if hasattr(node, 'decorator_list'):
            decorators = [self._get_name(d) for d in node.decorator_list]

        source_lines = source.splitlines()
        src = "\n".join(source_lines[line_start - 1:line_end]) if line_end > line_start else ""

        return CodeFunction(
            name=name,
            line_start=line_start,
            line_end=line_end,
            docstring=ast.get_docstring(node) or "",
            args=args,
            calls=list(set(calls)),
            is_async=isinstance(node, ast.AsyncFunctionDef),
            decorators=decorators,
            source_code=src
        )

    def _get_name(self, node: ast.AST) -> str:
        """从 AST 节点提取名字字符串"""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return f"{self._get_name(node.value)}.{node.attr}"
        elif isinstance(node, ast.Subscript):
            return f"{self._get_name(node.value)}[{self._get_name(node.slice)}]"
        elif isinstance(node, ast.Call):
            return self._get_name(node.func)
        elif isinstance(node, ast.Constant):
            return str(node.value)
        elif isinstance(node, ast.Str):
            return node.s
        return str(node)[:30] if node else "?"

    def _build_call_graph(self, classes: List[CodeClass], functions: List[CodeFunction]) -> Dict[str, List[str]]:
        """构建调用图"""
        graph = {}
        for func in functions:
            graph[func.name] = list(set(func.calls))
        for cls in classes:
            for method in cls.methods:
                key = f"{cls.name}.{method.name}"
                graph[key] = list(set(method.calls))
        return graph

    def _calc_complexity(self, code: str) -> int:
        """计算圈复杂度"""
        if not code:
            return 0
        try:
            tree = ast.parse(code)
        except SyntaxError:
            return 0

        complexity = 1
        for node in ast.walk(tree):
            if isinstance(node, (ast.If, ast.While, ast.For, ast.ExceptHandler,
                                 ast.AsyncFor, ast.AsyncWith, ast.Assert)):
                complexity += 1
            elif isinstance(node, ast.BoolOp):
                complexity += len(node.values) - 1
            elif isinstance(node, ast.Match):
                complexity += len(node.cases)
        return complexity

    def _detect_issues(self, source: str, tree: ast.AST, cf: CodeFile) -> List[Dict]:
        """检测代码中的常见问题"""
        issues = []
        source_lines = source.splitlines()

        for node in ast.walk(tree):
            # 1. 空 except
            if isinstance(node, ast.ExceptHandler) and not node.type:
                issues.append({
                    "type": "bare_except",
                    "line": node.lineno,
                    "msg": "裸 except（无异常类型）会捕获 KeyboardInterrupt 等"
                })

            # 2. 可变默认参数
            if isinstance(node, ast.FunctionDef):
                for default in node.args.defaults:
                    if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                        issues.append({
                            "type": "mutable_default",
                            "line": node.lineno,
                            "msg": f"函数 '{node.name}' 有可变默认参数"
                        })

            # 3. 过长的行（>100）
            if hasattr(node, 'lineno') and node.lineno <= len(source_lines):
                line = source_lines[node.lineno - 1]
                if len(line) > 120:
                    issues.append({
                        "type": "long_line",
                        "line": node.lineno,
                        "msg": f"行过长 ({len(line)} 字符)"
                    })

            # 4. print 语句
            if isinstance(node, ast.Call):
                if getattr(node.func, 'id', '') == 'print':
                    # 简化检查：如果 print 在函数内
                    for parent in ast.walk(tree):
                        if isinstance(parent, ast.FunctionDef) and hasattr(node, 'lineno'):
                            if parent.lineno <= node.lineno <= parent.end_lineno:
                                issues.append({
                                    "type": "debug_print",
                                    "line": node.lineno,
                                    "msg": "生产代码中的 print 语句"
                                })
                                break

        # 5. 函数/方法过长
        for func in cf.functions:
            if func.line_end - func.line_start > 100:
                issues.append({
                    "type": "long_function",
                    "line": func.line_start,
                    "msg": f"函数 '{func.name}' 过长 ({func.line_end - func.line_start} 行)"
                })

        return issues[:20]

    def _analyze_raw(self, source: str) -> Tuple[ast.AST, List[Dict]]:
        """返回原始 AST 和问题列表"""
        tree = ast.parse(source)
        cf = CodeFile()
        self._walk_tree(tree, source, cf)
        return tree, cf.issues
