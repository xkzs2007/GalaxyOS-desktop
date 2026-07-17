#!/usr/bin/env python3
"""
RLM (Recursive Language Model) — REPL 环境

允许 LLM 将超长 prompt 当作外部环境变量，通过 Python REPL
符号式切片、分解、递归调用自身处理。

参考: https://arxiv.org/abs/2512.24601
"""

import os
import sys
import json
import time
import ast
import traceback
from typing import Dict, List, Any, Optional, Callable
from pathlib import Path
import logging
from galaxyos.shared.paths import workspace

logger = logging.getLogger("rlm_env")

# ── 安全受限的 REPL 执行器 ──

_BLOCKED_MODULES = {
    "os": ["system", "popen", "fork", "exec", "kill", "remove", "rmdir", "unlink", "chmod", "chown"],
    "subprocess": None,  # 全部禁止
    "shutil": None,
    "signal": None,
    "ctypes": None,
    "multiprocessing": None,
    "socket": None,
    "requests": None,
}

_ALLOWED_BUILTINS = {
    "abs": abs, "all": all, "any": any, "bool": bool, "chr": chr,
    "dict": dict, "enumerate": enumerate, "filter": filter, "float": float,
    "format": format, "frozenset": frozenset, "int": int, "isinstance": isinstance,
    "len": len, "list": list, "map": map, "max": max, "min": min,
    "ord": ord, "pow": pow, "print": print, "range": range,
    "repr": repr, "reversed": reversed, "round": round, "set": set,
    "slice": slice, "sorted": sorted, "str": str, "sum": sum,
    "tuple": tuple, "type": type, "zip": zip, "open": open,
    "iter": iter, "next": next,
}

_SAFE_GLOBALS = {"__builtins__": {k: v for k, v in _ALLOWED_BUILTINS.items()}}


def _make_safe_globals(custom_vars: dict = None) -> dict:
    """创建安全的执行环境"""
    g = dict(_SAFE_GLOBALS)
    if custom_vars:
        g.update(custom_vars)
    g["import"] = _safe_import
    return g


def _safe_import(name: str):
    """受限导入"""
    if name in _BLOCKED_MODULES:
        raise ImportError(f"模块 {name} 被限制导入（RLM 安全策略）")
    return __import__(name)


