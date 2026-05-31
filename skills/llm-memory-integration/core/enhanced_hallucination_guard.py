#!/usr/bin/env python3
"""
增强版防幻觉系统 (Enhanced Hallucination Guard)

整合：
1. 多源交叉验证 - 从多个来源验证信息
2. 思考能力增强 - 使用思考技能分析不确定性
3. 渐进式验证 - 根据不确定性级别选择验证策略

Author: 小艺 Claw
Version: 2.0.0
Created: 2026-04-21
"""

import os
import sys
import json
import re
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime
from dataclasses import dataclass, field
from enum import Enum

# 添加模块路径
CORE_DIR = Path(__file__).parent
sys.path.insert(0, str(CORE_DIR))

logger = logging.getLogger(__name__)


class VerificationLevel(Enum):
    """验证级别"""
    NONE = "none"               # 无需验证（高置信度）
    LIGHT = "light"             # 轻度验证（中高置信度）
    MODERATE = "moderate"       # 中度验证（中等置信度）
    DEEP = "deep"               # 深度验证（低置信度）
    EXHAUSTIVE = "exhaustive"   # 穷尽验证（极低置信度）


class SourceType(Enum):
    """信息来源类型"""
    INTERNAL_MEMORY = "internal_memory"     # 内部记忆
    USER_STATEMENT = "user_statement"       # 用户陈述
    WEB_SEARCH = "web_search"               # 网络搜索
    DOCUMENT = "document"                   # 文档
    KNOWLEDGE_GRAPH = "knowledge_graph"     # 知识图谱
    IMAGE_ANALYSIS = "image_analysis"       # 图像分析（OCR2）
    INFERENCE = "inference"                 # 推断
    UNKNOWN = "unknown"                     # 未知


@dataclass
class VerificationSource:
    """验证来源"""
    source_type: SourceType
    content: str
    confidence: float
    timestamp: str = ""
    metadata: Dict = field(default_factory=dict)


@dataclass
class CrossValidationResult:
    """交叉验证结果"""
    statement: str
    is_verified: bool
    confidence: float
    sources: List[VerificationSource]
    agreements: int
    disagreements: int
    consensus: str  # "strong_agreement" | "weak_agreement" | "disagreement" | "insufficient_data"
    analysis: str
    thinking_process: List[str] = field(default_factory=list)


