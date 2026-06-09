#!/usr/bin/env python3
"""
ontology LLM 增强模块
为知识图谱添加 LLM 辅助功能
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Tuple
import re

# 配置路径

# ── Centralized path resolution ──
import os as _os, sys as _sys
_ws_root = _os.environ.get("OPENCLAW_WORKSPACE", _os.path.expanduser("~/.openclaw/workspace"))
for _p in [_ws_root, "/workspace"]:
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
import path_resolver
ONTOLOGY_DIR = path_resolver.SKILLS_DIR / "ontology"
ONTOLOGY_DB = path_resolver.OPENCLAW_HOME / "ontology" / "ontology.db"
LLM_CONFIG = path_resolver.LLM_CONFIG_JSON

class OntologyLLMEnhancer:
    """ontology LLM 增强器"""
    
    # 实体类型定义
    ENTITY_TYPES = {
        "Person": ["人", "先生", "女士", "老师", "工程师", "经理"],
        "Organization": ["公司", "团队", "组织", "部门", "集团"],
        "Project": ["项目", "计划", "任务", "工程"],
        "Event": ["会议", "活动", "聚会", "培训"],
        "Location": ["地点", "城市", "地址", "位置"],
        "Document": ["文档", "文件", "报告", "方案"],
        "Task": ["任务", "待办", "工作"],
        "Goal": ["目标", "指标", "KPI"]
    }
    
    # 关系类型定义
    RELATION_TYPES = {
        "works_for": ["工作于", "就职于", "任职于"],
        "manages": ["管理", "负责", "领导"],
        "participates_in": ["参与", "参加", "加入"],
        "depends_on": ["依赖", "需要", "基于"],
        "related_to": ["相关", "关联", "涉及"],
        "located_at": ["位于", "在", "地址"],
        "created_by": ["创建于", "由...创建"],
        "assigned_to": ["分配给", "指派给"]
    }
    
    def __init__(self):
        self.ontology_dir = ONTOLOGY_DIR
        self.llm_config = self._load_llm_config()
    
    def _load_llm_config(self) -> Dict:
        """加载 LLM 配置"""
        if LLM_CONFIG.exists():
            return json.loads(LLM_CONFIG.read_text())
        return {}
    
    def extract_entities_with_llm(self, text: str) -> List[Dict]:
        """使用 LLM 提取实体"""
        entities = []
        
        # 基于规则的实体提取（可替换为 LLM 调用）
        for entity_type, keywords in self.ENTITY_TYPES.items():
            for keyword in keywords:
                # 简单匹配
                pattern = rf'([^\s，。！？]+){keyword}'
                matches = re.findall(pattern, text)
                for match in matches:
                    entities.append({
                        "name": match + keyword,
                        "type": entity_type,
                        "confidence": 0.8
                    })
        
        return entities
    
    def extract_relations_with_llm(self, text: str, entities: List[Dict]) -> List[Dict]:
        """使用 LLM 提取关系"""
        relations = []
        
        # 基于规则的关系提取（可替换为 LLM 调用）
        for relation_type, keywords in self.RELATION_TYPES.items():
            for keyword in keywords:
                if keyword in text:
                    # 查找相关实体
                    for i, entity1 in enumerate(entities):
                        for entity2 in entities[i+1:]:
                            if keyword in text:
                                relations.append({
                                    "from": entity1["name"],
                                    "relation": relation_type,
                                    "to": entity2["name"],
                                    "confidence": 0.7
                                })
        
        return relations
    
    def infer_entity_type(self, entity_name: str, context: str = "") -> str:
        """推断实体类型"""
        # 基于名称和上下文推断类型
        for entity_type, keywords in self.ENTITY_TYPES.items():
            for keyword in keywords:
                if keyword in entity_name or keyword in context:
                    return entity_type
        return "Note"
    
    def suggest_relations(self, entity: Dict, all_entities: List[Dict]) -> List[Dict]:
        """建议可能的关系"""
        suggestions = []
        
        for other in all_entities:
            if other["id"] != entity.get("id"):
                # 基于类型推断可能的关系
                if entity.get("type") == "Person" and other.get("type") == "Organization":
                    suggestions.append({
                        "from": entity["name"],
                        "relation": "works_for",
                        "to": other["name"],
                        "confidence": 0.6
                    })
                elif entity.get("type") == "Person" and other.get("type") == "Project":
                    suggestions.append({
                        "from": entity["name"],
                        "relation": "participates_in",
                        "to": other["name"],
                        "confidence": 0.6
                    })
        
        return suggestions
    
    def validate_entity(self, entity: Dict) -> Tuple[bool, List[str]]:
        """验证实体"""
        errors = []
        
        # 检查必需字段
        if not entity.get("name"):
            errors.append("实体名称不能为空")
        
        if not entity.get("type"):
            errors.append("实体类型不能为空")
        
        # 检查类型是否有效
        if entity.get("type") and entity["type"] not in self.ENTITY_TYPES:
            errors.append(f"无效的实体类型: {entity['type']}")
        
        return len(errors) == 0, errors

def main():
    print("=" * 60)
    print("ontology LLM 增强模块")
    print("=" * 60)
    
    enhancer = OntologyLLMEnhancer()
    
    # 测试增强功能
    test_text = "张三是ABC公司的工程师，他参与了新项目开发"
    
    print("\n测试文本:", test_text)
    
    entities = enhancer.extract_entities_with_llm(test_text)
    print("\n提取实体:", entities)
    
    relations = enhancer.extract_relations_with_llm(test_text, entities)
    print("\n提取关系:", relations)
    
    print("\n" + "=" * 60)
    print("✅ ontology LLM 增强模块已就绪")

if __name__ == "__main__":
    main()