class RLMEnvironment:
    """
    RLM REPL 环境

    prompt 作为外部变量存储，模型通过 REPL 写代码切片/递归处理。
    """

    def __init__(self, prompt: str, workspace: str = None):
        self.prompt = prompt
        self.prompt_len = len(prompt)
        self._variables = {}       # REPL 变量存储
        self._callbacks = {}       # 递归回调注册
        self._exec_count = 0
        self._output = []
        self._start_time = time.time()

        if workspace is None:
            workspace = os.environ.get("OPENCLAW_WORKSPACE",
                                       str(Path(workspace())))
        self.workspace = workspace

    @property
    def meta_context(self) -> str:
        """返回给模型的 meta 信息（很小，不占 context）"""
        return (
            f"prompt_len={self.prompt_len}\n"
            f"prompt_prefix={self.prompt[:200]!r}...\n"
            f"vars={list(self._variables.keys())}\n"
        )

    def register_recursive_call(self, name: str, callback: Callable):
        """注册递归回调 — 模型可以调 rlm('子prompt') 递归处理"""
        self._callbacks[name] = callback

    def exec_repl(self, code: str) -> str:
        """
        执行一段 Python 代码，返回 stdout 和新增变量。

        关键限制:
        - 每段代码的 stdout 只保留 prefix + 长度（不是全文）
        - 变量存储在 _variables 中
        """
        self._exec_count += 1
        old_stdout = sys.stdout

        from io import StringIO
        buf = StringIO()
        sys.stdout = buf

        result = {"ok": True, "output": "", "new_vars": [], "error": ""}

        try:
            # 构建可用变量环境
            env_vars = {
                "P": self.prompt,           # 完整 prompt
                "P_LEN": self.prompt_len,   # prompt 长度
                "rlm": self._rlm_func,      # 递归函数
                "slice": self._slice_func,  # 切片函数
                "vars": self._variables,    # REPL 变量
            }
            env_vars.update(self._variables)
            safe_globals = _make_safe_globals(env_vars)

            # 执行代码
            compiled = compile(code, "<rlm_repl>", "exec",
                               flags=ast.PyCF_ONLY_AST | ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
            code_obj = compile(compiled, "<rlm_repl>", "exec")
            exec(code_obj, safe_globals)

            # 收集新变量
            before_keys = set(self._variables.keys())
            # 从 safe_globals 中提取用户定义的变量（不包括内置的）
            user_vars = {k: v for k, v in safe_globals.items()
                        if k not in env_vars and not k.startswith("_") and k != "import"}
            self._variables.update(user_vars)
            new_keys = set(self._variables.keys()) - before_keys

            result["new_vars"] = list(new_keys)

        except Exception as e:
            result["ok"] = False
            result["error"] = f"{type(e).__name__}: {e}"
            traceback.print_exc(file=buf)
        finally:
            sys.stdout = old_stdout
            raw_output = buf.getvalue()
            # 只保留 prefix + 长度
            if len(raw_output) > 200:
                result["output"] = raw_output[:200] + f"\n... (truncated, total {len(raw_output)} chars)"
            else:
                result["output"] = raw_output

        self._output.append({
            "exec_no": self._exec_count,
            "code_len": len(code),
            "result": result
        })

        return json.dumps(result, ensure_ascii=False)

    def _slice_func(self, start: int, end: int) -> str:
        """切片 prompt"""
        start = max(0, min(start, self.prompt_len))
        end = max(start, min(end, self.prompt_len))
        return self.prompt[start:end]

    def _rlm_func(self, name: str, sub_prompt: str) -> str:
        """递归调用 — 按 name 查找回调，处理子 prompt"""
        if name in self._callbacks:
            # 有注册的回调，调用它
            result = self._callbacks[name](sub_prompt)
            return json.dumps({"ok": True, "result": str(result), "len": len(sub_prompt)})
        # 默认：存为变量
        key = f"_rlm_seg_{self._exec_count}_{int(time.time() * 1000) % 10000}"
        self._variables[key] = sub_prompt
        return json.dumps({"ok": True, "stored_as": key, "len": len(sub_prompt)})

    def get_var(self, name: str, default=None):
        return self._variables.get(name, default)

    def get_summary(self) -> dict:
        return {
            "exec_count": self._exec_count,
            "variables": list(self._variables.keys()),
            "prompt_len": self.prompt_len,
            "elapsed": time.time() - self._start_time,
            "output_log": self._output[-5:] if self._output else [],
        }


class RLMProcessor:
    """
    RLM 处理器 — 集成到 GalaxyOS pipeline

    作为 Worker 的一个 UDS 端点暴露。
    """

    def __init__(self, llm_flash=None, llm_pro=None):
        self.llm_flash = llm_flash
        self.llm_pro = llm_pro
        self._max_depth = 3          # 最大递归深度
        self._max_root_iters = 20    # 根循环最大迭代次数
        self._max_tokens_per_seg = 8000  # 每个子片段的 token 上限

    def process(self, prompt: str, depth: int = 0) -> str:
        """
        主入口: 递归处理超长 prompt

        流程:
        1. 初始化 REPL 环境
        2. 给 LLM 发 meta 信息（只有长度+前缀）
        3. LLM 写代码切片/递归
        4. 执行代码，返回结果
        """
        if depth > self._max_depth:
            logger.warning(f"RLM 已达最大递归深度 ({depth})，截断处理")
            return self._truncate_and_process(prompt)

        if len(prompt) < self._max_tokens_per_seg:
            # 足够短，直接返回
            return prompt

        env = RLMEnvironment(prompt)
        env.register_recursive_call("rlm_process",
                                     lambda sub: self.process(sub, depth + 1))

        # 构建给 LLM 的 meta prompt
        meta = (
            "你是一个 RLM（Recursive Language Model）。\n"
            "用户输入已作为外部变量 P 存储在 REPL 环境中。\n"
            f"P 的长度 = {len(prompt)} 字符。\n\n"
            "可用工具:\n"
            "  slice(start, end) → P[start:end] 切片（原地返回）\n"
            "  rlm(sub_prompt) → 递归处理一段子内容\n"
            "  print() → 输出到最终结果\n"
            "  vars['key'] = value → 存储中间变量\n\n"
            "规则:\n"
            "1. 不要试图一次读完 P，应该切片处理\n"
            "2. 用 rlm() 递归处理复杂子片段\n"
            "3. 最终结果存入变量 Final\n"
            "4. 每次迭代只返回代码，不要自然语言回复\n\n"
            f"环境状态:\n{env.meta_context}\n\n"
            "请生成 Python 代码来处理这个超长输入:"
        )

        # 根循环
        final_result = None
        for iteration in range(self._max_root_iters):
            # 调 LLM 生成代码
            code = self._call_llm_for_code(meta, env)
            if not code:
                break

            # 执行代码
            result = env.exec_repl(code)
            result_obj = json.loads(result)

            # 检查是否设置了 Final
            final_val = env.get_var("Final")
            if final_val is not None:
                final_result = str(final_val)
                break

            # 更新 meta 给下一次迭代
            meta = (
                f"迭代 {iteration + 1} 完成。\n"
                f"环境状态:\n{env.meta_context}\n"
                f"上次执行结果: {result_obj.get('output', '')[:300]}\n"
                "请继续生成代码处理:"
            )

        if final_result is not None:
            return final_result

        # 兜底: 如果 RLM 没设置 Final，取所有输出拼接
        outputs = [o["result"].get("output", "") for o in env._output]
        combined = "\n".join(outputs)
        return combined if combined else self._truncate_and_process(prompt)

    def _call_llm_for_code(self, meta: str, env: RLMEnvironment) -> Optional[str]:
        """调 LLM 生成 Python 代码"""
        try:
            if self.llm_pro:
                resp = self.llm_pro.chat.completions.create(
                    model="xiaoyiprovider/LLM_DeepSeekV4_Pro",
                    messages=[
                        {"role": "system", "content": "你是一个 RLM 代码生成器。只输出可执行的 Python 代码，不要其他内容。"},
                        {"role": "user", "content": meta},
                    ],
                    temperature=0.3,
                    max_tokens=4000,
                )
                code = resp.choices[0].message.content.strip()
            elif self.llm_flash:
                resp = self.llm_flash.chat.completions.create(
                    model="xiaoyiprovider/LLM_DeepSeekV4_Flash",
                    messages=[
                        {"role": "system", "content": "你是一个 RLM 代码生成器。只输出 Python 代码。"},
                        {"role": "user", "content": meta},
                    ],
                    temperature=0.3,
                    max_tokens=4000,
                )
                code = resp.choices[0].message.content.strip()
            else:
                return None

            # 提取代码块
            if "```python" in code:
                code = code.split("```python")[1].split("```")[0].strip()
            elif "```" in code:
                code = code.split("```")[1].split("```")[0].strip()

            return code if code else None
        except Exception as e:
            logger.error(f"RLM LLM 调用失败: {e}")
            return None

    def _truncate_and_process(self, text: str) -> str:
        """兜底截断处理"""
        max_chars = self._max_tokens_per_seg * 4
        if len(text) > max_chars:
            return text[:max_chars] + f"\n\n[... 截断, 原文 {len(text)} 字符]"
        return text


# ── Fast path: 无需 LLM 的自动切片合并 ──

class FastRLMProcessor:
    """
    快速 RLM 处理器 - 不需要 LLM 调用

    使用滑动窗口 + 自动分段策略:
    1. 按段落/句子边界分段
    2. 每段独立处理/摘要
    3. 合并结果
    """

    def __init__(self, chunk_size: int = 6000, overlap: int = 200):
        self.chunk_size = chunk_size
        self.overlap = overlap

    def process(self, text: str, processor: Callable = None) -> List[str]:
        """
        分割长文本并处理每段

        Args:
            text: 输入文本
            processor: 每段的处理函数，None 则直接返回片段

        Returns:
            处理后的片段列表
        """
        chunks = self._smart_split(text)
        results = []
        for chunk in chunks:
            if processor:
                result = processor(chunk)
            else:
                result = chunk
            results.append(result)
        return results

    def _smart_split(self, text: str) -> List[str]:
        """智能分段 - 按段落边界"""
        if len(text) <= self.chunk_size:
            return [text]

        chunks = []
        paragraphs = text.split("\n\n")
        current = ""

        for para in paragraphs:
            if len(current) + len(para) < self.chunk_size:
                current += (para + "\n\n")
            else:
                if current:
                    chunks.append(current.strip())
                current = para + "\n\n"

        if current:
            chunks.append(current.strip())

        return chunks