class MultiSourceCrossValidator:
    """
    多源交叉验证器
    
    从多个来源验证信息的一致性。
    """
    
    # 来源可信度权重
    SOURCE_WEIGHTS = {
        SourceType.USER_STATEMENT: 0.95,
        SourceType.INTERNAL_MEMORY: 0.80,
        SourceType.KNOWLEDGE_GRAPH: 0.85,
        SourceType.DOCUMENT: 0.75,
        SourceType.IMAGE_ANALYSIS: 0.85,  # OCR2 图像分析可信度高
        SourceType.WEB_SEARCH: 0.70,
        SourceType.INFERENCE: 0.50,
        SourceType.UNKNOWN: 0.30,
    }
    
    def __init__(self, workspace_path: str = None):
        self.workspace_path = Path(workspace_path or 
            os.path.expanduser("~/.openclaw/workspace"))
        
        # 加载记忆系统
        self._memories: List[Dict] = []
        self._load_memories()
    
    def _load_memories(self):
        """加载内部记忆"""
        memory_file = self.workspace_path / ".learnings" / "verified_memories.jsonl"
        if memory_file.exists():
            with open(memory_file, "r", encoding="utf-8") as f:
                for line in f:
                    if line.strip():
                        self._memories.append(json.loads(line))
    
    def search_internal_memory(self, query: str, top_k: int = 5) -> List[VerificationSource]:
        """从内部记忆搜索（支持中文 jieba 分词）"""
        results = []
        try:
            import jieba
            query_words = set(jieba.cut_for_search(query.lower()))
        except ImportError:
            query_words = set(query.lower().split())
        import re
        num_pattern = re.findall(r'\d+\s*[a-zA-Z\u4e00-\u9fff]+', query.lower())
        query_words.update(num_pattern)
        
        for mem in self._memories:
            content = mem.get("content", "")
            try:
                content_words = set(jieba.cut_for_search(content.lower()))
            except ImportError:
                content_words = set(content.lower().split())
            content_words.update(re.findall(r'\d+\s*[a-zA-Z\u4e00-\u9fff]+', content.lower()))
            overlap = len(query_words & content_words)
            
            if overlap > 0:
                confidence = mem.get("confidence", 0.5)
                results.append(VerificationSource(
                    source_type=SourceType.INTERNAL_MEMORY,
                    content=content,
                    confidence=confidence,
                    timestamp=mem.get("created_at", ""),
                    metadata={"id": mem.get("id")}
                ))
        
        results.sort(key=lambda x: x.confidence, reverse=True)
        return results[:top_k]
    
    def search_web(self, query: str) -> List[VerificationSource]:
        """
        多搜索引擎聚合搜索（通过代理跨墙）
        
        按优先级依次尝试多个搜索引擎，谁先返回干净结果用谁。
        国内引擎直连，国际引擎走代理（mihomo mihomo:7890）。
        
        引擎（16个）: DuckDuckGo / Google / Google HK / Bing CN+INT / 
                      Baidu / 360 / Sogou / Brave / Ecosia / Qwant /
                      Startpage / Yahoo / WolframAlpha
        """
        import urllib.request, urllib.parse
        
        proxy = 'http://127.0.0.1:7890'
        proxy_handler = urllib.request.ProxyHandler({'http': proxy, 'https': proxy})
        
        engines = [
            # 国际引擎（走代理，质量优先）
            ("DuckDuckGo", f"https://duckduckgo.com/html/?q={urllib.parse.quote(query)}", proxy_handler, 0.70),
            ("Google",     f"https://www.google.com/search?q={urllib.parse.quote(query)}&hl=zh-CN", proxy_handler, 0.68),
            ("Bing INT",   f"https://cn.bing.com/search?q={urllib.parse.quote(query)}&ensearch=1", proxy_handler, 0.65),
            ("Brave",      f"https://search.brave.com/search?q={urllib.parse.quote(query)}", proxy_handler, 0.65),
            # 国内引擎（直连，中文结果好）
            ("Baidu",      f"https://www.baidu.com/s?wd={urllib.parse.quote(query)}", None, 0.60),
            ("Bing CN",    f"https://cn.bing.com/search?q={urllib.parse.quote(query)}&ensearch=0", None, 0.60),
            ("360",        f"https://www.so.com/s?q={urllib.parse.quote(query)}", None, 0.55),
            ("Sogou",      f"https://sogou.com/web?query={urllib.parse.quote(query)}", None, 0.55),
            # 国际备选（走代理）
            ("Google HK",  f"https://www.google.com.hk/search?q={urllib.parse.quote(query)}&hl=zh-CN", proxy_handler, 0.63),
            ("Yahoo",      f"https://search.yahoo.com/search?p={urllib.parse.quote(query)}", proxy_handler, 0.60),
            ("Ecosia",     f"https://www.ecosia.org/search?q={urllib.parse.quote(query)}", proxy_handler, 0.60),
            ("Qwant",      f"https://www.qwant.com/?q={urllib.parse.quote(query)}", proxy_handler, 0.55),
            ("Startpage",  f"https://www.startpage.com/sp/search?query={urllib.parse.quote(query)}", proxy_handler, 0.55),
        ]
        
        def extract_text(html):
            # 去 script 和 style
            text = re.sub(r'<script[^>]*>.*?</script>', '', html, flags=re.DOTALL)
            text = re.sub(r'<style[^>]*>.*?</style>', '', text, flags=re.DOTALL)
            text = re.sub(r'<[^>]+>', '', text)
            text = text.replace('&amp;','&').replace('&lt;','<').replace('&gt;','>')
            text = text.replace('&quot;','"').replace('&#39;',"'")
            # 按行提取并过滤
            lines = [l.strip() for l in text.split('\n') if len(l.strip()) > 10]
            # 过滤导航/广告/空行
            skip_words = ['cookie','adblock','consent','sign in','sign up','登录','注册',
                          'setting','settings','menu','navigation','privacy','terms of',
                          'all regions','belgium','netherlands','indonesia','malaysia',
                          'language','region','accepted','necessary','functional']
            # 保留看起来像结果的行（包含中文或长度适中）
            result_lines = []
            for l in lines:
                ll = l.lower()
                if any(x in ll for x in skip_words):
                    continue
                # 有中文或明显的内容行
                if any('\u4e00' <= c <= '\u9fff' for c in l) or (25 < len(l) < 200):
                    result_lines.append(l)
            # 如果中文结果太少，扩大范围取
            if sum(1 for l in result_lines if any('\u4e00' <= c <= '\u9fff' for c in l)) < 3:
                result_lines += [l for l in lines if 20 < len(l) < 300 
                                 and not any(x in l.lower() for x in skip_words)]
            return '\n'.join(result_lines[:12])[:600]
        
        for name, url, handler, base_conf in engines:
            try:
                opener = urllib.request.build_opener(handler) if handler else urllib.request.build_opener()
                opener.addheaders = [
                    ('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'),
                    ('Accept-Language', 'zh-CN,zh;q=0.9,en;q=0.8'),
                ]
                with opener.open(url, timeout=10) as resp:
                    clean = extract_text(resp.read().decode('utf-8', errors='replace'))
                    if clean:
                        return [VerificationSource(
                            source_type=SourceType.WEB_SEARCH,
                            content=clean,
                            confidence=base_conf,
                            timestamp=datetime.now().isoformat(),
                            metadata={"query": query, "source": f"{name.lower().replace(' ','_')}_via_proxy"}
                        )]
            except Exception as e:
                logger.debug(f"多引擎搜索: {name} 失败 ({str(e)[:50]})")
                continue
        
        # 兜底：WolframAlpha 知识查询
        try:
            wa = f"https://www.wolframalpha.com/input?i={urllib.parse.quote(query)}"
            op = urllib.request.build_opener(proxy_handler)
            op.addheaders = [('User-Agent', 'Mozilla/5.0')]
            with op.open(wa, timeout=8) as r:
                clean = extract_text(r.read().decode('utf-8', errors='replace'))[:300]
                if clean:
                    return [VerificationSource(
                        source_type=SourceType.WEB_SEARCH,
                        content=f"[WolframAlpha]\n{clean}",
                        confidence=0.50,
                        timestamp=datetime.now().isoformat(),
                        metadata={"query": query, "source": "wolframalpha_via_proxy"}
                    )]
        except Exception:
            pass
        
        return []
    
    def search_knowledge_graph(self, entity: str) -> List[VerificationSource]:
        """从知识图谱搜索"""
        # 实际实现需要调用 ontology 模块
        return []

    def detect_cognitive_biases(self, statement: str) -> Dict[str, Any]:
        """
        检测陈述中可能存在的认知偏差

        10 种常见认知偏差检测，用于交叉验证时辅助判断。
        当检测到偏差时，降低对应来源的可信度。

        Args:
            statement: 待检测的陈述

        Returns:
            {
                "biases_detected": List[str],
                "bias_details": List[Dict],
                "confidence_penalty": float,  # 0.0 - 0.3
                "bias_weight": float,         # 0.0 - 1.0
                "recommendation": str
            }
        """
        result = {
            "biases_detected": [],
            "bias_details": [],
            "confidence_penalty": 0.0,
            "bias_weight": 0.0,
            "recommendation": ""
        }

        text = statement.lower()
        bias_count = 0

        # 1. 确认偏误 — "果然/不出所料/我就知道" + 无相反证据
        if re.search(r'果然|不出所料|我就知道|早就说了|早就说|果不其然', text):
            bias_count += 1
            result["biases_detected"].append("确认偏误")
            result["bias_details"].append({
                "name": "确认偏误",
                "trigger": "果然/不出所料/我就知道",
                "description": "倾向于确认已有信念，忽略反证",
                "penalty": 0.15
            })

        # 2. 锚定效应 — "至少/至少也要/不会低于" 等绝对下限表述
        if re.search(r'至少|最低|不会低于|不低于|少说也要|少说得|最起码|最差也', text):
            bias_count += 1
            result["biases_detected"].append("锚定效应")
            result["bias_details"].append({
                "name": "锚定效应",
                "trigger": "至少/不低于/最起码",
                "description": "过度依赖最先获得的信息（锚点）",
                "penalty": 0.15
            })

        # 3. 可得性启发 — "最近/经常/总是" + 过度推广
        if re.search(r'最近\s*(总是|一直|经常)|天天\s*(都|在)|从来没|永远不', text):
            bias_count += 1
            result["biases_detected"].append("可得性启发")
            result["bias_details"].append({
                "name": "可得性启发",
                "trigger": "最近总是/从来不/永远不",
                "description": "凭实例的易得性判断概率，而非统计事实",
                "penalty": 0.12
            })

        # 4. 损失厌恶 — "宁可/宁愿/风险太大/万一" + 回避表述
        if re.search(r'宁可|宁愿|风险太大|万一\s*(失败|亏|赔|出错)|不能\s*(冒|赌)|保险起见', text):
            bias_count += 1
            result["biases_detected"].append("损失厌恶")
            result["bias_details"].append({
                "name": "损失厌恶",
                "trigger": "宁可/风险太大/不能冒",
                "description": "对损失的敏感度远高于收益",
                "penalty": 0.10
            })

        # 5. 框架效应 — "100% 安全/零风险/绝对保证" 绝对化表述
        if re.search(r'100%|百分之百|零风险|绝对.*(保证|安全|正确)|毫无.*(问题|风险)|一定\s*(会|能|行)', text):
            bias_count += 1
            result["biases_detected"].append("框架效应")
            result["bias_details"].append({
                "name": "框架效应",
                "trigger": "100%/零风险/绝对保证",
                "description": "表述方式影响判断，绝对化表述可能隐藏风险",
                "penalty": 0.20
            })

        # 6. 过度自信 — "我敢肯定/毫无疑问/显而易见" 超肯定表述
        if re.search(r'我敢.*(肯定|保证|打赌)|毫无疑问|毋庸置疑|显而易见|明摆着|摆明了', text):
            bias_count += 1
            result["biases_detected"].append("过度自信")
            result["bias_details"].append({
                "name": "过度自信",
                "trigger": "我敢肯定/毫无疑问/显而易见",
                "description": "对自身判断过于自信，低估不确定性",
                "penalty": 0.18
            })

        # 7. 沉没成本谬误 — "已经投入/不能白费/硬着头皮" 投入导向
        if re.search(r'已经\s*(投入|花了|做了)这么多|不能\s*(白费|浪费|放弃)|硬着头皮|骑虎难下', text):
            bias_count += 1
            result["biases_detected"].append("沉没成本谬误")
            result["bias_details"].append({
                "name": "沉没成本谬误",
                "trigger": "已经投入了这么多/不能白费",
                "description": "受已投入成本影响，忽视未来预期",
                "penalty": 0.15
            })

        # 8. 代表性启发 — "这不就是/跟...一样/典型的" 过度类比
        if re.search(r'这不\s*(就是|跟)|跟.*一\s*(样|模一样)|典型的|又是个|老\s*(套路|路子|规矩)', text):
            bias_count += 1
            result["biases_detected"].append("代表性启发")
            result["bias_details"].append({
                "name": "代表性启发",
                "trigger": "这不就是/跟...一样/典型的",
                "description": "基于典型特征判断，忽略基础概率",
                "penalty": 0.12
            })

        # 9. 群体思维 — "大家都/所有人都/没有人认为" 从众表述
        if re.search(r'大家\s*(都|一致|全)|所有人\s*(都|一致|全)|没有人\s*(认为|觉得|反对)|一致\s*(认为|同意|通过)|都说|都认为|都觉得', text):
            bias_count += 1
            result["biases_detected"].append("群体思维")
            result["bias_details"].append({
                "name": "群体思维",
                "trigger": "大家都/所有人都/一致同意",
                "description": "追求共识而忽略异见，压制批判性思维",
                "penalty": 0.15
            })

        # 10. 乐观偏见 — "没问题/肯定行/小事一桩" 过度乐观
        if re.search(r'小事一桩|肯定\s*(行|能|没问题)|包在.*身上|包在我身上|易如反掌|不在话下', text):
            bias_count += 1
            result["biases_detected"].append("乐观偏见")
            result["bias_details"].append({
                "name": "乐观偏见",
                "trigger": "小事一桩/肯定行/包在我身上",
                "description": "对结果过于乐观，低估风险和难度",
                "penalty": 0.10
            })

        # 计算总惩罚和权重
        if bias_count > 0:
            # 每项偏差加 0.05 基础惩罚，特殊项额外累加
            total_penalty = sum(b["penalty"] for b in result["bias_details"])
            result["confidence_penalty"] = min(0.3, total_penalty)
            result["bias_weight"] = min(1.0, bias_count * 0.15)

            if result["bias_weight"] < 0.3:
                result["recommendation"] = "存在轻度认知偏差，建议对来源持审慎态度"
            elif result["bias_weight"] < 0.6:
                result["recommendation"] = "存在中度认知偏差，建议交叉验证后再引用"
            else:
                result["recommendation"] = "存在严重认知偏差，建议寻找更多独立来源验证"

        return result

    def verify_image_claim(self, image_source: str, claim: str) -> List[VerificationSource]:
        """
        使用 OCR2 验证图像相关声明

        Args:
            image_source: 图片源（URL、路径、Base64）
            claim: 待验证的声明

        Returns:
            验证来源列表
        """
        results = []

        try:
            from deepseek_ocr2_adapter import get_adapter
            adapter = get_adapter()

            # 调用 OCR2 验证声明
            verify_result = adapter.verify_claim(image_source, claim)

            if verify_result.get('verified', False):
                results.append(VerificationSource(
                    source_type=SourceType.IMAGE_ANALYSIS,
                    content=f"图像验证通过: {verify_result.get('evidence', '')}",
                    confidence=verify_result.get('confidence', 0.8),
                    timestamp=datetime.now().isoformat(),
                    metadata={
                        'image_source': image_source[:100] if len(image_source) > 100 else image_source,
                        'claim': claim
                    }
                ))
            else:
                results.append(VerificationSource(
                    source_type=SourceType.IMAGE_ANALYSIS,
                    content=f"图像验证未通过: {verify_result.get('evidence', '')}",
                    confidence=1.0 - verify_result.get('confidence', 0.5),
                    timestamp=datetime.now().isoformat(),
                    metadata={
                        'image_source': image_source[:100] if len(image_source) > 100 else image_source,
                        'claim': claim,
                        'verified': False
                    }
                ))

        except Exception as e:
            logger.warning(f"OCR2 图像验证失败: {e}")

        return results
    
    def cross_validate(
        self,
        statement: str,
        sources: List[VerificationSource],
        agreement_threshold: float = 0.7
    ) -> CrossValidationResult:
        """
        交叉验证
        
        Args:
            statement: 待验证的陈述
            sources: 验证来源列表
            agreement_threshold: 一致性阈值
        
        Returns:
            CrossValidationResult
        """
        if not sources:
            return CrossValidationResult(
                statement=statement,
                is_verified=False,
                confidence=0.0,
                sources=[],
                agreements=0,
                disagreements=0,
                consensus="insufficient_data",
                analysis="没有足够的验证来源"
            )
        
        # 计算加权一致性
        weighted_agreements = 0.0
        weighted_disagreements = 0.0
        total_weight = 0.0
        
        agreements = 0
        disagreements = 0
        
        for source in sources:
            # 过滤过低置信度的来源（避免 recall keyword_fallback 稀释验证）
            if source.confidence < 0.1:
                continue
            weight = self.SOURCE_WEIGHTS.get(source.source_type, 0.5)
            total_weight += weight
            
            # 中英文兼容的关键词重叠计算
            # 优先 jieba 分词（中文），降级到空格分词（英文）
            try:
                import jieba
                statement_words = set(jieba.cut_for_search(statement.lower()))
                source_words = set(jieba.cut_for_search(source.content.lower()))
            except ImportError:
                statement_words = set(statement.lower().split())
                source_words = set(source.content.lower().split())
            
            # 额外提取数字单位模式（如 "15层"、"29个"、"4个"）确保关键数值匹配
            import re
            num_pattern = re.findall(r'\d+\s*[a-zA-Z\u4e00-\u9fff]+', statement.lower())
            source_num_pattern = re.findall(r'\d+\s*[a-zA-Z\u4e00-\u9fff]+', source.content.lower())
            statement_words.update(num_pattern)
            source_words.update(source_num_pattern)
            
            # 数值冲突检测：如果两者都有数字+单位组合，但数值不同 → 视为不一致
            numeric_conflict = False
            for sn in source_num_pattern:
                sn_val = re.match(r'(\d+)', sn)
                if not sn_val: continue
                for stn in num_pattern:
                    stn_val = re.match(r'(\d+)', stn)
                    if not stn_val: continue
                    # 同单位但数值不同 → 冲突
                    if sn[sn_val.end():] == stn[stn_val.end():] and sn_val.group(1) != stn_val.group(1):
                        numeric_conflict = True
                        break
                if numeric_conflict:
                    break
            
            overlap = len(statement_words & source_words)
            overlap_ratio = overlap / max(len(statement_words), 1)
            
            # 有数值冲突时直接视为 disagree
            if numeric_conflict:
                weighted_disagreements += weight * source.confidence
                disagreements += 1
            elif overlap_ratio >= 0.3:  # 30% 以上关键词匹配视为一致
                weighted_agreements += weight * source.confidence
                agreements += 1
            else:
                weighted_disagreements += weight * source.confidence
                disagreements += 1
        
        # 计算一致性比率
        if total_weight > 0:
            agreement_ratio = weighted_agreements / total_weight
        else:
            agreement_ratio = 0.0
        
        # 判断共识程度
        if agreement_ratio >= 0.8:
            consensus = "strong_agreement"
            is_verified = True
            confidence = agreement_ratio
        elif agreement_ratio >= agreement_threshold:
            consensus = "weak_agreement"
            is_verified = True
            confidence = agreement_ratio * 0.9
        elif agreement_ratio >= 0.4:
            consensus = "disagreement"
            is_verified = False
            confidence = agreement_ratio * 0.5
        else:
            consensus = "insufficient_data"
            is_verified = False
            confidence = agreement_ratio * 0.3
        
        # 生成分析
        analysis = self._generate_analysis(statement, sources, agreements, disagreements, consensus)
        
        return CrossValidationResult(
            statement=statement,
            is_verified=is_verified,
            confidence=confidence,
            sources=sources,
            agreements=agreements,
            disagreements=disagreements,
            consensus=consensus,
            analysis=analysis
        )
    
    def _generate_analysis(
        self,
        statement: str,
        sources: List[VerificationSource],
        agreements: int,
        disagreements: int,
        consensus: str
    ) -> str:
        """生成分析报告"""
        total = agreements + disagreements
        
        analysis_parts = [
            f"交叉验证结果：{agreements}/{total} 个来源支持",
            f"共识程度：{consensus}",
        ]
        
        if consensus == "strong_agreement":
            analysis_parts.append("多个独立来源一致确认该信息，可信度高")
        elif consensus == "weak_agreement":
            analysis_parts.append("多数来源支持该信息，但存在分歧，建议进一步验证")
        elif consensus == "disagreement":
            analysis_parts.append("来源之间存在明显分歧，信息可靠性存疑")
        else:
            analysis_parts.append("验证来源不足，无法确定信息可靠性")
        
        return " | ".join(analysis_parts)


