#!/usr/bin/env python3
"""
技能协调器 (Skill Coordinator)

智能路由和协调多个思考技能：
- 自动识别场景
- 推荐合适的技能
- 组合多个技能
- 执行工作流

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-04-19
"""

import re
import json
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass
from enum import Enum


class SkillCategory(Enum):
    """技能类别"""
    THINKING = "thinking"      # 思考能力
    AUTONOMY = "autonomy"      # 自主性
    PRODUCT = "product"        # 产品能力


@dataclass
class SkillInfo:
    """技能信息"""
    name: str
    category: SkillCategory
    triggers: List[str]        # 触发词
    description: str
    use_cases: List[str]       # 使用场景


# 技能注册表
SKILL_REGISTRY: Dict[str, SkillInfo] = {
    # 思考能力
    "first-principles": SkillInfo(
        name="first-principles",
        category=SkillCategory.THINKING,
        triggers=["从根本上", "本质是什么", "第一性原理", "first principles", "基本原理"],
        description="从基本事实推导，质疑假设，从零重构",
        use_cases=["创新问题", "技术选型", "突破常规思维"]
    ),
    "systems-thinking": SkillInfo(
        name="systems-thinking",
        category=SkillCategory.THINKING,
        triggers=["系统", "整体", "反馈", "涌现", "循环", "系统思维"],
        description="分析要素关系、反馈回路和涌现现象",
        use_cases=["复杂问题", "组织设计", "流程优化"]
    ),
    "critical-thinking": SkillInfo(
        name="critical-thinking",
        category=SkillCategory.THINKING,
        triggers=["评估", "论证", "证据", "逻辑谬误", "批判性思维", "可信吗"],
        description="评估证据、识别逻辑谬误、检验假设",
        use_cases=["决策分析", "论证评估", "信息验证"]
    ),
    "backward-thinking": SkillInfo(
        name="backward-thinking",
        category=SkillCategory.THINKING,
        triggers=["倒推", "逆向", "从终点出发", "逆向思维", "目标导向"],
        description="从目标倒推起点，识别关键路径",
        use_cases=["项目规划", "风险管理", "目标达成"]
    ),
    "analogical-thinking": SkillInfo(
        name="analogical-thinking",
        category=SkillCategory.THINKING,
        triggers=["类比", "借鉴", "像...一样", "跨领域", "相似案例"],
        description="从其他领域的成功模式中借鉴解决方案",
        use_cases=["创新", "学习新领域", "寻找灵感"]
    ),
    "feynman-technique": SkillInfo(
        name="feynman-technique",
        category=SkillCategory.THINKING,
        triggers=["解释给...听", "教会我", "简单说明", "费曼技巧", "理解"],
        description="以教代学，通过简单解释发现知识盲点",
        use_cases=["学习新知识", "检验理解", "教学"]
    ),
    # 自主性
    "multi-agent-collaboration": SkillInfo(
        name="multi-agent-collaboration",
        category=SkillCategory.AUTONOMY,
        triggers=["协作", "分工", "多个agent", "并行处理", "多智能体"],
        description="协调多个专门的AI代理完成复杂任务",
        use_cases=["复杂任务", "多专业协作", "大规模处理"]
    ),
    "decision-engine": SkillInfo(
        name="decision-engine",
        category=SkillCategory.AUTONOMY,
        triggers=["决策", "选择", "权衡", "决策引擎", "做决定"],
        description="结构化决策分析，权衡利弊，量化风险",
        use_cases=["重大决策", "多选项选择", "风险评估"]
    ),
    # 产品能力
    "product-thinking": SkillInfo(
        name="product-thinking",
        category=SkillCategory.PRODUCT,
        triggers=["产品", "用户需求", "MVP", "产品思维", "产品设计"],
        description="从用户需求出发，设计、验证、迭代产品",
        use_cases=["产品设计", "功能规划", "用户研究"]
    ),
}

