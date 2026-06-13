"""
R-CCAM 五阶段状态对象

从 xiaoyi_claw_api.py 提取，保持独立以减少 God Object 体积。
"""
from typing import Dict, List, Any, Optional
from datetime import datetime
import time
import uuid


class PhaseState:
    """R-CCAM 五阶段的状态传递对象"""

    def __init__(self, user_input: str):
        # 原始输入
        self.user_input = user_input
        self._start_time = time.time()
        self.session_key = f"rccam_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

        # 图片相关(Visual RAG)
        self.has_image: bool = False
        self.image_source: Optional[str] = None

        # Retrieval 阶段输出
        self.retrieved_memories: List[Dict] = []
        self.dag_summaries: List[Dict] = []
        self.kg_entities: List[Dict] = []
        self.web_results: List[Dict] = []
        self.retrieval_confidence: float = 0.0
        self.needs_more_info: bool = False
        self.paper_engine_results: List[Dict] = []  # RAPTOR/GraphRAG/Reflection
        self.suggested_tool: Optional[Dict] = None  # Toolformer 预判工具

        # Cognition 阶段输出
        self.knowledge_type: str = "info"
        self.type_confidence: float = 0.5
        self.analysis: Dict[str, Any] = {}
        self.intent: str = "query"
        self.thinking_skills_used: List[str] = []

        # Control 阶段输出
        self.strategy: str = "answer"
        self.boundaries: List[str] = []
        self.fallback: str = "polite_refuse"
        self.reasoning: str = ""
        self.control_decision: Dict[str, Any] = {}

        # Action 阶段输出
        self.action_result: Any = None
        self.action_success: bool = False
        self.action_error: Optional[str] = None
        self.generated_answer: str = ""
        self.answer_confidence: float = 0.0

        # RCI 异步批评结果
        self.consistency_action: str = ""
        self.critic_scores: Dict[str, float] = {}

        # Memory 阶段输出
        self.memory_ids: List[str] = []
        self.dag_nodes_created: int = 0
        self.synapse_updated: bool = False
        self.emotion_marked: bool = False
        self.evolution_triggered: bool = False

        # 循环控制
        self.cycle_count: int = 0
        self.max_cycles: int = 3
        self.should_stop: bool = False
        self.stop_reason: str = ""