class ThinkingSkillVerifier:
    """
    思考技能验证器
    
    使用思考技能分析不确定性。
    """
    
    # 思考技能触发规则
    SKILL_TRIGGERS = {
        "critical-thinking": {
            "triggers": ["验证", "确定", "可靠", "准确", "是真的吗"],
            "questions": [
                "这个说法的证据是什么？",
                "有没有反例或例外情况？",
                "是否存在逻辑谬误？",
                "信息来源是否可靠？"
            ]
        },
        "systems-thinking": {
            "triggers": ["为什么", "原因", "影响", "关系"],
            "questions": [
                "这个信息在更大系统中处于什么位置？",
                "有哪些相关因素？",
                "是否存在反馈循环？"
            ]
        },
        "first-principles": {
            "triggers": ["本质", "根本", "基础", "原理"],
            "questions": [
                "这个说法建立在什么假设之上？",
                "这些假设是否成立？",
                "从基本事实出发，结论是否必然？"
            ]
        },
        "analogical-thinking": {
            "triggers": ["类似", "像", "比较", "类比"],
            "questions": [
                "有没有类似的已知案例？",
                "相似之处和差异是什么？",
                "类比是否恰当？"
            ]
        }
    }
    
    def detect_applicable_skill(self, statement: str) -> Optional[str]:
        """检测适用的思考技能"""
        statement_lower = statement.lower()
        
        for skill, config in self.SKILL_TRIGGERS.items():
            for trigger in config["triggers"]:
                if trigger in statement_lower:
                    return skill
        
        return None
    
    def generate_verification_questions(
        self,
        statement: str,
        skill: str = None
    ) -> List[str]:
        """
        生成验证问题
        
        Args:
            statement: 待验证陈述
            skill: 指定的思考技能（可选）
        
        Returns:
            验证问题列表
        """
        if skill is None:
            skill = self.detect_applicable_skill(statement)
        
        if skill and skill in self.SKILL_TRIGGERS:
            return self.SKILL_TRIGGERS[skill]["questions"]
        
        # 默认问题
        return [
            "这个说法有证据支撑吗？",
            "有没有反例或例外情况？",
            "这个信息是否可能已经过时？",
            "是否存在歧义或多重理解？"
        ]
    
    def analyze_with_thinking(
        self,
        statement: str,
        context: Dict = None,
        skill: str = None
    ) -> Dict:
        """
        使用思考技能分析
        
        Args:
            statement: 待分析陈述
            context: 上下文
            skill: 指定的思考技能
        
        Returns:
            {
                "skill_used": str,
                "questions": [...],
                "analysis": str,
                "confidence_adjustment": float,
                "issues_found": [...]
            }
        """
        if skill is None:
            skill = self.detect_applicable_skill(statement) or "critical-thinking"
        
        questions = self.generate_verification_questions(statement, skill)
        
        # 分析问题
        issues_found = []
        confidence_adjustment = 0.0
        
        # 检查绝对化表述
        absolute_patterns = [
            (r"一定", "过于绝对"),
            (r"肯定", "过于绝对"),
            (r"绝对", "过于绝对"),
            (r"所有.*都", "可能存在例外"),
            (r"没有任何", "可能存在例外"),
        ]
        
        for pattern, issue in absolute_patterns:
            if re.search(pattern, statement):
                issues_found.append(issue)
                confidence_adjustment -= 0.1
        
        # 检查数据来源
        if re.search(r'\d+', statement):
            if not re.search(r'来源|根据|数据显示|统计', statement):
                issues_found.append("包含数据但未注明来源")
                confidence_adjustment -= 0.15
        
        # 检查时效性
        if not re.search(r'目前|现在|截至|当前|今天|今年', statement):
            if re.search(r'是|为|有|在', statement):
                issues_found.append("可能缺乏时效性说明")
                confidence_adjustment -= 0.05
        
        # 生成分析
        analysis = self._generate_thinking_analysis(skill, questions, issues_found)
        
        return {
            "skill_used": skill,
            "questions": questions,
            "analysis": analysis,
            "confidence_adjustment": confidence_adjustment,
            "issues_found": issues_found
        }
    
    def _generate_thinking_analysis(
        self,
        skill: str,
        questions: List[str],
        issues: List[str]
    ) -> str:
        """生成思考分析"""
        skill_names = {
            "critical-thinking": "批判性思维",
            "systems-thinking": "系统思维",
            "first-principles": "第一性原理",
            "analogical-thinking": "类比思维"
        }
        
        parts = [f"使用 {skill_names.get(skill, skill)} 分析："]
        
        if issues:
            parts.append(f"发现 {len(issues)} 个潜在问题：")
            for issue in issues:
                parts.append(f"  - {issue}")
        else:
            parts.append("未发现明显问题")
        
        parts.append(f"\n建议思考的问题：")
        for q in questions[:3]:
            parts.append(f"  ? {q}")
        
        return "\n".join(parts)


