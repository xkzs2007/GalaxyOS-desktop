#!/usr/bin/env python3
"""
智能处理层 (Smart Processor) — 统一路由层

三模型通道（Flash / Pro / VLM）+ 人物人格注入 + R-CCAM 完整路由。

功能：
1. 查询改写（Pro）— 将口语化查询改为检索友好格式
2. 多源检索（向量 + DAG + Web）— 通过 retrieval_hub 统一入口
3. 结果总结（Flash）— 证据摘要
4. 回答合成（Flash）— 带人格和参考资料的最终回答
5. VLM 图像理解（llm_config.json 路由）— 第三通道

架构定位 — Layer 4:
  R-CCAM _action_phase 不再内联 Flash/Pro 调用，统一走 SmartProcessor。
  Worker 通过 RPC 暴露 smart_processor 端点，CLI 也可独立调用。

Author: GalaxyOS
Version: 2.0.0 (R-CCAM 统一路由)
"""

import sys
import json
import os
from pathlib import Path
from typing import Dict, Any, List, Optional

# VLM 第三通道 — 从 llm_config.json 读取

CORE = Path.home() / ".openclaw/workspace/skills/galaxyos-engine/skills/llm-memory-integration/core"
sys.path.insert(0, str(CORE))

try:
    from llm_client import LLMClient, load_config
except ImportError as e:
    print(f"模块导入失败: {e}")
    sys.exit(1)

logger = None
try:
    import logging
    logger = logging.getLogger("SmartProcessor")
except Exception:
    pass

def _log(msg: str):
    if logger:
        logger.info(msg)