# 扩展技能推荐表（智能推荐用）
EXTENDED_SKILL_RECOMMENDATIONS = {
    # 工具类技能
    "excalidraw-diagram": {
        "triggers": ["画图", "流程图", "架构图", "思维导图", "时序图", "示意图", "可视化"],
        "description": "生成专业的流程图、架构图、思维导图",
        "suggest_when": "需要可视化展示时"
    },
    "weather": {
        "triggers": ["天气", "气温", "下雨", "晴天", "天气预报", "冷不冷", "热不热"],
        "description": "查询实时天气和预报",
        "suggest_when": "用户关心出行或天气时"
    },
    "deep-search-and-insight-synthesize": {
        "triggers": ["调研", "深入研究", "全面分析", "系统性了解", "深度调研"],
        "description": "多源深度调研，生成专业分析报告",
        "suggest_when": "需要系统性调研某个主题时"
    },
    "best-minds": {
        "triggers": ["怎么看", "如果是", "模拟", "乔布斯", "马斯克", "巴菲特", "名人思维"],
        "description": "模拟名人思维，多角度分析问题",
        "suggest_when": "需要多角度思考时"
    },
    "brainstorming": {
        "triggers": ["头脑风暴", "创意", "想法", "点子", "brainstorm", "发散"],
        "description": "创意发散，生成大量想法",
        "suggest_when": "需要创意发散时"
    },
    "brainhole-factory": {
        "triggers": ["假如", "如果", "what if", "脑洞", "平行宇宙", "假设"],
        "description": "生成脑洞大开的平行宇宙场景",
        "suggest_when": "需要创意发散或娱乐时"
    },
    "fitness-coach": {
        "triggers": ["健身", "运动", "锻炼", "减肥", "增肌", "训练计划"],
        "description": "科学健身指导，制定个性化训练计划",
        "suggest_when": "用户关心健康和运动时"
    },
    "imap-smtp-email": {
        "triggers": ["邮件", "email", "发邮件", "收邮件", "邮箱"],
        "description": "收发邮件，管理邮箱",
        "suggest_when": "需要处理邮件时"
    },
    "huawei-drive": {
        "triggers": ["云盘", "华为云", "上传文件", "下载文件", "云存储"],
        "description": "华为云盘操作，上传下载文件",
        "suggest_when": "需要云盘操作时"
    },
    "markitdown": {
        "triggers": ["转markdown", "文档转换", "pdf转", "word转"],
        "description": "将各种文档转换为 Markdown 格式",
        "suggest_when": "需要文档格式转换时"
    },
    "natural-language-planner": {
        "triggers": ["规划", "计划", "安排", "日程规划", "项目计划"],
        "description": "从自然语言生成执行计划",
        "suggest_when": "需要制定计划时"
    },
    "webapp-testing": {
        "triggers": ["测试", "web测试", "自动化测试", "页面测试"],
        "description": "Web 应用自动化测试",
        "suggest_when": "需要测试 Web 应用时"
    },
}


class SkillCoordinator:
    """技能协调器"""
    
    def __init__(self):
        self.registry = SKILL_REGISTRY
    
    def detect_intent(self, user_message: str) -> List[Tuple[str, float]]:
        """
        检测用户意图，返回匹配的技能列表
        
        Args:
            user_message: 用户消息
        
        Returns:
            [(技能名, 匹配度), ...]
        """
        matches = []
        message_lower = user_message.lower()
        
        for skill_name, skill_info in self.registry.items():
            score = 0.0
            
            # 检查触发词
            for trigger in skill_info.triggers:
                if trigger.lower() in message_lower:
                    score += 0.3
            
            # 检查使用场景
            for use_case in skill_info.use_cases:
                if any(keyword in message_lower for keyword in use_case.split()):
                    score += 0.1
            
            if score > 0:
                matches.append((skill_name, min(score, 1.0)))
        
        # 按匹配度排序
        matches.sort(key=lambda x: x[1], reverse=True)
        
        return matches
    
    def recommend_skill(self, user_message: str) -> Optional[str]:
        """
        推荐最合适的技能
        
        Args:
            user_message: 用户消息
        
        Returns:
            技能名或None
        """
        matches = self.detect_intent(user_message)
        
        if matches and matches[0][1] >= 0.3:
            return matches[0][0]
        
        return None
    
    def get_skill_info(self, skill_name: str) -> Optional[SkillInfo]:
        """获取技能信息"""
        return self.registry.get(skill_name)
    
    def suggest_combination(self, user_message: str) -> List[str]:
        """
        建议技能组合
        
        Args:
            user_message: 用户消息
        
        Returns:
            [技能名, ...]
        """
        matches = self.detect_intent(user_message)
        
        # 返回匹配度 >= 0.2 的技能
        return [name for name, score in matches if score >= 0.2]
    
    def get_workflow(self, scenario: str) -> List[str]:
        """
        获取预定义的工作流
        
        Args:
            scenario: 场景名称
        
        Returns:
            [技能名, ...]
        """
        workflows = {
            "problem_solving": [
                "first-principles",   # 拆解问题
                "systems-thinking",   # 分析系统
                "decision-engine",    # 做决策
            ],
            "learning": [
                "feynman-technique",  # 理解概念
                "analogical-thinking", # 类比学习
            ],
            "innovation": [
                "first-principles",   # 打破假设
                "analogical-thinking", # 跨领域借鉴
                "systems-thinking",   # 系统设计
            ],
            "decision_making": [
                "critical-thinking",  # 评估信息
                "systems-thinking",   # 系统分析
                "decision-engine",    # 结构化决策
            ],
            "product_design": [
                "product-thinking",   # 用户需求
                "systems-thinking",   # 系统设计
                "decision-engine",    # 优先级决策
            ],
        }
        
        return workflows.get(scenario, [])
    
    def list_skills(self, category: Optional[SkillCategory] = None) -> List[SkillInfo]:
        """
        列出技能
        
        Args:
            category: 可选的类别过滤
        
        Returns:
            [SkillInfo, ...]
        """
        skills = list(self.registry.values())
        
        if category:
            skills = [s for s in skills if s.category == category]
        
        return skills
    
    def recommend_extended_skill(self, user_message: str) -> List[Dict]:
        """
        推荐扩展技能（工具类技能）
        
        Args:
            user_message: 用户消息
        
        Returns:
            [{"name": 技能名, "description": 描述, "confidence": 置信度}, ...]
        """
        recommendations = []
        message_lower = user_message.lower()
        
        for skill_name, skill_info in EXTENDED_SKILL_RECOMMENDATIONS.items():
            score = 0.0
            
            # 检查触发词
            for trigger in skill_info["triggers"]:
                if trigger.lower() in message_lower:
                    score += 0.4
            
            if score > 0:
                recommendations.append({
                    "name": skill_name,
                    "description": skill_info["description"],
                    "suggest_when": skill_info["suggest_when"],
                    "confidence": min(score, 1.0)
                })
        
        # 按置信度排序
        recommendations.sort(key=lambda x: x["confidence"], reverse=True)
        
        return recommendations
    
    def get_smart_suggestion(self, user_message: str) -> Optional[str]:
        """
        智能推荐：当检测到用户可能需要某个技能时，返回提示
        
        Args:
            user_message: 用户消息
        
        Returns:
            推荐提示或None
        """
        # 先检查思考技能
        thinking_match = self.recommend_skill(user_message)
        if thinking_match:
            skill_info = self.get_skill_info(thinking_match)
            if skill_info:
                return f"💡 建议使用「{skill_info.description}」来分析这个问题"
        
        # 再检查扩展技能
        extended_matches = self.recommend_extended_skill(user_message)
        if extended_matches:
            top_match = extended_matches[0]
            return f"💡 可以用「{top_match['name']}」：{top_match['description']}"
        
        return None


