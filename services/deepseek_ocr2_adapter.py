#!/usr/bin/env python3
"""
DeepSeek-OCR-2 适配器

基于 DeepSeek-OCR-2 的图像理解模块，核心特性：
- Visual Causal Flow：语义驱动的视觉 token 处理
- 级联一维因果推理：逼近二维图像理解
- 复杂版式优化：文档、图表、复杂布局理解

API 信息：
- Endpoint: https://cloud.infini-ai.com/maas/v1/chat/completions
- Model: deepseek-ocr-2
- 图片格式：仅支持 Base64 编码（适配器自动转换）

支持的图片输入格式：
- HTTP/HTTPS URL：自动下载并转换（带重试机制）
- 本地文件路径：自动读取并转换
- 二进制数据 (bytes)：直接转换
- Base64 Data URI：直接使用

推荐的测试图片源：
- QuickChart（图表）：https://quickchart.io/chart?c={type:"bar",data:{labels:["A","B"],datasets:[{data:[10,20]}]}}
- Picsum（随机照片）：https://picsum.photos/400/200
- 本地文件：/path/to/local/image.png

Author: 小艺 Claw
Version: 1.1.0
Created: 2026-04-25
Updated: 2026-04-29 - 添加重试机制和推荐图片源
"""

import os
import base64
import json
import logging
import requests
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
from dataclasses import dataclass
from enum import Enum
from datetime import datetime
import hashlib

logger = logging.getLogger(__name__)


# ============================================================================
# 配置
# ============================================================================

INFINI_API_URL = "https://cloud.infini-ai.com/maas/v1/chat/completions"
INFINI_API_KEY = os.getenv("INFINI_API_KEY", "YOUR_INFINI_API_KEY")
DEFAULT_MODEL = "deepseek-ocr-2"
DEFAULT_TIMEOUT = 120


class ImageUnderstandingMode(Enum):
    """图像理解模式"""
    GENERAL = "general"           # 通用理解
    OCR = "ocr"                   # 文字识别
    DOCUMENT = "document"         # 文档解析
    CHART = "chart"               # 图表分析
    TABLE = "table"               # 表格识别
    HANDWRITING = "handwriting"   # 手写识别
    COMPLEX_LAYOUT = "complex"    # 复杂版式


# 不同模式的提示词模板
MODE_PROMPTS = {
    ImageUnderstandingMode.GENERAL: "请详细描述这张图片的内容。",
    ImageUnderstandingMode.OCR: "请识别图片中的所有文字，保持原有格式。",
    ImageUnderstandingMode.DOCUMENT: "请解析这份文档的结构和内容，包括标题、段落、列表等。",
    ImageUnderstandingMode.CHART: "请分析这个图表的数据和趋势，描述图表类型、坐标轴、数据点等。",
    ImageUnderstandingMode.TABLE: "请识别这个表格的所有单元格内容，保持行列结构。",
    ImageUnderstandingMode.HANDWRITING: "请识别图片中的手写文字。",
    ImageUnderstandingMode.COMPLEX_LAYOUT: "请详细分析这张图片的版式结构，包括各个区域的内容和关系。",
}


@dataclass
class ImageUnderstandingResult:
    """图像理解结果"""
    success: bool
    content: str
    mode: ImageUnderstandingMode
    model: str
    latency_ms: float
    tokens_used: int
    confidence: float = 0.8
    metadata: Dict = None
    
    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}


# ============================================================================
# DeepSeek-OCR-2 适配器
# ============================================================================

