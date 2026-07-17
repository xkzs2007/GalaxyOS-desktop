#!/usr/bin/env python3
"""
视觉生成模块 - Visual Generation Module
========================================

将 Seedream 图像生成能力集成到系统架构中，
实现记忆可视化、知识图谱可视化、报告增强等功能。

Layer 12: 多模态生成层

作者: 小艺 Claw
日期: 2026-04-21
"""

import os
import sys
import json
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import logging

# 设置日志
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class VisualGenerator:
    """
    视觉生成器

    核心能力：
    1. 记忆可视化 - 将抽象记忆转为图像
    2. 知识图谱可视化 - 实体关系的图形化展示
    3. 报告增强 - 自动生成配图
    4. 概念可视化 - 防幻觉辅助验证
    """

    def __init__(self, skill_path: str = None):
        """
        初始化视觉生成器

        Args:
            skill_path: seedream-image-gen 技能路径
        """
        if skill_path is None:
            skill_path = os.path.expanduser(
                "~/.openclaw/workspace/skills/seedream-image_gen/scripts/generate_seedream.py"
            )

        self.skill_path = skill_path
        self.output_dir = os.path.expanduser(
            "~/.openclaw/workspace/generated-images"
        )

        # 确保输出目录存在
        Path(self.output_dir).mkdir(parents=True, exist_ok=True)

        logger.info("VisualGenerator 初始化完成")
        logger.info(f"  技能路径: {self.skill_path}")
        logger.info(f"  输出目录: {self.output_dir}")

    def generate_image(
        self,
        prompt: str,
        style: str = "anime",
        size: str = "2K",
        max_images: int = 1
    ) -> Dict[str, Any]:
        """
        生成图像的基础方法

        Args:
            prompt: 图像描述
            style: 风格 (anime, realistic, oil_painting, watercolor, etc.)
            size: 尺寸 (1K, 2K, 4K)
            max_images: 最大生成数量

        Returns:
            生成结果，包含图片路径和元数据
        """
        # 构建增强的 prompt
        enhanced_prompt = self._enhance_prompt(prompt, style)

        # 调用 seedream-image-gen
        cmd = [
            "python3",
            self.skill_path,
            "--prompt", enhanced_prompt,
            "--size", size,
            "--max-images", str(max_images)
        ]

        logger.info(f"生成图像: {enhanced_prompt[:50]}...")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=120
            )

            if result.returncode != 0:
                logger.error(f"图像生成失败: {result.stderr}")
                return {
                    "success": False,
                    "error": result.stderr,
                    "images": []
                }

            # 解析输出，提取图片路径
            images = self._parse_output(result.stdout)

            return {
                "success": True,
                "images": images,
                "prompt": enhanced_prompt,
                "style": style,
                "timestamp": datetime.now().isoformat()
            }

        except subprocess.TimeoutExpired:
            logger.error("图像生成超时")
            return {
                "success": False,
                "error": "Timeout",
                "images": []
            }
        except Exception as e:
            logger.error(f"图像生成异常: {e}")
            return {
                "success": False,
                "error": str(e),
                "images": []
            }

    def visualize_memory(
        self,
        memory_content: str,
        memory_type: str = "episodic"
    ) -> Dict[str, Any]:
        """
        记忆可视化

        将抽象的记忆内容转换为视觉图像

        Args:
            memory_content: 记忆内容
            memory_type: 记忆类型 (episodic, semantic, procedural)

        Returns:
            可视化结果
        """
        # 根据记忆类型选择风格
        style_map = {
            "episodic": "anime",      # 事件记忆 → 动漫风格
            "semantic": "realistic",  # 语义记忆 → 写实风格
            "procedural": "diagram"   # 程序记忆 → 图解风格
        }

        style = style_map.get(memory_type, "anime")

        # 构建可视化 prompt
        prompt = f"将以下记忆内容可视化为图像：{memory_content}"

        logger.info(f"可视化记忆 ({memory_type}): {memory_content[:50]}...")

        return self.generate_image(prompt, style=style)

    def visualize_knowledge_graph(
        self,
        entities: List[Dict[str, Any]],
        relations: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        知识图谱可视化

        将实体和关系转换为图形化展示

        Args:
            entities: 实体列表 [{"name": "xxx", "type": "person"}, ...]
            relations: 关系列表 [{"from": "A", "to": "B", "type": "knows"}, ...]

        Returns:
            可视化结果
        """
        # 构建图谱描述
        entity_desc = ", ".join([f"{e['name']}({e.get('type', 'entity')})" for e in entities[:5]])
        relation_desc = ", ".join([f"{r['from']}-{r['type']}->{r['to']}" for r in relations[:5]])

        prompt = f"知识图谱可视化：实体包括{entity_desc}，关系包括{relation_desc}，使用清晰的节点和连线表示，现代简约风格"

        logger.info(f"可视化知识图谱: {len(entities)} 实体, {len(relations)} 关系")

        return self.generate_image(prompt, style="diagram")

    def enhance_report(
        self,
        report_title: str,
        report_summary: str,
        style: str = "professional"
    ) -> Dict[str, Any]:
        """
        报告增强

        为报告自动生成配图

        Args:
            report_title: 报告标题
            report_summary: 报告摘要
            style: 风格 (professional, creative, minimal)

        Returns:
            配图结果
        """
        # 根据报告内容生成配图
        prompt = f"为报告《{report_title}》生成配图，内容概要：{report_summary}，{style}风格，适合商务场景"

        logger.info(f"增强报告: {report_title}")

        return self.generate_image(prompt, style="realistic")

    def visualize_concept(
        self,
        concept: str,
        context: str = ""
    ) -> Dict[str, Any]:
        """
        概念可视化（防幻觉辅助）

        将抽象概念转为图像，用于多源验证

        Args:
            concept: 概念名称
            context: 上下文信息

        Returns:
            可视化结果
        """
        prompt = f"概念可视化：{concept}"
        if context:
            prompt += f"，上下文：{context}"
        prompt += "，清晰准确地表达概念本质"

        logger.info(f"可视化概念: {concept}")

        return self.generate_image(prompt, style="realistic")

    def generate_avatar(
        self,
        description: str,
        style: str = "anime"
    ) -> Dict[str, Any]:
        """
        生成个性化头像

        Args:
            description: 头像描述
            style: 风格

        Returns:
            头像生成结果
        """
        prompt = f"头像设计：{description}，{style}风格，适合作为个人形象"

        logger.info(f"生成头像: {description[:50]}...")

        return self.generate_image(prompt, style=style)

    def _enhance_prompt(self, prompt: str, style: str) -> str:
        """
        增强 prompt，添加风格和质量描述

        Args:
            prompt: 原始 prompt
            style: 风格

        Returns:
            增强后的 prompt
        """
        style_keywords = {
            "anime": "日系动漫风格，线条流畅，色彩明亮",
            "realistic": "写实风格，细节丰富，光影真实",
            "oil_painting": "油画风格，笔触厚重，色彩浓郁",
            "watercolor": "水彩风格，淡雅清新，晕染效果",
            "diagram": "图解风格，简洁清晰，信息可视化",
            "professional": "专业商务风格，简洁大气",
            "creative": "创意艺术风格，独特新颖"
        }

        style_desc = style_keywords.get(style, "")

        if style_desc:
            return f"{prompt}，{style_desc}"

        return prompt

    def _parse_output(self, output: str) -> List[str]:
        """
        解析生成输出，提取图片路径

        Args:
            output: 命令输出

        Returns:
            图片路径列表
        """
        images = []

        for line in output.split("\n"):
            if "Saved to:" in line or "saved to:" in line.lower():
                # 提取路径
                parts = line.split(":")
                if len(parts) >= 2:
                    path = parts[-1].strip()
                    if os.path.exists(path):
                        images.append(path)

        # 如果没找到，尝试从输出目录获取最新图片
        if not images:
            try:
                files = sorted(
                    Path(self.output_dir).glob("*.jpg"),
                    key=lambda x: x.stat().st_mtime,
                    reverse=True
                )
                if files:
                    images.append(str(files[0]))
            except Exception as e:
                logger.warning(f"获取最新图片失败: {e}")

        return images


class VisualGenerationWorkflow:
    """
    视觉生成工作流

    集成到统一协调器中，与其他模块协同工作
    """

    def __init__(self):
        self.generator = VisualGenerator()

    def execute(self, task: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行视觉生成任务

        Args:
            task: 任务描述
                - type: 任务类型 (memory_viz, kg_viz, report_enhance, concept_viz, avatar)
                - data: 任务数据

        Returns:
            执行结果
        """
        task_type = task.get("type", "general")
        data = task.get("data", {})

        handlers = {
            "memory_viz": self._handle_memory_visualization,
            "kg_viz": self._handle_kg_visualization,
            "report_enhance": self._handle_report_enhancement,
            "concept_viz": self._handle_concept_visualization,
            "avatar": self._handle_avatar_generation,
            "general": self._handle_general_generation
        }

        handler = handlers.get(task_type, self._handle_general_generation)

        return handler(data)

    def _handle_memory_visualization(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """处理记忆可视化"""
        return self.generator.visualize_memory(
            memory_content=data.get("content", ""),
            memory_type=data.get("memory_type", "episodic")
        )

    def _handle_kg_visualization(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """处理知识图谱可视化"""
        return self.generator.visualize_knowledge_graph(
            entities=data.get("entities", []),
            relations=data.get("relations", [])
        )

    def _handle_report_enhancement(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """处理报告增强"""
        return self.generator.enhance_report(
            report_title=data.get("title", ""),
            report_summary=data.get("summary", ""),
            style=data.get("style", "professional")
        )

    def _handle_concept_visualization(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """处理概念可视化"""
        return self.generator.visualize_concept(
            concept=data.get("concept", ""),
            context=data.get("context", "")
        )

    def _handle_avatar_generation(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """处理头像生成"""
        return self.generator.generate_avatar(
            description=data.get("description", ""),
            style=data.get("style", "anime")
        )

    def _handle_general_generation(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """处理通用图像生成"""
        return self.generator.generate_image(
            prompt=data.get("prompt", ""),
            style=data.get("style", "anime"),
            size=data.get("size", "2K"),
            max_images=data.get("max_images", 1)
        )


# 便捷函数
def visualize_memory(content: str, memory_type: str = "episodic") -> Dict[str, Any]:
    """便捷函数：可视化记忆"""
    generator = VisualGenerator()
    return generator.visualize_memory(content, memory_type)


def visualize_concept(concept: str, context: str = "") -> Dict[str, Any]:
    """便捷函数：可视化概念"""
    generator = VisualGenerator()
    return generator.visualize_concept(concept, context)


def generate_image(prompt: str, style: str = "anime") -> Dict[str, Any]:
    """便捷函数：生成图像"""
    generator = VisualGenerator()
    return generator.generate_image(prompt, style)


# CLI 入口
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="视觉生成模块")
    parser.add_argument("--type", choices=[
        "memory", "kg", "report", "concept", "avatar", "general"
    ], default="general", help="任务类型")
    parser.add_argument("--prompt", required=True, help="提示词或内容")
    parser.add_argument("--style", default="anime", help="风格")
    parser.add_argument("--output", help="输出路径")

    args = parser.parse_args()

    generator = VisualGenerator()

    if args.type == "memory":
        result = generator.visualize_memory(args.prompt)
    elif args.type == "concept":
        result = generator.visualize_concept(args.prompt)
    else:
        result = generator.generate_image(args.prompt, args.style)

    print(json.dumps(result, ensure_ascii=False, indent=2))