# 快捷指令映射
SHORTCUTS = {
    "/fp": "first-principles",
    "/sys": "systems-thinking",
    "/crit": "critical-thinking",
    "/back": "backward-thinking",
    "/ana": "analogical-thinking",
    "/feyn": "feynman-technique",
    "/dec": "decision-engine",
    "/prod": "product-thinking",
    # 扩展快捷指令
    "/diagram": "excalidraw-diagram",
    "/weather": "weather",
    "/research": "deep-search-and-insight-synthesize",
    "/minds": "best-minds",
    "/brainstorm": "brainstorming",
    "/fitness": "fitness-coach",
    "/email": "imap-smtp-email",
    "/ana": "analogical-thinking",
    "/feyn": "feynman-technique",
    "/multi": "multi-agent-collaboration",
    "/dec": "decision-engine",
    "/prod": "product-thinking",
}


def parse_shortcut(message: str) -> Optional[str]:
    """
    解析快捷指令
    
    Args:
        message: 用户消息
    
    Returns:
        技能名或None
    """
    message = message.strip()
    
    if message in SHORTCUTS:
        return SHORTCUTS[message]
    
    # 检查消息开头
    for shortcut, skill_name in SHORTCUTS.items():
        if message.startswith(shortcut + " ") or message.startswith(shortcut + "\n"):
            return skill_name
    
    return None


# CLI 接口
def main():
    """命令行接口"""
    import argparse
    
    parser = argparse.ArgumentParser(description="技能协调器")
    parser.add_argument("command", choices=["detect", "recommend", "list", "workflow"])
    parser.add_argument("--message", help="用户消息")
    parser.add_argument("--scenario", help="场景名称")
    parser.add_argument("--category", help="技能类别")
    
    args = parser.parse_args()
    
    coordinator = SkillCoordinator()
    
    if args.command == "detect":
        if not args.message:
            print("错误: 需要提供 --message")
            return
        
        matches = coordinator.detect_intent(args.message)
        print(f"检测到的技能:")
        for name, score in matches:
            print(f"  - {name}: {score:.2f}")
    
    elif args.command == "recommend":
        if not args.message:
            print("错误: 需要提供 --message")
            return
        
        skill = coordinator.recommend_skill(args.message)
        if skill:
            info = coordinator.get_skill_info(skill)
            print(f"推荐技能: {skill}")
            print(f"描述: {info.description}")
            print(f"使用场景: {', '.join(info.use_cases)}")
        else:
            print("未检测到明确的技能需求")
    
    elif args.command == "list":
        category = SkillCategory(args.category) if args.category else None
        skills = coordinator.list_skills(category)
        
        print(f"技能列表 ({len(skills)} 个):")
        for skill in skills:
            print(f"  - {skill.name} ({skill.category.value})")
            print(f"    触发词: {', '.join(skill.triggers)}")
    
    elif args.command == "workflow":
        if not args.scenario:
            print("错误: 需要提供 --scenario")
            return
        
        workflow = coordinator.get_workflow(args.scenario)
        if workflow:
            print(f"工作流: {args.scenario}")
            for i, skill in enumerate(workflow, 1):
                print(f"  {i}. {skill}")
        else:
            print(f"未找到场景: {args.scenario}")


if __name__ == "__main__":
    main()
