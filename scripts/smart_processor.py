#!/usr/bin/env python3
"""
智能处理层 (Smart Processor) — 统一路由层 v3.0.0

三模型通道（Flash / Pro / VLM）+ 人物人格注入 + R-CCAM 完整路由。

v3.0.0 变更：
- 已去除独立 LLM 客户端初始化，完全依赖外部注入
- Flash/Pro/DAG 配置由调用方传入，不再读 llm_config.json
- VLM 仍独立（GLM-4V-Plus 非 DeepSeek 系）
- KV 缓存复用保留

功能：
1. 查询改写（Pro）
2. 多源检索（向量 + DAG + Web）
3. 结果总结（Flash）
4. 回答合成（Flash + 人格）
5. VLM 图像理解（GLM-4V-Plus）

Author: 小艺 Claw
Version: 3.0.0
"""

import sys, json, os
from pathlib import Path
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

# VLM 第三通道 — glm-4v-plus
VLM_API_KEY = "3b94029d5a044474bf41d4f8825881b0.VULoxcszSQigtVsX"
VLM_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
VLM_MODEL = "glm-4v-plus"

logger = None
try:
    import logging
    logger = logging.getLogger("SmartProcessor")
except Exception:
    pass

def _log(msg: str):
    if logger:
        logger.info(msg)

# LLM 调用线程池 — 全局共享，最多 4 个并发 LLM 调用
_llm_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="llm")

# LLM 调用默认超时（秒）
_LLM_TIMEOUT = 60

# KV 缓存 user_id（统一前缀，复用 DeepSeek prefix cache）
_KV_USER_ID = "xiaoyi-claw-smart-pro-001"


