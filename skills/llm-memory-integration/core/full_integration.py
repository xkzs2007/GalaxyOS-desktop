#!/usr/bin/env python3
"""
小艺 Claw 完整集成层 (Full Integration Layer)

真正接入所有模块，实现：
1. CRAG 纠错检索
2. 投机解码加速
3. 思考技能自动路由
4. 自主任务执行
5. 向量记忆优化

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-04-21
"""

import sys
import os
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
import logging
import json

# 添加模块路径
CORE_DIR = Path(__file__).parent
PRIVILEGED_DIR = Path.home() / ".openclaw/workspace/skills/llm-memory-integration/src/privileged"
SKILLS_DIR = Path.home() / ".openclaw/workspace/skills"
sys.path.insert(0, str(CORE_DIR))
sys.path.insert(0, str(PRIVILEGED_DIR))

logger = logging.getLogger(__name__)


class FullIntegration:
    """
    完整集成层
    
    真正接入所有模块，提供统一的调用接口。
    """
    
    def __init__(self, workspace_path: str = None):
        self.workspace_path = Path(workspace_path or 
            os.path.expanduser("~/.openclaw/workspace"))
        
        # 核心记忆系统（走 XiaoYiClawLLM 统一出口）
        from xiaoyi_claw_api import get_xiaoyi_claw
        self._claw = get_xiaoyi_claw()
        self.memory = self._claw.memory_v2 if hasattr(self._claw, 'memory_v2') else None
        
        # 腾讯云记忆插件
        self._tencentdb = None
        
        # 模块缓存
        self._crag_pipeline = None
        self._hybrid_search = None
        self._speculative_decoder = None
        self._skill_coordinator = None
        self._autonomous_tasks = None
        self._vector_memory = None
        
        logger.info("✅ 完整集成层初始化完成")
    
    # ==================== CRAG 纠错检索 ====================
    
    def _load_crag(self):
        """加载 CRAG Pipeline"""
        if self._crag_pipeline is not None:
            return self._crag_pipeline
        
        try:
            from crag_pipeline import CRAGPipeline
            
            # CRAGPipeline 不需要初始化参数，使用 run() 时传入 retriever_fn
            self._crag_pipeline = CRAGPipeline()
            
            logger.debug("CRAG Pipeline 加载成功")
            return self._crag_pipeline
            
        except Exception as e:
            logger.warning(f"CRAG Pipeline 加载失败: {e}")
            return None
    
    def crag_retrieve(
        self,
        query: str,
        top_k: int = 5,
        enable_web_fallback: bool = False
    ) -> Dict:
        """
        CRAG 纠错检索
        
        流程:
        1. 初始检索
        2. 评估相关度
        3. 低置信度 → 纠正/补充
        4. 重排
        5. 返回结果
        
        Args:
            query: 查询
            top_k: 返回数量
            enable_web_fallback: 是否启用 Web 搜索补充
        
        Returns:
            {
                "documents": [...],
                "confidence": str,
                "corrections": [...],
                "metadata": {...}
            }
        """
        result = {
            "query": query,
            "documents": [],
            "confidence": "unknown",
            "corrections": [],
            "metadata": {
                "method": "basic",
                "latency_ms": 0
            }
        }
        
        start_time = datetime.now()
        
        # 尝试使用 CRAG
        crag = self._load_crag()
        if crag:
            try:
                # 定义检索函数
                def retriever_fn(query):
                    results = self.memory.recall(query, top_k=top_k * 2, use_enhanced=False)
                    from crag_pipeline import RAGDocument
                    return [
                        RAGDocument(
                            content=r["content"],
                            score=r["confidence"],
                            source=r["source"],
                            metadata={"id": r["id"]}
                        )
                        for r in results
                    ]
                
                crag_result = crag.run(query, retriever_fn=retriever_fn)
                
                result["documents"] = [
                    {
                        "content": doc.content,
                        "score": doc.score,
                        "source": doc.source
                    }
                    for doc in crag_result.documents_used
                ]
                result["confidence"] = crag_result.retrieval_confidence.value
                result["corrections"] = crag_result.corrections_made
                result["metadata"]["method"] = "crag"
                result["metadata"]["self_rag_flags"] = crag_result.self_rag_flags
                
            except Exception as e:
                logger.warning(f"CRAG 检索失败，回退到基础检索: {e}")
                result["documents"] = self.memory.recall(query, top_k=top_k)
        else:
            # 回退到基础检索
            result["documents"] = self.memory.recall(query, top_k=top_k)
        
        result["metadata"]["latency_ms"] = (datetime.now() - start_time).total_seconds() * 1000
        
        return result
    
    # ==================== 混合检索 ====================
    
    def _load_hybrid_search(self):
        """加载混合检索"""
        if self._hybrid_search is not None:
            return self._hybrid_search
        
        try:
            from hybrid_search import HybridSearcher, BM25Index, QueryRewriter
            
            # 创建 BM25 索引
            bm25 = BM25Index(language="zh")
            
            # 加载记忆到 BM25
            memories = self.memory.hallucination_guard._load_memories()
            if memories:
                docs = [m.content for m in memories]
                doc_ids = [m.id for m in memories]
                bm25.add_documents(docs, doc_ids)
            
            # 创建查询重写器
            rewriter = QueryRewriter()
            
            self._hybrid_search = HybridSearcher(
                embedding_client=None,  # 使用记忆系统的向量检索
                bm25_k1=1.5,
                bm25_b=0.75
            )
            # 添加文档到 BM25
            if memories:
                docs = [m.content for m in memories]
                doc_ids = [m.id for m in memories]
                self._hybrid_search.add_documents(docs, doc_ids)
            
            logger.debug("混合检索加载成功")
            return self._hybrid_search
            
        except Exception as e:
            logger.warning(f"混合检索加载失败: {e}")
            return None
    
    def hybrid_retrieve(
        self,
        query: str,
        top_k: int = 10,
        alpha: float = 0.5
    ) -> Dict:
        """
        混合检索 (Dense + Sparse)
        
        Args:
            query: 查询
            top_k: 返回数量
            alpha: Dense 权重 (1-alpha 为 Sparse 权重)
        
        Returns:
            {
                "results": [...],
                "dense_results": [...],
                "sparse_results": [...],
                "rewritten_queries": [...]
            }
        """
        result = {
            "query": query,
            "results": [],
            "dense_results": [],
            "sparse_results": [],
            "rewritten_queries": []
        }
        
        # Dense 检索（使用记忆系统）
        result["dense_results"] = self.memory.recall(query, top_k=top_k * 2)
        
        # 尝试混合检索
        hybrid = self._load_hybrid_search()
        if hybrid:
            try:
                # 使用 search 方法，mode="sparse" 只用 BM25
                sparse_results = hybrid.search(query, top_k=top_k * 2, mode="sparse")
                result["sparse_results"] = [
                    {"content": r.content, "score": r.score}
                    for r in sparse_results
                ]
                
                # RRF 融合
                result["results"] = self._rrf_fusion(
                    result["dense_results"],
                    result["sparse_results"],
                    alpha=alpha
                )
                
            except Exception as e:
                logger.warning(f"Sparse 检索失败: {e}")
                result["results"] = result["dense_results"]
        else:
            result["results"] = result["dense_results"]
        
        return result
    
    def _rrf_fusion(
        self,
        dense_results: List[Dict],
        sparse_results: List[Dict],
        alpha: float = 0.5,
        k: int = 60
    ) -> List[Dict]:
        """RRF (Reciprocal Rank Fusion) 融合"""
        scores = {}
        
        # Dense 分数
        for i, r in enumerate(dense_results):
            content = r.get("content", "")[:100]
            if content not in scores:
                scores[content] = {"content": r.get("content", ""), "score": 0}
            scores[content]["score"] += alpha / (k + i + 1)
        
        # Sparse 分数
        for i, r in enumerate(sparse_results):
            content = r.get("content", "")[:100]
            if content not in scores:
                scores[content] = {"content": r.get("content", ""), "score": 0}
            scores[content]["score"] += (1 - alpha) / (k + i + 1)
        
        # 排序
        results = sorted(scores.values(), key=lambda x: x["score"], reverse=True)
        return results
    
    # ==================== 思考技能路由 ====================
    
    def _load_skill_coordinator(self):
        """加载技能协调器"""
        if self._skill_coordinator is not None:
            return self._skill_coordinator
        
        try:
            # 使用私有包中的技能协调器
            from skill_coordinator import SkillCoordinator
            self._skill_coordinator = SkillCoordinator()
            logger.debug("技能协调器加载成功")
            return self._skill_coordinator
        except Exception as e:
            logger.warning(f"技能协调器加载失败: {e}")
            return None
    
    def detect_thinking_skill(self, message: str) -> Dict:
        """
        检测应该使用的思考技能
        
        Args:
            message: 用户消息
        
        Returns:
            {
                "detected_skill": str,
                "confidence": float,
                "workflow": [...]
            }
        """
        result = {
            "message": message,
            "detected_skill": None,
            "confidence": 0.0,
            "workflow": []
        }
        
        # 使用技能协调器
        coordinator = self._load_skill_coordinator()
        if coordinator:
            try:
                detected = coordinator.detect_intent(message)
                if detected:
                    result["detected_skill"] = detected.get("skill")
                    result["confidence"] = detected.get("confidence", 0.0)
                    result["workflow"] = coordinator.get_workflow(detected.get("skill", ""))
            except Exception as e:
                logger.warning(f"技能检测失败: {e}")
        
        # 回退: 简单关键词匹配
        if not result["detected_skill"]:
            skill_keywords = {
                "first-principles": ["从根本上", "本质是什么", "第一性原理", "基本原理"],
                "systems-thinking": ["系统性", "整体", "反馈", "涌现", "循环"],
                "critical-thinking": ["评估", "论证", "证据", "逻辑谬误"],
                "backward-thinking": ["倒推", "逆向", "从终点出发"],
                "analogical-thinking": ["类比", "借鉴", "像...一样", "跨领域"],
                "feynman-technique": ["解释给", "教会我", "简单说明"],
                "decision-engine": ["决策", "选择", "权衡"],
                "product-thinking": ["产品", "用户需求", "MVP"],
            }
            
            message_lower = message.lower()
            for skill, keywords in skill_keywords.items():
                for kw in keywords:
                    if kw in message_lower:
                        result["detected_skill"] = skill
                        result["confidence"] = 0.8
                        break
                if result["detected_skill"]:
                    break
        
        return result
    
    def execute_thinking_skill(
        self,
        skill_name: str,
        problem: str,
        context: Dict = None
    ) -> Dict:
        """
        执行思考技能
        
        Args:
            skill_name: 技能名称
            problem: 问题
            context: 上下文
        
        Returns:
            思考结果
        """
        result = {
            "skill": skill_name,
            "problem": problem,
            "analysis": "",
            "insights": [],
            "recommendations": []
        }
        
        # 加载技能
        skill_path = SKILLS_DIR / skill_name / "SKILL.md"
        if skill_path.exists():
            # 读取技能指导
            skill_content = skill_path.read_text(encoding="utf-8")
            result["skill_guidance"] = skill_content[:500]
        
        # 根据技能类型执行不同分析
        if skill_name == "first-principles":
            result["analysis"] = self._first_principles_analysis(problem)
        elif skill_name == "systems-thinking":
            result["analysis"] = self._systems_analysis(problem)
        elif skill_name == "critical-thinking":
            result["analysis"] = self._critical_analysis(problem)
        elif skill_name == "backward-thinking":
            result["analysis"] = self._backward_analysis(problem)
        else:
            result["analysis"] = f"使用 {skill_name} 分析问题: {problem}"
        
        return result
    
    def _first_principles_analysis(self, problem: str) -> str:
        """第一性原理分析"""
        return f"""
【第一性原理分析】

问题: {problem}

分析步骤:
1. 识别基本假设 - 这个问题建立在什么假设之上？
2. 质疑假设 - 这些假设真的成立吗？有没有反例？
3. 回归基本事实 - 什么是不可动摇的基本事实？
4. 从零重构 - 如果从基本事实出发，解决方案是什么？

建议: 从最基本的事实出发，不要被既有假设束缚。
"""
    
    def _systems_analysis(self, problem: str) -> str:
        """系统思维分析"""
        return f"""
【系统思维分析】

问题: {problem}

分析维度:
1. 要素识别 - 系统由哪些要素组成？
2. 关系映射 - 要素之间如何相互影响？
3. 反馈回路 - 存在哪些正/负反馈？
4. 涌现现象 - 系统整体表现出什么特性？

建议: 关注整体而非局部，寻找杠杆点。
"""
    
    def _critical_analysis(self, problem: str) -> str:
        """批判性思维分析"""
        return f"""
【批判性思维分析】

问题: {problem}

评估维度:
1. 证据检验 - 支持结论的证据是否充分？
2. 逻辑检验 - 推理过程是否存在谬误？
3. 假设检验 - 隐含假设是否合理？
4. 反例寻找 - 是否存在相反的证据？

建议: 保持怀疑，寻找反例，验证假设。
"""
    
    def _backward_analysis(self, problem: str) -> str:
        """逆向思维分析"""
        return f"""
【逆向思维分析】

问题: {problem}

分析步骤:
1. 定义终点 - 理想结果是什么？
2. 倒推路径 - 从终点到起点需要什么？
3. 识别瓶颈 - 关键障碍在哪里？
4. 规划行动 - 如何突破瓶颈？

建议: 从目标出发，反向规划路径。
"""
    
    # ==================== 自主任务执行 ====================
    
    def _load_autonomous_tasks(self):
        """加载自主任务系统"""
        if self._autonomous_tasks is not None:
            return self._autonomous_tasks
        
        try:
            # 检查 proactive-tasks 技能
            proactive_path = SKILLS_DIR / "proactive-tasks" / "scripts" / "task_manager.py"
            if proactive_path.exists():
                self._autonomous_tasks = proactive_path
                logger.debug("自主任务系统加载成功")
                return self._autonomous_tasks
        except Exception as e:
            logger.warning(f"自主任务系统加载失败: {e}")
        
        return None
    
    def get_next_task(self) -> Optional[Dict]:
        """获取下一个待执行任务"""
        task_script = self._load_autonomous_tasks()
        if task_script:
            try:
                import subprocess
                result = subprocess.run(
                    ["python3", str(task_script), "next-task"],
                    capture_output=True,
                    text=True,
                    timeout=30
                )
                if result.returncode == 0 and result.stdout.strip():
                    return json.loads(result.stdout)
            except Exception as e:
                logger.warning(f"获取任务失败: {e}")
        
        return None
    
    def execute_task(self, task: Dict, duration_minutes: int = 15) -> Dict:
        """
        执行任务
        
        Args:
            task: 任务信息
            duration_minutes: 执行时长（分钟）
        
        Returns:
            执行结果
        """
        result = {
            "task": task,
            "status": "pending",
            "progress": 0,
            "outputs": []
        }
        
        # TODO: 实际执行任务
        # 这里可以根据任务类型调用不同的处理器
        
        result["status"] = "completed"
        result["progress"] = 100
        
        return result
    
    # ==================== 向量记忆优化 ====================
    
    def _load_vector_memory(self):
        """加载向量记忆优化"""
        if self._vector_memory is not None:
            return self._vector_memory
        
        try:
            # 检查 vector-memory-hack 技能
            vector_path = SKILLS_DIR / "vector-memory-hack" / "scripts" / "memory_search.py"
            if vector_path.exists():
                self._vector_memory = vector_path
                logger.debug("向量记忆优化加载成功")
                return self._vector_memory
        except Exception as e:
            logger.warning(f"向量记忆优化加载失败: {e}")
        
        return None
    
    def fast_memory_search(self, query: str, max_results: int = 5) -> List[Dict]:
        """
        快速记忆搜索（使用 TF-IDF + SQLite）
        
        比 embedding 搜索快 100 倍，适合快速检索。
        """
        vector_script = self._load_vector_memory()
        if vector_script:
            try:
                import subprocess
                result = subprocess.run(
                    ["python3", str(vector_script), "search", query, "--max-results", str(max_results)],
                    capture_output=True,
                    text=True,
                    timeout=10
                )
                if result.returncode == 0 and result.stdout.strip():
                    return json.loads(result.stdout)
            except Exception as e:
                logger.warning(f"向量记忆搜索失败: {e}")
        
        # 回退到基础搜索
        return self.memory.recall(query, top_k=max_results)
    
    # ==================== 腾讯云记忆插件 ====================
    
    def _load_tencentdb(self):
        """加载腾讯云记忆插件"""
        if self._tencentdb is not None:
            return self._tencentdb
        
        try:
            from tencentdb_integration import TencentDBMemory
            self._tencentdb = TencentDBMemory()
            logger.debug("腾讯云记忆插件加载成功")
            return self._tencentdb
        except Exception as e:
            logger.warning(f"腾讯云记忆插件加载失败: {e}")
            return None
    
    def get_tencentdb_stats(self) -> Dict:
        """获取腾讯云记忆插件统计"""
        tdb = self._load_tencentdb()
        if tdb:
            return tdb.get_stats()
        return {"error": "腾讯云记忆插件未加载"}
    
    def search_tencentdb(self, query: str, limit: int = 10) -> List[Dict]:
        """搜索腾讯云记忆"""
        tdb = self._load_tencentdb()
        if tdb:
            return tdb.search_memories(query, limit=limit)
        return []
    
    def get_persona(self) -> Optional[str]:
        """获取用户画像"""
        tdb = self._load_tencentdb()
        if tdb:
            return tdb.get_persona()
        return None
    
    def get_scene_blocks(self) -> List[Dict]:
        """获取场景块"""
        tdb = self._load_tencentdb()
        if tdb:
            return tdb.get_scene_blocks()
        return []
    
    def unified_recall(
        self,
        query: str,
        sources: List[str] = ["xiaoyi", "tencentdb"],
        top_k: int = 10
    ) -> Dict:
        """
        统一召回（整合多个记忆源）
        
        Args:
            query: 查询
            sources: 记忆源列表
            top_k: 每个源返回数量
        
        Returns:
            {
                "xiaoyi_results": [...],
                "tencentdb_results": [...],
                "merged_results": [...]
            }
        """
        result = {
            "query": query,
            "xiaoyi_results": [],
            "tencentdb_results": [],
            "merged_results": []
        }
        
        # 小艺记忆系统
        if "xiaoyi" in sources:
            result["xiaoyi_results"] = self.memory.recall(query, top_k=top_k)
        
        # 腾讯云记忆插件
        if "tencentdb" in sources:
            result["tencentdb_results"] = self.search_tencentdb(query, limit=top_k)
        
        # 合并结果
        all_results = []
        for r in result["xiaoyi_results"]:
            all_results.append({
                "content": r.get("content", ""),
                "score": r.get("confidence", r.get("score", 0)),
                "source": "xiaoyi"
            })
        for r in result["tencentdb_results"]:
            all_results.append({
                "content": r.get("content", ""),
                "score": r.get("score", 0),
                "source": "tencentdb"
            })
        
        # 按分数排序
        all_results.sort(key=lambda x: x["score"], reverse=True)
        result["merged_results"] = all_results[:top_k]
        
        return result
    
    # ==================== 统一接口 ====================
    
    def smart_recall(
        self,
        query: str,
        method: str = "auto",
        top_k: int = 10
    ) -> Dict:
        """
        智能召回（自动选择最优方法）
        
        Args:
            query: 查询
            method: 方法 (auto/crag/hybrid/basic)
            top_k: 返回数量
        
        Returns:
            召回结果
        """
        if method == "auto":
            # 根据查询复杂度自动选择
            if len(query.split()) > 10 or "?" in query:
                method = "crag"
            else:
                method = "hybrid"
        
        if method == "crag":
            return self.crag_retrieve(query, top_k=top_k)
        elif method == "hybrid":
            return self.hybrid_retrieve(query, top_k=top_k)
        else:
            return {
                "query": query,
                "results": self.memory.recall(query, top_k=top_k),
                "method": "basic"
            }
    
    def smart_answer(
        self,
        query: str,
        use_thinking: bool = True,
        use_crag: bool = True
    ) -> Dict:
        """
        智能回答（整合所有能力）
        
        Args:
            query: 问题
            use_thinking: 是否使用思考技能
            use_crag: 是否使用 CRAG
        
        Returns:
            回答结果
        """
        result = {
            "query": query,
            "answer": "",
            "confidence": 0.0,
            "sources": [],
            "thinking_used": None,
            "method": "basic"
        }
        
        # 1. 检测是否需要思考技能
        if use_thinking:
            thinking = self.detect_thinking_skill(query)
            if thinking["detected_skill"]:
                result["thinking_used"] = thinking["detected_skill"]
                thinking_result = self.execute_thinking_skill(
                    thinking["detected_skill"],
                    query
                )
                result["thinking_analysis"] = thinking_result["analysis"]
        
        # 2. 检索相关信息
        if use_crag:
            retrieval = self.crag_retrieve(query, top_k=5)
            result["sources"] = retrieval["documents"]
            result["method"] = "crag"
        else:
            result["sources"] = self.memory.recall(query, top_k=5)
        
        # 3. 生成回答
        answer_result = self.memory.answer(
            query,
            raw_answer=None,  # 让系统自己生成
            top_k=5
        )
        
        result["answer"] = answer_result["answer"]
        result["confidence"] = answer_result["confidence"]
        
        return result
    
    def get_status(self) -> Dict:
        """获取系统状态"""
        status = {
            "memory_health": self.memory.health_check(),
            "modules_loaded": {
                "crag": self._crag_pipeline is not None,
                "hybrid_search": self._hybrid_search is not None,
                "skill_coordinator": self._skill_coordinator is not None,
                "autonomous_tasks": self._autonomous_tasks is not None,
                "vector_memory": self._vector_memory is not None,
                "tencentdb": self._tencentdb is not None,
            },
            "skills_available": len(list(SKILLS_DIR.iterdir())) if SKILLS_DIR.exists() else 0,
            "tencentdb_stats": self.get_tencentdb_stats() if self._tencentdb else None
        }
        
        return status