class DeepSeekOCR2Adapter:
    """
    DeepSeek-OCR-2 图像理解适配器
    
    核心能力：
    1. 图像理解（通用）
    2. OCR 文字识别
    3. 文档解析
    4. 图表分析
    5. 表格识别
    6. 复杂版式理解
    
    优势：
    - Visual Causal Flow：语义驱动的 token 处理
    - 因果推理能力：更接近人类视觉
    - 复杂版式优化：文档、图表等
    """
    
    def __init__(
        self,
        api_key: str = INFINI_API_KEY,
        api_url: str = INFINI_API_URL,
        model: str = DEFAULT_MODEL,
        timeout: int = DEFAULT_TIMEOUT
    ):
        self.api_key = api_key
        self.api_url = api_url
        self.model = model
        self.timeout = timeout
        
        # 统计
        self.stats = {
            "total_requests": 0,
            "successful_requests": 0,
            "failed_requests": 0,
            "total_tokens": 0,
            "total_latency_ms": 0,
        }
        
        logger.info(f"DeepSeek-OCR-2 适配器初始化完成，模型: {model}")
    
    def _encode_image(self, image_source: Union[str, bytes, Path]) -> str:
        """
        将图片编码为 Base64
        
        Args:
            image_source: 图片路径、URL 或二进制数据
        
        Returns:
            Base64 编码的图片字符串（带 data URI 前缀）
        """
        if isinstance(image_source, bytes):
            # 直接是二进制数据
            image_data = image_source
        elif isinstance(image_source, (str, Path)):
            path = Path(image_source)
            if path.exists():
                # 本地文件
                with open(path, 'rb') as f:
                    image_data = f.read()
            elif image_source.startswith(('http://', 'https://')):
                # URL - 需要下载
                image_data = self._download_image(str(image_source))
            elif image_source.startswith('data:'):
                # 已经是 Base64 data URI
                return image_source
            else:
                raise ValueError(f"无法识别的图片源: {image_source}")
        else:
            raise TypeError(f"不支持的图片类型: {type(image_source)}")
        
        # 编码为 Base64
        b64_data = base64.b64encode(image_data).decode('utf-8')
        
        # 检测图片类型
        image_type = self._detect_image_type(image_data)
        
        return f"data:image/{image_type};base64,{b64_data}"
    
    def _download_image(self, url: str, max_retries: int = 3) -> bytes:
        """
        下载图片（带重试机制）

        Args:
            url: 图片 URL
            max_retries: 最大重试次数

        Returns:
            图片二进制数据
        """
        import time

        last_error = None

        for attempt in range(max_retries):
            try:
                resp = requests.get(url, timeout=30, verify=True)
                resp.raise_for_status()
                return resp.content
            except requests.exceptions.SSLError as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2  # 递增等待时间
                    logger.warning(f"SSL 错误，{wait_time}秒后重试 ({attempt + 1}/{max_retries}): {url}")
                    time.sleep(wait_time)
                    continue
            except requests.exceptions.Timeout as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2
                    logger.warning(f"请求超时，{wait_time}秒后重试 ({attempt + 1}/{max_retries}): {url}")
                    time.sleep(wait_time)
                    continue
            except requests.exceptions.RequestException as e:
                last_error = e
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1)
                    logger.warning(f"请求失败，{wait_time}秒后重试 ({attempt + 1}/{max_retries}): {url}")
                    time.sleep(wait_time)
                    continue

        # 所有重试都失败
        logger.error(f"下载图片失败（已重试 {max_retries} 次）: {url}, {last_error}")
        raise last_error or Exception(f"下载图片失败: {url}")
    
    def _detect_image_type(self, data: bytes) -> str:
        """检测图片类型"""
        if data[:8] == b'\x89PNG\r\n\x1a\n':
            return 'png'
        elif data[:2] == b'\xff\xd8':
            return 'jpeg'
        elif data[:6] in (b'GIF87a', b'GIF89a'):
            return 'gif'
        elif data[:4] == b'RIFF' and data[8:12] == b'WEBP':
            return 'webp'
        else:
            return 'png'  # 默认
    
    def understand(
        self,
        image_source: Union[str, bytes, Path],
        prompt: str = None,
        mode: ImageUnderstandingMode = ImageUnderstandingMode.GENERAL,
        temperature: float = 0.7,
        max_tokens: int = 2048
    ) -> ImageUnderstandingResult:
        """
        图像理解
        
        Args:
            image_source: 图片源（路径、URL 或二进制）
            prompt: 自定义提示词（覆盖 mode 默认提示词）
            mode: 理解模式
            temperature: 温度参数
            max_tokens: 最大 token 数
        
        Returns:
            ImageUnderstandingResult
        """
        import time
        start_time = time.time()
        
        self.stats["total_requests"] += 1
        
        try:
            # 编码图片
            image_b64 = self._encode_image(image_source)
            
            # 构建提示词
            if prompt is None:
                prompt = MODE_PROMPTS.get(mode, MODE_PROMPTS[ImageUnderstandingMode.GENERAL])
            
            # 构建请求
            payload = {
                "model": self.model,
                "messages": [{
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": image_b64}
                        },
                        {
                            "type": "text",
                            "text": prompt
                        }
                    ]
                }],
                "temperature": temperature,
                "stream": False
            }
            
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}"
            }
            
            # 发送请求
            response = requests.post(
                self.api_url,
                headers=headers,
                json=payload,
                timeout=self.timeout
            )
            
            latency_ms = (time.time() - start_time) * 1000
            
            if response.status_code != 200:
                error_msg = response.text
                logger.error(f"API 调用失败: {response.status_code}, {error_msg}")
                self.stats["failed_requests"] += 1
                return ImageUnderstandingResult(
                    success=False,
                    content=f"API 调用失败: {error_msg}",
                    mode=mode,
                    model=self.model,
                    latency_ms=latency_ms,
                    tokens_used=0
                )
            
            # 解析响应
            result = response.json()
            
            # 提取内容
            content = ""
            if "choices" in result and len(result["choices"]) > 0:
                message = result["choices"][0].get("message", {})
                content = message.get("content", "")
            
            # 提取 token 使用量
            usage = result.get("usage", {})
            tokens_used = usage.get("total_tokens", 0)
            
            # 更新统计
            self.stats["successful_requests"] += 1
            self.stats["total_tokens"] += tokens_used
            self.stats["total_latency_ms"] += latency_ms
            
            return ImageUnderstandingResult(
                success=True,
                content=content,
                mode=mode,
                model=self.model,
                latency_ms=latency_ms,
                tokens_used=tokens_used,
                metadata={
                    "prompt": prompt,
                    "temperature": temperature,
                    "response_id": result.get("id", ""),
                }
            )
            
        except Exception as e:
            latency_ms = (time.time() - start_time) * 1000
            logger.error(f"图像理解失败: {e}")
            self.stats["failed_requests"] += 1
            return ImageUnderstandingResult(
                success=False,
                content=f"处理失败: {str(e)}",
                mode=mode,
                model=self.model,
                latency_ms=latency_ms,
                tokens_used=0
            )
    
    def ocr(
        self,
        image_source: Union[str, bytes, Path],
        preserve_format: bool = True
    ) -> ImageUnderstandingResult:
        """
        OCR 文字识别
        
        Args:
            image_source: 图片源
            preserve_format: 是否保持原有格式
        
        Returns:
            ImageUnderstandingResult
        """
        prompt = "请识别图片中的所有文字"
        if preserve_format:
            prompt += "，保持原有的格式和排版"
        
        return self.understand(
            image_source,
            prompt=prompt,
            mode=ImageUnderstandingMode.OCR
        )
    
    def parse_document(
        self,
        image_source: Union[str, bytes, Path]
    ) -> ImageUnderstandingResult:
        """
        文档解析
        
        Args:
            image_source: 图片源
        
        Returns:
            ImageUnderstandingResult
        """
        return self.understand(
            image_source,
            mode=ImageUnderstandingMode.DOCUMENT
        )
    
    def analyze_chart(
        self,
        image_source: Union[str, bytes, Path]
    ) -> ImageUnderstandingResult:
        """
        图表分析
        
        Args:
            image_source: 图片源
        
        Returns:
            ImageUnderstandingResult
        """
        return self.understand(
            image_source,
            mode=ImageUnderstandingMode.CHART
        )
    
    def recognize_table(
        self,
        image_source: Union[str, bytes, Path]
    ) -> ImageUnderstandingResult:
        """
        表格识别
        
        Args:
            image_source: 图片源
        
        Returns:
            ImageUnderstandingResult
        """
        return self.understand(
            image_source,
            mode=ImageUnderstandingMode.TABLE
        )
    
    def verify_claim(
        self,
        image_source: Union[str, bytes, Path],
        claim: str
    ) -> Dict[str, Any]:
        """
        验证图像相关声明（用于防幻觉）
        
        Args:
            image_source: 图片源
            claim: 待验证的声明
        
        Returns:
            {
                "is_supported": bool,
                "confidence": float,
                "analysis": str,
                "evidence": str
            }
        """
        prompt = f"""请分析这张图片，验证以下声明是否正确：

声明：{claim}

请回答：
1. 该声明是否得到图片内容的支持？
2. 支持或反驳的证据是什么？
3. 你的置信度（0-1）是多少？

请以 JSON 格式回答：
{{"is_supported": true/false, "confidence": 0.X, "evidence": "证据描述"}}"""
        
        result = self.understand(
            image_source,
            prompt=prompt,
            mode=ImageUnderstandingMode.GENERAL
        )
        
        if not result.success:
            return {
                "is_supported": False,
                "confidence": 0.0,
                "analysis": f"验证失败: {result.content}",
                "evidence": ""
            }
        
        # 尝试解析 JSON
        try:
            # 提取 JSON 部分
            content = result.content
            json_start = content.find('{')
            json_end = content.rfind('}') + 1
            if json_start >= 0 and json_end > json_start:
                json_str = content[json_start:json_end]
                parsed = json.loads(json_str)
                return {
                    "is_supported": parsed.get("is_supported", False),
                    "confidence": parsed.get("confidence", 0.5),
                    "analysis": result.content,
                    "evidence": parsed.get("evidence", "")
                }
        except json.JSONDecodeError:
            pass
        
        # 无法解析 JSON，使用简单判断
        is_supported = "支持" in result.content or "正确" in result.content
        return {
            "is_supported": is_supported,
            "confidence": 0.6,
            "analysis": result.content,
            "evidence": result.content
        }
    
    def extract_entities(
        self,
        image_source: Union[str, bytes, Path]
    ) -> List[Dict[str, str]]:
        """
        从图像中提取实体（用于知识图谱）
        
        Args:
            image_source: 图片源
        
        Returns:
            [{"name": "实体名", "type": "类型", "description": "描述"}, ...]
        """
        prompt = """请分析这张图片，提取其中的所有实体。

实体包括：人物、地点、组织、物品、概念等。

请以 JSON 数组格式回答：
[{"name": "实体名", "type": "类型", "description": "描述"}, ...]"""
        
        result = self.understand(
            image_source,
            prompt=prompt,
            mode=ImageUnderstandingMode.GENERAL
        )
        
        if not result.success:
            return []
        
        # 尝试解析 JSON
        try:
            content = result.content
            json_start = content.find('[')
            json_end = content.rfind(']') + 1
            if json_start >= 0 and json_end > json_start:
                json_str = content[json_start:json_end]
                return json.loads(json_str)
        except json.JSONDecodeError:
            pass
        
        return []
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        avg_latency = (
            self.stats["total_latency_ms"] / self.stats["successful_requests"]
            if self.stats["successful_requests"] > 0 else 0
        )
        
        return {
            **self.stats,
            "avg_latency_ms": avg_latency,
            "success_rate": (
                self.stats["successful_requests"] / self.stats["total_requests"]
                if self.stats["total_requests"] > 0 else 0
            )
        }