class SmartProcessor:
    """智能处理层 — 三模型通道 + 人格注入 + R-CCAM 路由"""

    def __init__(
        self,
        llm_flash=None,
        llm_pro=None,
        persona_context: str = "",
        llm_pro_model: str = "",
        llm_flash_model: str = "",
        llm_pro_temperature: float = None,
        llm_pro_timeout: int = None,
        llm_flash_temperature: float = None,
    ):
        self.persona_context = persona_context

        # 外部注入的 LLM 客户端（必须，不能为空）
        self._ext_flash = llm_flash
        self.llm_pro = llm_pro

        # 模型名和参数，由调用方指定（不读配置文件）
        self._flash_model = llm_flash_model or "deepseek-v4-flash"
        self._pro_model = llm_pro_model or "deepseek-v4-pro"
        self._flash_temperature = llm_flash_temperature if llm_flash_temperature is not None else 0.3
        self._pro_temperature = llm_pro_temperature if llm_pro_temperature is not None else 0.7
        self._pro_timeout = llm_pro_timeout or 180

        # VLM 第三通道（GLM-4V-Plus，独立于 DeepSeek 客户端）
        self.vlm = None
        try:
            from openai import OpenAI as OpenAIClient
            self.vlm = OpenAIClient(
                api_key=VLM_API_KEY,
                base_url=VLM_BASE_URL,
                timeout=_LLM_TIMEOUT,
            )
        except Exception:
            pass

    # ──────────────────────────────────────────────
    # KV 缓存 — 统一的 extra_body 构造
    # ──────────────────────────────────────────────

    def _kv_extra(self, model_type: str = "flash") -> dict:
        """构造 KV 缓存复用参数。"""
        return {"prefix": True, "user_id": _KV_USER_ID}

    # ──────────────────────────────────────────────
    # LLM 调用封装（带超时 + 并发管理）
    # ──────────────────────────────────────────────

    def _call_llm(self, client_callable, timeout: int = _LLM_TIMEOUT):
        """通过线程池执行 LLM 调用，带超时保护。"""
        future = _llm_pool.submit(client_callable)
        try:
            return future.result(timeout=timeout)
        except FuturesTimeout:
            _log(f"LLM 调用超时 ({timeout}s)，取消")
            future.cancel()
            return None
        except Exception:
            return None

    # ──────────────────────────────────────────────
    # Flash / Pro 调用
    # ──────────────────────────────────────────────

    def _call_flash(self, messages: list, max_tokens: int = 500,
                     temperature: float = None, persona: str = "") -> str:
        flash = self._ext_flash
        if not flash:
            return ""
        _sys_prefix = "你是一个智能助手。"
        if persona:
            _sys_prefix = f"=== 助手人格 ===\n{persona[:800]}\n\n{_sys_prefix}"
        full_messages = [{"role": "system", "content": _sys_prefix}] + messages
        try:
            temp = temperature if temperature is not None else self._flash_temperature
            kwargs = {"extra_body": self._kv_extra("flash")}
            def _do():
                return flash.chat.completions.create(
                    model=self._flash_model,
                    messages=full_messages,
                    max_tokens=max_tokens,
                    temperature=temp,
                    **kwargs,
                )
            resp = self._call_llm(_do)
            if resp:
                return resp.choices[0].message.content or ""
        except Exception:
            pass
        return ""

    def _call_pro(self, messages: list, max_tokens: int = 500,
                   temperature: float = None, persona: str = "") -> str:
        if not self.llm_pro:
            return ""
        try:
            temp = temperature if temperature is not None else self._pro_temperature
            extra = self._kv_extra("pro")

            _sys = "你是一个信息处理助手，按要求完成任务。"
            if persona:
                _sys = f"=== 助手人格 ===\n{persona[:800]}\n\n{_sys}"

            has_system = any(m.get("role") == "system" for m in messages)
            if not has_system:
                messages = [{"role": "system", "content": _sys}] + messages

            def _do():
                return self.llm_pro.chat.completions.create(
                    model=self._pro_model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temp,
                    extra_body=extra,
                )
            resp = self._call_llm(_do, timeout=self._pro_timeout)
            if resp:
                return resp.choices[0].message.content or ""
        except Exception:
            pass
        return ""

    def set_persona(self, persona_context: str):
        self.persona_context = persona_context

    # ──────────────────────────────────────────────
    # 核心路由方法
    # ──────────────────────────────────────────────

    def rewrite_query(self, query: str, persona: str = "") -> str:
        """查询改写 — Pro 模型"""
        if len(query) < 10:
            return query
        _persona = persona or self.persona_context
        _sys = "将以下用户查询改写为更适合记忆检索的表述，保留核心关键词和意图，去除口语化修饰。直接输出改写结果，不要额外解释。"
        if _persona:
            _sys = f"=== 助手人格 ===\n{_persona[:800]}\n\n{_sys}"
        messages = [
            {"role": "system", "content": _sys},
            {"role": "user", "content": f"原始查询: {query}\n改写:"},
        ]
        try:
            result = self._call_pro(messages, max_tokens=150,
                                     temperature=0.3, persona=_persona)
            rewritten = result.strip().strip('"\'')
            if rewritten:
                return rewritten[:200]
        except Exception:
            pass
        return query

    def summarize_results(self, query: str, results: list,
                           max_length: int = 500, persona: str = "") -> str:
        """结果总结 — Flash 模型"""
        if not results:
            return "未找到相关记忆"
        if len(results) == 1:
            return results[0].get("content", "")
        context = "\n".join([f"- {r.get('content', '')[:200]}" for r in results[:5]])
        _persona = persona or self.persona_context
        messages = [{"role": "user",
                     "content": f"用户问题: {query}\n检索结果:\n{context}\n总结:"}]
        try:
            result = self._call_flash(messages, max_tokens=max_length,
                                       temperature=0.3, persona=_persona)
            return result.strip()
        except Exception:
            return "\n".join([f"- {r.get('content', '')[:200]}" for r in results[:3]])

    def answer_synthesis(self, query: str, context: str,
                          persona: str = "", max_tokens: int = 1024) -> str:
        """回答合成 — Flash 模型"""
        _persona = persona or self.persona_context
        if not context:
            return ""
        flash = self._ext_flash
        if not flash:
            return ""
        try:
            _sys = "你是一个智能助手。根据提供的参考资料回答用户的问题。回答要简洁准确，只基于参考资料中的信息。如果参考资料不足以回答问题，请如实说明。"
            if _persona:
                _sys = f"=== 助手人格 ===\n{_persona[:800]}\n\n{_sys}"
            kwargs = {"extra_body": self._kv_extra("flash")}
            def _do():
                return flash.chat.completions.create(
                    model=self._flash_model,
                    messages=[
                        {"role": "system", "content": _sys},
                        {"role": "user",
                         "content": f"用户问题: {query[:300]}\n\n参考资料:\n{context[:3000]}\n\n请根据以上参考资料回答用户问题:"},
                    ],
                    max_tokens=max_tokens,
                    temperature=0.3,
                    **kwargs,
                )
            resp = self._call_llm(_do)
            if resp:
                return resp.choices[0].message.content.strip()
        except Exception:
            pass
        return ""

    def process_rccam(self, state, llm_flash=None, llm_pro=None) -> Dict[str, Any]:
        """
        R-CCAM 路由入口 — SmartProcessor

        执行链:
          1. Pro 查询改写
          2. retrieval_hub 多源检索
          3. Flash 结果总结
          4. Flash 回答合成
        """
        if llm_flash:
            self._ext_flash = llm_flash
        if llm_pro:
            self.llm_pro = llm_pro

        query = state.user_input
        _persona = self.persona_context or ''
        _state_analysis = getattr(state, 'analysis', {})
        if isinstance(_state_analysis, dict) and _state_analysis.get('persona_context'):
            _persona = _state_analysis['persona_context']
        elif _state_analysis and hasattr(_state_analysis, 'get'):
            _persona = _state_analysis.get('persona_context', _persona)
        _persona = _persona or ''

        result = {
            "rewritten_query": query,
            "flash_summary": "",
            "hub_context": "",
            "generated_answer": "",
            "confidence": 0.0,
        }

        # 1. Pro 查询改写
        rewritten = query
        if self.llm_pro and len(query) > 5:
            try:
                rewritten = self.rewrite_query(query, persona=_persona)
                result["rewritten_query"] = rewritten
                _log(f"Pro 查询改写: '{query}' → '{rewritten}'")
            except Exception:
                pass

        # 2. 多源检索
        hub_results = []
        try:
            from retrieval_hub import retrieval_hub
            hub_result = retrieval_hub(rewritten, top_k=12, include_web=False)
            hub_results = hub_result.get("results", [])
        except ImportError:
            pass

        # 3. Flash 结果总结 + 上下文准备
        context_parts = []
        for j, r in enumerate(hub_results[:16]):
            content = r.get('content', r.get('user_text', ''))[:1500]
            if content:
                context_parts.append(f"[{j+1}] {content}")
        hub_context = "\n".join(context_parts)[:3000]

        dag_ctx = (getattr(state, 'analysis', {}) or {}).get('current_dag_context', '')
        if dag_ctx:
            hub_context = f"[DAG 上下文]\n{dag_ctx[:2000]}\n\n{hub_context}"

        result["hub_context"] = hub_context

        if hub_results:
            try:
                summary = self.summarize_results(rewritten, [
                    {"content": r.get('content', r.get('user_text', ''))[:300]}
                    for r in hub_results[:5]
                ], persona=_persona)
                result["flash_summary"] = summary
            except Exception:
                pass

        # 4. Flash 回答合成
        ctx_for_answer = hub_context or result.get("flash_summary", "") or ""
        if ctx_for_answer:
            try:
                answer = self.answer_synthesis(query, ctx_for_answer,
                                                persona=_persona)
                result["generated_answer"] = answer
                result["confidence"] = 0.6 if answer else 0.0
            except Exception:
                pass

        return result

    def process(self, query: str, top_k: int = 5,
                 rewrite: bool = True, summarize: bool = True,
                 persona: str = "") -> dict:
        """智能处理入口（兼容旧接口）"""
        _persona = persona or self.persona_context
        rewritten = self.rewrite_query(query, persona=_persona) if rewrite else query
        results = self._recall(rewritten, top_k)
        summary = self.summarize_results(query, results, persona=_persona) if summarize else ""
        return {
            "query": query,
            "rewritten": rewritten,
            "results": results,
            "summary": summary,
            "result_count": len(results),
        }

    def _recall(self, query: str, top_k: int = 5) -> list:
        try:
            from retrieval_hub import retrieval_hub
            hub_result = retrieval_hub(query, top_k=top_k)
            return hub_result.get("results", [])
        except ImportError:
            pass
        try:
            if hasattr(self, 'xiaoyi_claw') and self.xiaoyi_claw:
                return self.xiaoyi_claw.recall(query, top_k=top_k) or []
        except Exception:
            pass
        return []

    def understand_image(self, image_url: str,
                          prompt: str = "请详细描述这张图片的内容",
                          max_tokens: int = 1000) -> dict:
        return self._call_vlm(prompt, image_url, max_tokens)

    def _call_vlm(self, prompt: str, image_url: str,
                   max_tokens: int = 1000,
                   temperature: float = 0.7) -> dict:
        if not self.vlm:
            return {"content": "", "success": False,
                    "error": "VLM client not initialized"}
        try:
            resp = self.vlm.chat.completions.create(
                model=VLM_MODEL,
                messages=[{"role": "user", "content": [
                    {"type": "image_url", "image_url": {"url": image_url}},
                    {"type": "text", "text": prompt},
                ]}],
                max_tokens=max_tokens,
                temperature=temperature,
            )
            msg = resp.choices[0].message
            content = msg.content or ""
            reasoning = getattr(msg, "reasoning_content", None) or ""
            if not content and reasoning:
                content = reasoning
            return {"content": content, "model": "glm-4v-plus",
                    "success": True, "error": ""}
        except Exception as e:
            return {"content": "", "model": VLM_MODEL,
                    "success": False, "error": str(e)}