class EnhancedHallucinationGuard:
    """
    增强版防幻觉守护系统
    
    整合多源交叉验证和思考能力。
    """
    
    def __init__(self, workspace_path: str = None):
        self.workspace_path = Path(workspace_path or 
            os.path.expanduser("~/.openclaw/workspace"))
        
        # 初始化组件
        self.cross_validator = MultiSourceCrossValidator(str(self.workspace_path))
        self.thinking_verifier = ThinkingSkillVerifier()
        
        # 加载基础防幻觉系统
        try:
            from hallucination_guard import HallucinationGuard
            self.base_guard = HallucinationGuard(str(self.workspace_path))
        except:
            self.base_guard = None
        
        logger.info("增强版防幻觉系统初始化完成")
    
    def determine_verification_level(self, confidence: float) -> VerificationLevel:
        """根据置信度确定验证级别"""
        if confidence >= 0.9:
            return VerificationLevel.NONE
        elif confidence >= 0.7:
            return VerificationLevel.LIGHT
        elif confidence >= 0.5:
            return VerificationLevel.MODERATE
        elif confidence >= 0.3:
            return VerificationLevel.DEEP
        else:
            return VerificationLevel.EXHAUSTIVE
    
    def verify_with_cross_validation(
        self,
        statement: str,
        initial_confidence: float = 0.5,
        use_web_search: bool = True,
        use_thinking: bool = True
    ) -> Dict:
        """
        使用交叉验证和思考能力验证陈述
        
        Args:
            statement: 待验证陈述
            initial_confidence: 初始置信度
            use_web_search: 是否使用网络搜索
            use_thinking: 是否使用思考技能
        
        Returns:
            {
                "statement": str,
                "initial_confidence": float,
                "final_confidence": float,
                "verification_level": str,
                "cross_validation": CrossValidationResult,
                "thinking_analysis": Dict,
                "is_reliable": bool,
                "recommendation": str
            }
        """
        result = {
            "statement": statement,
            "initial_confidence": initial_confidence,
            "final_confidence": initial_confidence,
            "verification_level": None,
            "cross_validation": None,
            "thinking_analysis": None,
            "is_reliable": False,
            "recommendation": ""
        }
        
        # 1. 确定验证级别
        level = self.determine_verification_level(initial_confidence)
        result["verification_level"] = level.value
        
        if level == VerificationLevel.NONE:
            result["is_reliable"] = True
            result["recommendation"] = "置信度足够高，无需额外验证"
            return result
        
        # 2. 收集验证来源
        sources = []
        
        # 2.1 内部记忆
        memory_sources = self.cross_validator.search_internal_memory(statement)
        sources.extend(memory_sources)
        
        # 2.1.5 调 recall() 补充证据（中文兼容，不受空格分词限制）
        try:
            from xiaoyi_claw_api import XiaoYiClawLLM
            _claw = XiaoYiClawLLM()
            recall_results = _claw.recall(statement, top_k=3)
            for r in recall_results:
                content = r.get('content', r.get('result', ''))[:500]
                if content and content not in [s.content for s in sources]:
                    sources.append(VerificationSource(
                        source_type=SourceType.INTERNAL_MEMORY,
                        content=content,
                        confidence=r.get('score', 0.5) * 0.8,
                    ))
        except Exception:
            pass
        
        # 2.2 网络搜索（如果启用）
        if use_web_search and level in [VerificationLevel.MODERATE, VerificationLevel.DEEP, VerificationLevel.EXHAUSTIVE]:
            # 实际实现需要调用 xiaoyi-web-search
            # web_sources = self._search_web(statement)
            # sources.extend(web_sources)
            pass
        
        # 3. 交叉验证
        if sources:
            cv_result = self.cross_validator.cross_validate(statement, sources)
            result["cross_validation"] = {
                "is_verified": cv_result.is_verified,
                "confidence": cv_result.confidence,
                "agreements": cv_result.agreements,
                "disagreements": cv_result.disagreements,
                "consensus": cv_result.consensus,
                "analysis": cv_result.analysis
            }
            
            # 基于 agreement_ratio 调整置信度
            # 用实际一致性比率替代二值逻辑
            cv_conf = cv_result.confidence
            if cv_result.consensus in ("strong_agreement", "weak_agreement"):
                adjusted = min(1.0, initial_confidence + 0.3 * cv_conf)
            elif cv_result.consensus == "disagreement":
                adjusted = initial_confidence * (0.5 + 0.3 * cv_conf)
            else:
                adjusted = initial_confidence * 0.2
            result["final_confidence"] = min(1.0, max(0.1, round(adjusted, 2)))
        
        # 4. 思考技能分析
        if use_thinking and level in [VerificationLevel.DEEP, VerificationLevel.EXHAUSTIVE]:
            thinking_result = self.thinking_verifier.analyze_with_thinking(statement)
            result["thinking_analysis"] = thinking_result
            
            # 根据思考结果调整置信度
            result["final_confidence"] += thinking_result["confidence_adjustment"]
            result["final_confidence"] = max(0.0, min(1.0, result["final_confidence"]))
        
        # 5. 认知偏差检测（对所有验证级别都做快速检测，深级做完整检测）
        bias_result = self.cross_validator.detect_cognitive_biases(statement)
        if bias_result["biases_detected"]:
            result["cognitive_biases"] = bias_result
            # 根据偏差检测调整置信度
            result["final_confidence"] -= bias_result["confidence_penalty"]
            result["final_confidence"] = max(0.0, min(1.0, result["final_confidence"]))
            logger.info(f"认知偏差检测: 发现{len(bias_result['biases_detected'])}个偏差, 置信度惩罚{bias_result['confidence_penalty']:.2f}")
        
        # 6. 最终判断
        result["is_reliable"] = result["final_confidence"] >= 0.6

        # 6. 生成建议
        result["recommendation"] = self._generate_recommendation(result)

        return result

    def verify_image_statement(
        self,
        image_source: str,
        claim: str,
        use_ocr2: bool = True
    ) -> Dict:
        """
        验证图像相关声明

        Args:
            image_source: 图片源
            claim: 关于图片的声明
            use_ocr2: 是否使用 OCR2 验证

        Returns:
            {
                "statement": str,
                "image_source": str,
                "is_verified": bool,
                "confidence": float,
                "sources": List,
                "analysis": str
            }
        """
        result = {
            "statement": claim,
            "image_source": image_source[:100] if len(image_source) > 100 else image_source,
            "is_verified": False,
            "confidence": 0.0,
            "sources": [],
            "analysis": ""
        }

        # 1. 使用 OCR2 验证
        if use_ocr2:
            ocr_sources = self.cross_validator.verify_image_claim(image_source, claim)
            result["sources"].extend([{
                "type": s.source_type.value,
                "content": s.content,
                "confidence": s.confidence
            } for s in ocr_sources])

            if ocr_sources:
                # 取最高置信度
                result["confidence"] = max(s.confidence for s in ocr_sources)
                result["is_verified"] = any(
                    s.source_type == SourceType.IMAGE_ANALYSIS and "验证通过" in s.content
                    for s in ocr_sources
                )

        # 2. 生成分析
        if result["is_verified"]:
            result["analysis"] = f"图像分析验证了声明: {claim}"
        else:
            result["analysis"] = f"图像分析未能验证声明: {claim}"

        return result
        
        return result
    
    def _generate_recommendation(self, result: Dict) -> str:
        """生成建议"""
        level = result["verification_level"]
        confidence = result["final_confidence"]
        
        if result["is_reliable"]:
            if confidence >= 0.8:
                return "信息经过多源验证，可信度高"
            else:
                return "信息基本可靠，但建议保持适度谨慎"
        else:
            if level == "exhaustive":
                return "信息可靠性存疑，强烈建议查证或暂缓使用"
            elif level == "deep":
                return "信息存在不确定性，建议进一步验证后再使用"
            else:
                return "信息可能不够准确，建议核实关键细节"
    
    def express_with_verification(
        self,
        content: str,
        verification_result: Dict
    ) -> str:
        """
        根据验证结果表达不确定性
        
        Args:
            content: 原始内容
            verification_result: 验证结果
        
        Returns:
            带不确定性表达的内容
        """
        confidence = verification_result["final_confidence"]
        
        if confidence >= 0.9:
            return content
        elif confidence >= 0.7:
            return f"根据多方验证，{content}"
        elif confidence >= 0.5:
            return f"我查到的信息显示{content}，但建议你再确认一下"
        elif confidence >= 0.3:
            issues = verification_result.get("thinking_analysis", {}).get("issues_found", [])
            if issues:
                return f"这个信息可能存在问题（{issues[0]}），建议查证后再使用"
            return f"这个信息不太确定，{content}，建议你查证"
        else:
            return f"这个信息可靠性较低，建议查阅权威资料确认"