# ============================================================================
# 便捷函数
# ============================================================================

_adapter = None

def get_adapter() -> DeepSeekOCR2Adapter:
    """获取适配器实例"""
    global _adapter
    if _adapter is None:
        _adapter = DeepSeekOCR2Adapter()
    return _adapter


def understand_image(
    image_source: Union[str, bytes, Path],
    prompt: str = None,
    mode: str = "general"
) -> str:
    """
    图像理解（便捷函数）
    
    Args:
        image_source: 图片源
        prompt: 提示词
        mode: 模式 (general/ocr/document/chart/table)
    
    Returns:
        理解结果文本
    """
    mode_enum = ImageUnderstandingMode(mode)
    result = get_adapter().understand(image_source, prompt, mode_enum)
    return result.content


def ocr_image(image_source: Union[str, bytes, Path]) -> str:
    """OCR 文字识别"""
    result = get_adapter().ocr(image_source)
    return result.content


def parse_document(image_source: Union[str, bytes, Path]) -> str:
    """文档解析"""
    result = get_adapter().parse_document(image_source)
    return result.content


def analyze_chart(image_source: Union[str, bytes, Path]) -> str:
    """图表分析"""
    result = get_adapter().analyze_chart(image_source)
    return result.content


# ============================================================================
# CLI 测试
# ============================================================================