class SmartProcessor:
    """智能处理层 — 三模型通道 + 人格注入 + R-CCAM 路由

    可接受外部 llm_flash/llm_pro 实例（来自 AgentCoreBridge），
    避免重复初始化 OpenAI 客户端。未提供时自动创建。
    """

    def __init__(
        self,
        llm_flash=None,
        llm_pro=None,
        persona_context: str = "",
    ):
        self.persona_context = persona_context

        # 外部注入的 LLM 客户端（优先使用）
        self._ext_flash = llm_flash
        self._ext_pro = llm_pro

        # 配置（用于降级创建或外部未提供时）
        config = load_config()
        self._config = config
        pro_cfg = config.get("llm_pro", {})

        # Flash — 批量检索/降级
        self.llm = LLMClient()

        # Pro — 查询改写/结果总结（仅外部未提供时创建）
        self.llm_pro = llm_pro
        self._pro_model = pro_cfg.get("model", "deepseek-v4-pro")
        self._pro_temperature = pro_cfg.get("temperature", 0.7)
        self._pro_timeout = pro_cfg.get("timeout", 180)
        if self.llm_pro is None and pro_cfg.get("api_key"):
            try:
                from openai import OpenAI
                self.llm_pro = OpenAI(
                    api_key=pro_cfg["api_key"],
                    base_url=pro_cfg.get("base_url", "https://api.deepseek.com"),
                )
            except Exception:
                pass

        # VLM — 图像理解（从 llm_config.json 读取配置）
        self.vlm = None
        self.vlm_model = ""
        vlm_cfg = config.get("vlm", {})
        vlm_key = vlm_cfg.get("api_key", "")
        vlm_url = vlm_cfg.get("base_url", "")
        vlm_model = vlm_cfg.get("model", "")
        if vlm_key and vlm_url and vlm_model:
            try:
                from openai import OpenAI as OpenAIClient
                self.vlm = OpenAIClient(
                    api_key=vlm_key,
                    base_url=vlm_url,
                )
                self.vlm_model = vlm_model
            except Exception:
                pass

    def _get_flash(self):
        """获取 Flash 客户端（优先外部注入）"""
        return self._ext_flash

    def _get_flash_model(self) -> str:
        """获取 Flash 模型名"""
        if self._ext_flash and hasattr(self._ext_flash, '_model_hint'):
            return getattr(self._ext_flash, '_model_hint', 'deepseek-v4-flash')
        return self._config.get('llm', {}).get('model', 'deepseek-v4-flash')

    def _call_flash(self, messages: list, max_tokens: int = 500,
                     temperature: Optional[float] = None, persona: str = "") -> str:
        """调用 Flash 模型，支持人格注入"""
        flash = self._get_flash()
        if flash:
            # 使用外部 Flash 客户端（OpenAI 格式）
            _sys_prefix = "你是一个智能助手。"
            if persona:
                _sys_prefix = f"=== 助手人格 ===\n{persona[:800]}\n\n{_sys_prefix}"
            full_messages = [{"role": "system", "content": _sys_prefix}] + messages
            try:
                model = self._get_flash_model()
                resp = flash.chat.completions.create(
                    model=model,
                    messages=full_messages,
                    max_tokens=max_tokens,
                    temperature=temperature if temperature is not None else 0.3,
                )
                return resp.choices[0].message.content or ""
            except Exception:
                pass

        # 降级到内置 LLMClient
        try:
            return self.llm.chat(messages, max_tokens=max_tokens,
                                temperature=temperature) or ""
        except Exception:
            return ""

    def _call_pro(self, messages: list, max_tokens: int = 500,
                   temperature: Optional[float] = None, persona: str = "") -> str:
        """调用 Pro 模型，支持人格注入 + KV 缓存"""
        if not self.llm_pro:
            return ""
        try:
            temp = temperature if temperature is not None else self._pro_temperature
            extra = {"prefix": True, "user_id": "pro_kv_user_id"}

            _sys = "你是一个信息处理助手，按要求完成任务。"
            if persona:
                _sys = f"=== 助手人格 ===\n{persona[:800]}\n\n{_sys}"

            has_system = any(m.get("role") == "system" for m in messages)
            if not has_system:
                messages = [{"role": "system", "content": _sys}] + messages

            kwargs = {"extra_body": extra}
            resp = self.llm_pro.chat.completions.create(
                model=self._pro_model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temp,
                **kwargs,
            )
            return resp.choices[0].message.content or ""
        except Exception:
            return ""

    def set_persona(self, persona_context: str):
        """设置人格上下文"""
        self.persona_context = persona_context

    # ──────────────────────────────────────────────
    # 核心路由方法
    # ──────────────────────────────────────────────

    def rewrite_query(self, query: str, persona: str = "") -> str:
        """查询改写 — Pro 模型，带人格注入"""
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
        # 降级到 Flash
        try:
            result = self.llm.chat([{"role": "user", "content": f"改写查询: {query}"}],
                                   max_tokens=150, temperature=0.3)
            rewritten = result.strip().strip('"\'')
            if rewritten:
                return rewritten[:200]
        except Exception:
            pass
        return query

    def summarize_results(self, query: str, results: list,
                           max_length: int = 500, persona: str = "") -> str:
        """结果总结 — Flash 模型，带人格注入"""
        if not results:
            return "未找到相关记忆"
        if len(results) == 1:
            return results[0]["content"]

        context = "\n".join([f"- {r['content'][:200]}" for r in results[:5]])
        _persona = persona or self.persona_context
        prompt = ("请根据以下检索结果，总结核心要点回答用户问题。"
                  "保持信息准确，不做无根据补充。")
        messages = [{"role": "user",
                     "content": f"用户问题: {query}\n检索结果:\n{context}\n总结:"}]
        try:
            result = self._call_flash(messages, max_tokens=max_length,
                                       temperature=0.3, persona=_persona)
            return result.strip()
        except Exception:
            return "\n".join([f"- {r['content'][:200]}" for r in results[:3]])

    def answer_synthesis(self, query: str, context: str,
                          persona: str = "", max_tokens: int = 1024) -> str:
        """回答合成 — Flash 模型，带人格 + 参考资料"""
        _persona = persona or self.persona_context
        if not context:
            return ""
        try:
            flash = self._get_flash()
            if not flash:
                return ""
            model = self._get_flash_model()
            _sys = "你是一个智能助手。根据提供的参考资料回答用户的问题。回答要简洁准确，只基于参考资料中的信息。如果参考资料不足以回答问题，请如实说明。"
            if _persona:
                _sys = f"=== 助手人格 ===\n{_persona[:800]}\n\n{_sys}"
            resp = flash.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": _sys},
                    {"role": "user",
                     "content": f"用户问题: {query[:300]}\n\n参考资料:\n{context[:3000]}\n\n请根据以上参考资料回答用户问题:"},
                ],
                max_tokens=max_tokens,
                temperature=0.3,
            )
            return resp.choices[0].message.content.strip()
        except Exception:
            return ""

    def process_rccam(self, state, llm_flash=None, llm_pro=None) -> Dict[str, Any]:
        """
        R-CCAM 路由入口 — 替代 _action_phase 内联 Flash/Pro 调用。

        执行链:
          1. Pro 查询改写（带人格）
          2. retrieval_hub 多源检索（向量 + DAG + 论文引擎）
          3. Flash 结果总结（证据摘要）
          4. Flash 回答合成（带人格 + 参考资料）

        Args:
            state: R-CCAM PhaseState
            llm_flash: 外部 Flash 客户端（覆盖 self._ext_flash）
            llm_pro: 外部 Pro 客户端（覆盖 self.llm_pro）

        Returns:
            dict with rewritten_query / flash_summary / generated_answer / confidence / hub_context
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

        # Flash 摘要
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
        """单路向量检索（兼容旧 process 接口）"""
        try:
            from retrieval_hub import retrieval_hub
            hub_result = retrieval_hub(query, top_k=top_k)
            return hub_result.get("results", [])
        except ImportError:
            pass

        # 降级
        try:
            if hasattr(self, '_recall_via_bridge'):
                return self._recall_via_bridge(query, top_k=top_k) or []
        except Exception:
            pass
        return []

    def understand_image(self, image_url: str,
                          prompt: str = "请详细描述这张图片的内容",
                          max_tokens: int = 1000) -> dict:
        """图像理解入口 — VLM 第三通道"""

        return self._call_vlm(prompt, image_url, max_tokens)

    def _call_vlm(self, prompt: str, image_url: str,
                   max_tokens: int = 1000,
                   temperature: float = 0.7) -> dict:
        if not self.vlm:
            return {"content": "", "success": False,
                    "error": "VLM client not initialized"}
        try:
            resp = self.vlm.chat.completions.create(
                model=self.vlm_model,
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
            return {"content": content, "model": self.vlm_model,
                    "success": True, "error": ""}
        except Exception as e:
            return {"content": "", "model": self.vlm_model,
                    "success": False, "error": str(e)}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("query", help="查询内容")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--no-rewrite", action="store_true")
    parser.add_argument("--no-summarize", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    sp = SmartProcessor()
    result = sp.process(
        args.query, top_k=args.top_k,
        rewrite=not args.no_rewrite,
        summarize=not args.no_summarize,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"查询: {result['query']}")
        if result['rewritten'] != result['query']:
            print(f"改写: {result['rewritten']}")
        print(f"\n检索结果 ({result['result_count']} 条):")
        for r in result['results']:
            print(f"  [{r.get('weighted_score', r.get('score', 0)):.3f}] "
                  f"({r.get('source','')}) {r.get('content','')[:60]}...")
        if result.get('summary'):
            print(f"\n总结: {result['summary'][:200]}...")
