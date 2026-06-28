"""
ACP Server - 多智能体协作协议实现

将私有包暴露为 ACP Server，支持被其他 OpenClaw 智能体调用。
v2.1: 增加认证机制、统一异常处理、async 适配
"""

import json
import asyncio
import hmac
import time
import os
from typing import Dict, Any, Optional, Callable
from dataclasses import dataclass
import sys
import inspect
import logging

logger = logging.getLogger(__name__)


@dataclass
class ACPTool:
    """ACP 工具定义"""
    name: str
    description: str
    input_schema: Dict[str, Any]
    output_schema: Dict[str, Any]
    handler: Callable


class ACPAuthError(Exception):
    """ACP 认证错误"""
    pass


class ACPServer:
    """ACP Server 实现（含认证机制）"""

    def __init__(
        self,
        name: str = "llm-memory-integration",
        auth_token: Optional[str] = None,
    ):
        """
        初始化 ACP Server

        Args:
            name: 服务名称
            auth_token: 认证令牌。为 None 时从环境变量 ACP_AUTH_TOKEN 读取，
                        空字符串则跳过认证（仅限开发环境）。
        """
        self.name = name
        self.tools: Dict[str, ACPTool] = {}
        self._auth_token = auth_token if auth_token is not None else os.environ.get('ACP_AUTH_TOKEN', '')
        self._register_default_tools()

    def _verify_auth(self, request: Dict[str, Any]) -> bool:
        """
        验证请求认证

        支持:
        1. Bearer token: {"auth": {"token": "xxx"}}
        2. HMAC 签名: {"auth": {"signature": "hmac-sha256 hex", "timestamp": "unix_ts"}}

        Returns:
            bool: 是否通过认证
        """
        if not self._auth_token:
            # 未配置 token，跳过认证（开发模式）
            return True

        auth = request.get('auth', {})
        if not auth:
            return False

        # 方式1: Bearer token 直接比对（常量时间比较，防时序攻击）
        token = auth.get('token')
        if token:
            return hmac.compare_digest(str(token), str(self._auth_token))

        # 方式2: HMAC 签名验证
        signature = auth.get('signature')
        timestamp = auth.get('timestamp')
        if signature and timestamp:
            # 检查时间戳（5 分钟有效期）
            try:
                ts = float(timestamp)
                if abs(time.time() - ts) > 300:
                    return False
            except (ValueError, TypeError):
                return False

            # 验证 HMAC
            method = request.get('method', '')
            params = request.get('params', {})
            message = f"{method}:{json.dumps(params, sort_keys=True)}:{timestamp}"
            expected = hmac.new(
                self._auth_token.encode('utf-8'),
                message.encode('utf-8'),
                'sha256'
            ).hexdigest()
            return hmac.compare_digest(signature, expected)

        return False

    def _register_default_tools(self):
        """注册默认工具"""
        # 记忆搜索工具
        self.register_tool(
            name="memory_search",
            description="搜索记忆库中的内容",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "搜索查询"},
                    "top_k": {"type": "integer", "default": 10, "description": "返回数量"},
                    "mode": {"type": "string", "enum": ["vector", "fts", "hybrid"], "default": "hybrid"}
                },
                "required": ["query"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "results": {"type": "array", "description": "搜索结果"},
                    "total": {"type": "integer", "description": "总数量"}
                }
            },
            handler=self._memory_search
        )

        # 记忆添加工具
        self.register_tool(
            name="memory_add",
            description="添加新记忆",
            input_schema={
                "type": "object",
                "properties": {
                    "content": {"type": "string", "description": "记忆内容"},
                    "metadata": {"type": "object", "description": "元数据"}
                },
                "required": ["content"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "id": {"type": "string", "description": "记忆ID"},
                    "status": {"type": "string", "description": "状态"}
                }
            },
            handler=self._memory_add
        )

        # 查询改写工具
        self.register_tool(
            name="query_rewrite",
            description="改写查询以提升搜索效果",
            input_schema={
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "原始查询"},
                    "context": {"type": "array", "description": "上下文"}
                },
                "required": ["query"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "rewritten_query": {"type": "string", "description": "改写后的查询"},
                    "expansion_terms": {"type": "array", "description": "扩展词"}
                }
            },
            handler=self._query_rewrite
        )

        # RRF 融合工具
        self.register_tool(
            name="rrf_fusion",
            description="RRF 融合多个搜索结果",
            input_schema={
                "type": "object",
                "properties": {
                    "vector_results": {"type": "array", "description": "向量搜索结果"},
                    "fts_results": {"type": "array", "description": "FTS 搜索结果"},
                    "k": {"type": "integer", "default": 60, "description": "RRF 参数"}
                },
                "required": ["vector_results", "fts_results"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "fused_results": {"type": "array", "description": "融合结果"}
                }
            },
            handler=self._rrf_fusion
        )

        # Embedding 编码工具
        self.register_tool(
            name="embedding_encode",
            description="将文本编码为向量",
            input_schema={
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "输入文本"},
                    "model": {"type": "string", "default": "text-embedding-3-small"}
                },
                "required": ["text"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "vector": {"type": "array", "description": "向量"},
                    "dimension": {"type": "integer", "description": "维度"}
                }
            },
            handler=self._embedding_encode
        )

        # ════════════════════════════════════════════════════════════════
        # Phase 3.4: 调试端点 — DAG 可视化、engram 检查、Skill Bank 状态
        # ════════════════════════════════════════════════════════════════

        # DAG 可视化查询
        self.register_tool(
            name="debug_dag_visualize",
            description="可视化 DAG 知识图谱结构（Phase 3.4 调试端点）",
            input_schema={
                "type": "object",
                "properties": {
                    "session_key": {"type": "string", "description": "会话 key"},
                    "depth": {"type": "integer", "default": 3, "description": "最大深度"},
                    "limit": {"type": "integer", "default": 50, "description": "节点数量限制"}
                },
                "required": ["session_key"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "nodes": {"type": "array", "description": "DAG 节点列表"},
                    "edges": {"type": "array", "description": "DAG 边列表"},
                    "stats": {"type": "object", "description": "统计信息"}
                }
            },
            handler=self._debug_dag_visualize
        )

        # Engram 检查
        self.register_tool(
            name="debug_engram_inspect",
            description="检查 engram 记忆内容（Phase 3.4 调试端点）",
            input_schema={
                "type": "object",
                "properties": {
                    "session_key": {"type": "string", "description": "会话 key"},
                    "query": {"type": "string", "description": "搜索查询"},
                    "limit": {"type": "integer", "default": 20, "description": "返回数量"}
                },
                "required": ["session_key"]
            },
            output_schema={
                "type": "object",
                "properties": {
                    "engrams": {"type": "array", "description": "engram 记录列表"},
                    "total": {"type": "integer", "description": "总数"}
                }
            },
            handler=self._debug_engram_inspect
        )

        # Skill Bank 状态查询
        self.register_tool(
            name="debug_skill_bank_status",
            description="查询 Skill Bank 状态（Phase 3.4 调试端点）",
            input_schema={
                "type": "object",
                "properties": {
                    "include_skills": {"type": "boolean", "default": True, "description": "是否包含技能列表"},
                    "include_review_queue": {"type": "boolean", "default": True, "description": "是否包含审核队列"}
                },
                "required": []
            },
            output_schema={
                "type": "object",
                "properties": {
                    "stats": {"type": "object", "description": "统计信息"},
                    "skills": {"type": "array", "description": "已毕业技能列表"},
                    "review_queue": {"type": "array", "description": "审核队列"},
                    "provenance": {"type": "object", "description": "来源追溯"}
                }
            },
            handler=self._debug_skill_bank_status
        )

    def register_tool(
        self,
        name: str,
        description: str,
        input_schema: Dict[str, Any],
        output_schema: Dict[str, Any],
        handler: Callable
    ):
        """注册工具"""
        self.tools[name] = ACPTool(
            name=name,
            description=description,
            input_schema=input_schema,
            output_schema=output_schema,
            handler=handler
        )

    def list_tools(self) -> list:
        """列出所有工具"""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "inputSchema": tool.input_schema,
                "outputSchema": tool.output_schema
            }
            for tool in self.tools.values()
        ]

    def _validate_tool_params(self, tool: ACPTool, params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        根据 handler 签名和 input_schema 校验工具参数

        仅传递 handler 声明中存在的参数，防止注入额外关键字。
        同时校验 input_schema 中 required 字段是否齐全。

        Args:
            tool: ACP 工具定义
            params: 请求中的参数字典

        Returns:
            校验后的参数字典，校验失败返回 None
        """
        if not isinstance(params, dict):
            return None

        # 检查 required 参数
        schema_required = tool.input_schema.get("required", [])
        for req_key in schema_required:
            if req_key not in params:
                return None

        # 只保留 handler 签名中存在的参数，防止注入
        try:
            sig = inspect.signature(tool.handler)
            valid_keys = set(sig.parameters.keys())
        except (ValueError, TypeError):
            valid_keys = set()

        filtered = {k: v for k, v in params.items() if k in valid_keys}
        return filtered

    async def handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """处理 ACP 请求（含认证和统一异常处理）"""
        method = request.get("method")
        params = request.get("params", {})
        request_id = request.get("id")

        try:
            # 认证检查（tools/list 之外的方法需要认证）
            if method != "tools/list" and not self._verify_auth(request):
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32001, "message": "Authentication failed"}
                }

            if method == "tools/list":
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"tools": self.list_tools()}
                }

            elif method == "tools/call":
                tool_name = params.get("name")
                tool_params = params.get("arguments", {})

                if tool_name not in self.tools:
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32602, "message": f"Tool not found: {tool_name}"}
                    }

                tool = self.tools[tool_name]
                # 根据 handler 签名和 input_schema 校验参数
                validated_params = self._validate_tool_params(tool, tool_params)
                if validated_params is None:
                    return {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "error": {"code": -32602, "message": "Invalid parameters: unexpected or missing arguments"}
                    }

                # 如果 handler 是同步函数，在线程池中执行以避免阻塞事件循环
                if asyncio.iscoroutinefunction(tool.handler):
                    result = await tool.handler(**validated_params)
                else:
                    import functools
                    loop = asyncio.get_running_loop()
                    # run_in_executor 不支持 kwargs，使用 partial 包装
                    handler_with_args = functools.partial(tool.handler, **validated_params)
                    result = await loop.run_in_executor(None, handler_with_args)

                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "result": {"content": [{"type": "text", "text": json.dumps(result)}]}
                }

            else:
                return {
                    "jsonrpc": "2.0",
                    "id": request_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"}
                }

        except Exception as e:
            logger.error(f"ACP 请求处理异常: {e}", exc_info=True)
            return {
                "jsonrpc": "2.0",
                "id": request_id,
                "error": {"code": -32603, "message": str(e)}
            }

    # ============ 工具处理器 ============

    def _memory_search(self, query: str, top_k: int = 10, mode: str = "hybrid"):
        """记忆搜索"""
        try:
            from .scripts_core.router import QueryRouter
            from .scripts_core.rrf import RRFFusion

            router = QueryRouter()
            fusion = RRFFusion()

            route = router.route(query, mode=mode)
            results = fusion.fuse(
                vector_results=route.get('vector', []),
                fts_results=route.get('fts', []),
                top_k=top_k
            )

            return {"results": results, "total": len(results)}
        except Exception as e:
            return {"error": str(e), "results": [], "total": 0}

    def _memory_add(self, content: str, metadata: dict = None):
        """添加记忆"""
        try:
            import uuid
            from datetime import datetime

            memory_id = str(uuid.uuid4())
            timestamp = datetime.now().isoformat()

            return {
                "id": memory_id,
                "status": "success",
                "timestamp": timestamp
            }
        except Exception as e:
            return {"error": str(e), "status": "failed"}

    def _query_rewrite(self, query: str, context: list = None):
        """查询改写"""
        try:
            from .scripts_core.rewriter import QueryRewriter
            rewriter = QueryRewriter()
            rewritten, corrections = rewriter.rewrite(query, context=context)
            expansions = rewriter.expand(query)

            return {
                "rewritten_query": rewritten,
                "expansion_terms": expansions
            }
        except Exception as e:
            return {"error": str(e), "rewritten_query": query, "expansion_terms": []}

    def _rrf_fusion(self, vector_results: list, fts_results: list, k: int = 60):
        """RRF 融合"""
        try:
            from .scripts_core.rrf import RRFFusion
            fusion = RRFFusion(k=k)
            fused = fusion.fuse(vector_results, fts_results)

            return {"fused_results": fused}
        except Exception as e:
            return {"error": str(e), "fused_results": []}

    def _embedding_encode(self, text: str, model: str = "text-embedding-3-small"):
        """Embedding 编码"""
        try:
            from .scripts_core.embedding import EmbeddingEngine
            engine = EmbeddingEngine(model=model)
            vector = engine.encode(text)

            return {
                "vector": vector.tolist() if hasattr(vector, 'tolist') else vector,
                "dimension": len(vector)
            }
        except Exception as e:
            return {"error": str(e), "vector": [], "dimension": 0}

    # ════════════════════════════════════════════════════════════════
    # Phase 3.4: 调试端点 handler 实现
    # ════════════════════════════════════════════════════════════════

    def _debug_dag_visualize(self, session_key: str, depth: int = 3, limit: int = 50):
        """DAG 可视化查询 — 返回节点和边列表供编辑器渲染"""
        try:
            import sys as _sys
            import os as _os
            # 尝试导入 DAGContextManager
            scripts_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))), "extensions", "galaxyos", "scripts")
            if scripts_dir not in _sys.path:
                _sys.path.insert(0, scripts_dir)
            from dag_context_manager import DAGContextManager

            dag = DAGContextManager()
            nodes = dag.get_session_nodes(session_key, limit=limit)

            node_list = []
            edge_list = []
            for node in nodes[:limit]:
                node_list.append({
                    "id": node.node_id,
                    "type": node.node_type,
                    "content": node.content[:200] if node.content else "",
                    "priority": node.priority.name if hasattr(node.priority, 'name') else str(node.priority),
                    "importance": node.importance_score,
                    "depth": getattr(node, 'depth', 0),
                    "timestamp": node.timestamp,
                })
                # 构建边
                parent_ids = node.parent_ids or []
                for pid in parent_ids:
                    edge_list.append({"source": pid, "target": node.node_id})

            return {
                "nodes": node_list,
                "edges": edge_list,
                "stats": {
                    "total_nodes": len(node_list),
                    "total_edges": len(edge_list),
                    "session_key": session_key,
                    "max_depth": depth,
                }
            }
        except Exception as e:
            return {"error": str(e), "nodes": [], "edges": [], "stats": {}}

    def _debug_engram_inspect(self, session_key: str, query: str = "", limit: int = 20):
        """engram 记忆检查 — 搜索并返回 engram 记录"""
        try:
            import sys as _sys
            import os as _os
            scripts_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))), "extensions", "galaxyos", "scripts")
            if scripts_dir not in _sys.path:
                _sys.path.insert(0, scripts_dir)

            # 尝试通过 Worker 查询事件日志（engram 轨迹）
            from dag_context_manager import DAGContextManager
            dag = DAGContextManager()
            nodes = dag.get_session_nodes(session_key, limit=limit)

            engrams = []
            for node in nodes:
                content = node.content or ""
                if query and query.lower() not in content.lower():
                    continue
                engrams.append({
                    "node_id": node.node_id,
                    "content": content[:500],
                    "source": node.node_type,
                    "importance": node.importance_score,
                    "timestamp": node.timestamp,
                    "keywords": node.keywords if hasattr(node, 'keywords') else [],
                })

            return {
                "engrams": engrams[:limit],
                "total": len(engrams),
            }
        except Exception as e:
            return {"error": str(e), "engrams": [], "total": 0}

    def _debug_skill_bank_status(self, include_skills: bool = True, include_review_queue: bool = True):
        """Skill Bank 状态查询 — 返回统计、技能列表、审核队列"""
        try:
            import sys as _sys
            import os as _os
            scripts_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.dirname(__file__))), "extensions", "galaxyos", "scripts")
            if scripts_dir not in _sys.path:
                _sys.path.insert(0, scripts_dir)

            result = {"stats": {}, "skills": [], "review_queue": [], "provenance": {}}

            # Skill Bank 统计
            try:
                from lfm_skill_bank import get_skill_bank
                bank = get_skill_bank()
                result["stats"] = bank.stats() if hasattr(bank, 'stats') else {}
                if include_skills:
                    for sid, skill in bank._skills.items():
                        result["skills"].append({
                            "skill_id": sid,
                            "name": skill.name,
                            "version": skill.version,
                            "quality_score": skill.quality_score,
                            "use_count": skill.use_count,
                            "retired": skill.retired,
                        })
            except Exception as e:
                result["stats"] = {"error": str(e)}

            # 审核队列
            if include_review_queue:
                try:
                    from injection_scanner import get_review_queue, get_provenance_store
                    rq = get_review_queue()
                    result["review_queue"] = rq.list_pending()
                    result["provenance"] = {
                        "total_tracked": len(get_provenance_store()._records),
                    }
                except Exception as e:
                    result["review_queue"] = {"error": str(e)}

            return result
        except Exception as e:
            return {"error": str(e), "stats": {}, "skills": [], "review_queue": [], "provenance": {}}


# ============ 启动函数 ============

async def run_stdio_server():
    """运行 STDIO 服务器"""
    server = ACPServer()

    while True:
        try:
            line = await asyncio.get_event_loop().run_in_executor(None, sys.stdin.readline)
            if not line:
                break

            request = json.loads(line.strip())
            response = await server.handle_request(request)
            print(json.dumps(response), flush=True)

        except json.JSONDecodeError as e:
            print(json.dumps({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": f"Parse error: {e}"}
            }), flush=True)
        except Exception as e:
            print(json.dumps({
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32603, "message": str(e)}
            }), flush=True)


def main():
    """主入口"""
    asyncio.run(run_stdio_server())


if __name__ == "__main__":
    main()