if __name__ == "__main__":
    import sys
    
    def test():
        adapter = DeepSeekOCR2Adapter()
        
        print("=" * 60)
        print("DeepSeek-OCR-2 适配器测试")
        print("=" * 60)
        
        # 测试 1: 创建测试图片
        print("\n[测试 1] 创建测试图片...")
        from PIL import Image, ImageDraw, ImageFont
        import io
        
        img = Image.new('RGB', (400, 200), color='white')
        draw = ImageDraw.Draw(img)
        draw.rectangle([10, 10, 390, 190], outline='black', width=2)
        draw.text((50, 50), "Hello World", fill='black')
        draw.text((50, 100), "测试中文识别", fill='blue')
        
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        image_data = buffer.getvalue()
        
        print(f"  创建测试图片: {len(image_data)} bytes")
        
        # 测试 2: OCR
        print("\n[测试 2] OCR 文字识别...")
        result = adapter.ocr(image_data)
        print(f"  成功: {result.success}")
        print(f"  结果: {result.content[:200]}...")
        print(f"  耗时: {result.latency_ms:.0f}ms")
        print(f"  Tokens: {result.tokens_used}")
        
        # 测试 3: 通用理解
        print("\n[测试 3] 通用图像理解...")
        result = adapter.understand(image_data, mode=ImageUnderstandingMode.GENERAL)
        print(f"  成功: {result.success}")
        print(f"  结果: {result.content[:200]}...")
        
        # 测试 4: 声明验证
        print("\n[测试 4] 声明验证...")
        verify_result = adapter.verify_claim(image_data, "图片中包含英文文字")
        print(f"  支持声明: {verify_result['is_supported']}")
        print(f"  置信度: {verify_result['confidence']}")
        
        # 统计
        print("\n[统计信息]")
        stats = adapter.get_stats()
        for k, v in stats.items():
            print(f"  {k}: {v}")
    
    test()
