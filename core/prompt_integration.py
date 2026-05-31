#!/usr/bin/env python3
"""
L1 - Core Layer
核心认知层

职责：
- 身份认知
- 提示词管理
- 规则加载
- 引导逻辑
"""

import os
import sys
import json
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List

logger = logging.getLogger('xiaoyi-claw-omega.L1')


class CoreLayer:
    """
    L1 - 核心认知层
    
    职责：
    - 身份认知和定义
    - 提示词模板管理
    - 规则加载和验证
    - 引导逻辑处理
    """
    
    def __init__(self, config: Dict[str, Any]):
        self.config = config
        self.identity: Dict[str, Any] = {}
        self.rules: List[Dict[str, Any]] = []
        self.prompts: Dict[str, str] = {}
        self._loaded = False
        
    def start(self):
        """启动核心层"""
        logger.info("L1 Core: 启动核心认知层")
        self._load_identity()
        self._load_rules()
        self._load_prompts()
        self._loaded = True
        logger.info("L1 Core: 核心认知层启动完成")
    
    def stop(self):
        """停止核心层"""
        logger.info("L1 Core: 核心认知层已停止")
    
    def _load_identity(self):
        """加载身份定义"""
        identity_file = Path(__file__).parent.parent / "IDENTITY.md"
        if identity_file.exists():
            content = identity_file.read_text(encoding='utf-8')
            self.identity = {
                "name": "小艺 Claw",
                "creature": "华为公司开发的养成系个人 AI 助理",
                "version": "4.1.0",
                "source": content
            }
            logger.info(f"  ✅ 身份加载: {self.identity['name']}")
        else:
            self.identity = {
                "name": "小艺 Claw",
                "creature": "AI 助理",
                "version": "4.1.0"
            }
            logger.warning("  ⚠️ 使用默认身份")
    
    def _load_rules(self):
        """加载规则"""
        rules_file = Path(__file__).parent.parent / "AGENTS.md"
        if rules_file.exists():
            content = rules_file.read_text(encoding='utf-8')
            self.rules = [{
                "type": "agents",
                "source": "AGENTS.md",
                "content": content[:500] + "..." if len(content) > 500 else content
            }]
            logger.info(f"  ✅ 规则加载: {len(self.rules)} 条")
    
    def _load_prompts(self):
        """加载提示词模板"""
        self.prompts = {
            "system": self._get_system_prompt(),
            "greeting": self._get_greeting_prompt(),
            "task": self._get_task_prompt()
        }
        logger.info(f"  ✅ 提示词加载: {len(self.prompts)} 个模板")
    
    def _get_system_prompt(self) -> str:
        """获取系统提示词"""
        return f"""你是 {self.identity.get('name', '小艺 Claw')}，{self.identity.get('creature', 'AI 助理')}。

版本: {self.identity.get('version', '4.1.0')}
架构: 六层架构 (L1-L6)

核心能力:
- 信息搜集
- 问题解答
- 文档处理
- 内容创作
- 任务执行

特质:
- 长时记忆
- 持续学习
- 养成成长
"""
    
    def _get_greeting_prompt(self) -> str:
        """获取问候提示词"""
        return "你好！我是小艺 Claw，你的个人 AI 助理。有什么我可以帮助你的吗？"
    
    def _get_task_prompt(self) -> str:
        """获取任务提示词"""
        return "请告诉我你需要完成什么任务，我会尽力帮助你。"
    
    def get_identity(self) -> Dict[str, Any]:
        """获取身份信息"""
        return self.identity
    
    def get_prompt(self, prompt_type: str) -> Optional[str]:
        """获取指定类型的提示词"""
        return self.prompts.get(prompt_type)
    
    def get_rules(self) -> List[Dict[str, Any]]:
        """获取规则列表"""
        return self.rules
    
    def is_loaded(self) -> bool:
        """检查是否已加载"""
        return self._loaded