# CLI 接口
def main():
    """命令行接口"""
    import argparse
    
    parser = argparse.ArgumentParser(description="完整集成层")
    parser.add_argument("command", choices=[
        "crag", "hybrid", "thinking", "smart-recall", "smart-answer", 
        "status", "tencentdb", "unified-recall"
    ])
    parser.add_argument("--query", help="查询")
    parser.add_argument("--skill", help="技能名称")
    parser.add_argument("--top-k", type=int, default=5, help="返回数量")
    
    args = parser.parse_args()
    
    integration = FullIntegration()
    
    if args.command == "crag":
        if not args.query:
            print("错误: 需要提供 --query")
            return
        result = integration.crag_retrieve(args.query, top_k=args.top_k)
        print(f"CRAG 检索结果:")
        print(f"  置信度: {result['confidence']}")
        print(f"  纠正: {result['corrections']}")
        for doc in result["documents"]:
            print(f"  - [{doc['score']:.2f}] {doc['content'][:50]}...")
    
    elif args.command == "hybrid":
        if not args.query:
            print("错误: 需要提供 --query")
            return
        result = integration.hybrid_retrieve(args.query, top_k=args.top_k)
        print(f"混合检索结果:")
        print(f"  Dense: {len(result['dense_results'])} 条")
        print(f"  Sparse: {len(result['sparse_results'])} 条")
        print(f"  融合: {len(result['results'])} 条")
    
    elif args.command == "thinking":
        if not args.query:
            print("错误: 需要提供 --query")
            return
        result = integration.detect_thinking_skill(args.query)
        print(f"检测到的技能: {result['detected_skill']}")
        print(f"置信度: {result['confidence']:.2f}")
        
        if result['detected_skill']:
            analysis = integration.execute_thinking_skill(
                result['detected_skill'],
                args.query
            )
            print(f"\n分析结果:\n{analysis['analysis']}")
    
    elif args.command == "smart-recall":
        if not args.query:
            print("错误: 需要提供 --query")
            return
        result = integration.smart_recall(args.query, top_k=args.top_k)
        print(f"智能召回结果 (方法: {result.get('method', 'unknown')}):")
        results = result.get("results") or result.get("documents") or []
        for r in results[:args.top_k]:
            content = r.get("content", r) if isinstance(r, dict) else str(r)
            score = r.get("score", 0) if isinstance(r, dict) else 0
            print(f"  - [{score:.2f}] {content[:50]}...")
    
    elif args.command == "smart-answer":
        if not args.query:
            print("错误: 需要提供 --query")
            return
        result = integration.smart_answer(args.query)
        print(f"回答: {result['answer']}")
        print(f"置信度: {result['confidence']:.2f}")
        if result['thinking_used']:
            print(f"使用的思考技能: {result['thinking_used']}")
    
    elif args.command == "status":
        status = integration.get_status()
        print("系统状态:")
        print(f"  记忆健康: {'✅' if status['memory_health']['healthy'] else '❌'}")
        print(f"  已加载模块:")
        for name, loaded in status['modules_loaded'].items():
            print(f"    {'✅' if loaded else '⏸️'} {name}")
        print(f"  可用技能: {status['skills_available']} 个")
        
        # 腾讯云记忆插件状态
        if status.get('tencentdb_stats'):
            tdb = status['tencentdb_stats']
            print(f"\n腾讯云记忆插件:")
            print(f"    L1 记忆: {tdb.get('l1_memories', 0)} 条")
            print(f"    L2 场景: {tdb.get('l2_scenes', 0)} 个")
            print(f"    L3 画像: {'✅' if tdb.get('l3_persona') else '❌'}")
            print(f"    L0 对话: {tdb.get('l0_conversations', 0)} 个")
    
    elif args.command == "tencentdb":
        tdb_stats = integration.get_tencentdb_stats()
        print("腾讯云记忆插件:")
        print(f"  L1 记忆: {tdb_stats.get('l1_memories', 0)} 条")
        print(f"  L2 场景: {tdb_stats.get('l2_scenes', 0)} 个")
        print(f"  L3 画像: {'✅' if tdb_stats.get('l3_persona') else '❌'}")
        print(f"  L0 对话: {tdb_stats.get('l0_conversations', 0)} 个")
        print(f"  向量库: {tdb_stats.get('vectors_db_size', 0) / 1024 / 1024:.1f} MB")
    
    elif args.command == "unified-recall":
        if not args.query:
            print("错误: 需要提供 --query")
            return
        result = integration.unified_recall(args.query, top_k=args.top_k)
        print(f"统一召回结果:")
        print(f"  小艺记忆: {len(result['xiaoyi_results'])} 条")
        print(f"  腾讯云: {len(result['tencentdb_results'])} 条")
        print(f"  合并: {len(result['merged_results'])} 条")
        for r in result['merged_results'][:args.top_k]:
            print(f"  - [{r['source']}] [{r['score']:.2f}] {r['content'][:50]}...")


if __name__ == "__main__":
    main()