# CLI 接口
def main():
    """命令行接口"""
    import argparse
    
    parser = argparse.ArgumentParser(description="增强版防幻觉系统")
    parser.add_argument("command", choices=["verify", "level", "questions"])
    parser.add_argument("--statement", help="待验证陈述")
    parser.add_argument("--confidence", type=float, default=0.5, help="初始置信度")
    parser.add_argument("--skill", help="指定思考技能")
    
    args = parser.parse_args()
    
    guard = EnhancedHallucinationGuard()
    
    if args.command == "verify":
        if not args.statement:
            print("错误: 需要提供 --statement")
            return
        
        result = guard.verify_with_cross_validation(
            args.statement,
            initial_confidence=args.confidence
        )
        
        print(f"验证结果:")
        print(f"  初始置信度: {result['initial_confidence']:.2f}")
        print(f"  最终置信度: {result['final_confidence']:.2f}")
        print(f"  验证级别: {result['verification_level']}")
        print(f"  是否可靠: {'✅' if result['is_reliable'] else '❌'}")
        print(f"  建议: {result['recommendation']}")
        
        if result.get("cross_validation"):
            cv = result["cross_validation"]
            print(f"\n交叉验证:")
            print(f"  一致来源: {cv['agreements']} 个")
            print(f"  分歧来源: {cv['disagreements']} 个")
            print(f"  共识程度: {cv['consensus']}")
        
        if result.get("thinking_analysis"):
            ta = result["thinking_analysis"]
            print(f"\n思考分析 ({ta['skill_used']}):")
            print(ta["analysis"])
    
    elif args.command == "level":
        level = guard.determine_verification_level(args.confidence)
        print(f"置信度 {args.confidence:.2f} 对应验证级别: {level.value}")
    
    elif args.command == "questions":
        if not args.statement:
            print("错误: 需要提供 --statement")
            return
        
        verifier = ThinkingSkillVerifier()
        skill = args.skill or verifier.detect_applicable_skill(args.statement)
        questions = verifier.generate_verification_questions(args.statement, skill)
        
        print(f"验证问题 ({skill or 'default'}):")
        for i, q in enumerate(questions, 1):
            print(f"  {i}. {q}")


if __name__ == "__main__":
    main()
