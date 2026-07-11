#!/usr/bin/env python3
"""
injection_scanner.py — Skill Bank 合约内容扫描器

在 COSPLAY/LFM Skill Bank 将 ProtoSkill 毕业为正式 Skill 之前，
检测合约文本中的 prompt injection 特征，防止恶意技能污染记忆系统。

检测策略：
  1. 指令注入模式（"忽略以上指令"、"system:" 前缀等）
  2. 可疑指令模板（角色扮演劫持、权限提升语句）
  3. 隐蔽编码特征（base64 片段、unicode 转义序列）
  4. 越权操作指令（文件删除、网络请求、进程执行）

分级响应：
  - 高分风险（score >= 0.8）：直接隔离，不毕业
  - 中分风险（0.5 <= score < 0.8）：进入人工审核队列
  - 低分风险（score < 0.5）：放行但标记监控
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

logger = logging.getLogger("galaxyos.injection_scanner")

# ════════════════════════════════════════════════════════════════
# 注入特征库
# ════════════════════════════════════════════════════════════════

# 高风险模式（权重 1.0）— 明确的指令劫持
HIGH_RISK_PATTERNS: List[tuple] = [
    # 中英文"忽略以上指令"变体
    (r"忽略以上.*指令", "ignore_instructions_cn"),
    (r"ignore\s+(all\s+)?(previous|above|prior)\s+instructions", "ignore_instructions_en"),
    (r"disregard\s+(all\s+)?(previous|prior)\s+", "disregard_instructions"),
    (r"forget\s+(everything|all\s+previous)", "forget_previous"),
    # system: 前缀伪造
    (r"^system\s*:", "system_prefix"),
    (r"^\[system\]", "system_bracket"),
    (r"<\s*system\s*>", "system_tag"),
    # 角色劫持
    (r"you\s+are\s+now\s+(a|an)\s+(different|new|admin|root|developer)", "role_hijack"),
    (r"从现在起.*你.*是", "role_hijack_cn"),
    (r"假装你是.*(管理员|开发者|root)", "role_hijack_cn_2"),
    # 权限提升
    (r"(grant|give)\s+me\s+(admin|root|sudo|superuser)", "privilege_escalation"),
    (r"以.*(管理员|root|sudo).*身份", "privilege_escalation_cn"),
]

# 中风险模式（权重 0.6）— 可疑但可能有合法用途
MEDIUM_RISK_PATTERNS: List[tuple] = [
    # 指令模板
    (r"^\s*(instruction|command|directive)\s*:", "instruction_prefix"),
    (r"^\s*\[.*(instruction|command|directive).*\]", "instruction_bracket"),
    # 越权操作
    (r"rm\s+-rf\s+/", "rm_rf_root"),
    (r"sudo\s+", "sudo_usage"),
    (r"chmod\s+\d{3,4}", "chmod_usage"),
    (r"curl\s+.*\|\s*(sh|bash)", "curl_pipe_shell"),
    (r"wget\s+.*\|\s*(sh|bash)", "wget_pipe_shell"),
    (r"eval\s*\(", "eval_usage"),
    (r"exec\s*\(", "exec_usage"),
    (r"subprocess\.(call|run|Popen)", "subprocess_usage"),
    (r"os\.system\s*\(", "os_system_usage"),
    # 网络请求指令
    (r"(fetch|request|send).*http[s]?://", "network_request"),
    (r"requests\.(get|post|put|delete)", "requests_usage"),
]

# 低风险模式（权重 0.3）— 需结合上下文判断
LOW_RISK_PATTERNS: List[tuple] = [
    # base64 片段（长度 > 50 的 base64 字符串）
    (r"[A-Za-z0-9+/]{50,}={0,2}", "base64_fragment"),
    # unicode 转义序列
    (r"\\u[0-9a-fA-F]{4}", "unicode_escape"),
    # 十六进制编码字符串
    (r"\\x[0-9a-fA-F]{2}", "hex_escape"),
    # 长串连续特殊字符
    (r"[^\w\s]{20,}", "long_special_chars"),
]

# ════════════════════════════════════════════════════════════════
# 扫描结果数据结构
# ════════════════════════════════════════════════════════════════

@dataclass
class ScanResult:
    """内容扫描结果"""
    risky: bool = False
    score: float = 0.0
    risk_level: str = "safe"  # safe / low / medium / high
    reason: str = ""
    detected_patterns: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "risky": self.risky,
            "score": self.score,
            "risk_level": self.risk_level,
            "reason": self.reason,
            "detected_patterns": self.detected_patterns,
        }


# ════════════════════════════════════════════════════════════════
# 扫描器主类
# ════════════════════════════════════════════════════════════════

class InjectionScanner:
    """
    Prompt Injection 内容扫描器

    用法：
        scanner = InjectionScanner()
        result = scanner.scan(contract_text)
        if result.risky:
            # 进入审核队列
    """

    # 分级阈值
    HIGH_THRESHOLD = 0.8
    MEDIUM_THRESHOLD = 0.5
    LOW_THRESHOLD = 0.3

    def __init__(self):
        # 预编译正则
        self._high_patterns = [(re.compile(p, re.IGNORECASE | re.MULTILINE), name, 1.0)
                               for p, name in HIGH_RISK_PATTERNS]
        self._medium_patterns = [(re.compile(p, re.IGNORECASE | re.MULTILINE), name, 0.6)
                                 for p, name in MEDIUM_RISK_PATTERNS]
        self._low_patterns = [(re.compile(p, re.MULTILINE), name, 0.3)
                              for p, name in LOW_RISK_PATTERNS]

        # 合法技能样本（用于回归测试，控制误报）
        self._legitimate_samples = [
            "检索记忆并返回相关结果",
            "分析用户输入的意图并路由到合适的处理流程",
            "存储观察到的信息到记忆系统",
            "Retrieve and rank memories by relevance score",
        ]

    def scan(self, text: str) -> ScanResult:
        """
        扫描文本中的 prompt injection 特征

        Args:
            text: 待扫描的合约文本

        Returns:
            ScanResult: 扫描结果，包含风险分数和检测到的模式
        """
        if not text or not isinstance(text, str):
            return ScanResult()

        result = ScanResult()
        max_score = 0.0
        all_detected = []

        # 扫描高风险模式
        for pattern, name, weight in self._high_patterns:
            matches = pattern.findall(text)
            if matches:
                score = min(weight * (1 + 0.3 * (len(matches) - 1)), 1.0)
                max_score = max(max_score, score)
                all_detected.append({
                    "pattern": name,
                    "weight": weight,
                    "matches": len(matches),
                    "sample": matches[0][:50] if matches else "",
                })

        # 扫描中风险模式
        for pattern, name, weight in self._medium_patterns:
            matches = pattern.findall(text)
            if matches:
                score = min(weight * (1 + 0.2 * (len(matches) - 1)), 0.9)
                max_score = max(max_score, score)
                all_detected.append({
                    "pattern": name,
                    "weight": weight,
                    "matches": len(matches),
                    "sample": matches[0][:50] if matches else "",
                })

        # 扫描低风险模式
        for pattern, name, weight in self._low_patterns:
            matches = pattern.findall(text)
            if matches:
                score = min(weight * len(matches) / 5, 0.5)  # 低风险累计不超过 0.5
                max_score = max(max_score, score)
                all_detected.append({
                    "pattern": name,
                    "weight": weight,
                    "matches": len(matches),
                    "sample": matches[0][:50] if matches else "",
                })

        # 确定风险等级
        result.score = max_score
        result.detected_patterns = all_detected

        if max_score >= self.HIGH_THRESHOLD:
            result.risky = True
            result.risk_level = "high"
            result.reason = f"高风险：检测到 {len(all_detected)} 个注入特征，最高分 {max_score:.2f}"
        elif max_score >= self.MEDIUM_THRESHOLD:
            result.risky = True
            result.risk_level = "medium"
            result.reason = f"中风险：检测到 {len(all_detected)} 个可疑特征，最高分 {max_score:.2f}"
        elif max_score >= self.LOW_THRESHOLD:
            result.risky = False
            result.risk_level = "low"
            result.reason = f"低风险：检测到 {len(all_detected)} 个边缘特征，最高分 {max_score:.2f}"
        else:
            result.risky = False
            result.risk_level = "safe"
            result.reason = "未检测到注入特征"

        return result

    def scan_contract(self, contract) -> ScanResult:
        """
        扫描 LfmSkillEffectsContract 对象

        Args:
            contract: LfmSkillEffectsContract 实例

        Returns:
            ScanResult: 扫描结果
        """
        # 将合约的所有文本字段拼接扫描
        parts = []
        if hasattr(contract, "name") and contract.name:
            parts.append(str(contract.name))
        if hasattr(contract, "description") and contract.description:
            parts.append(str(contract.description))
        if hasattr(contract, "eff_add"):
            parts.extend(str(e) for e in contract.eff_add)
        if hasattr(contract, "eff_del"):
            parts.extend(str(e) for e in contract.eff_del)
        if hasattr(contract, "eff_event"):
            parts.extend(str(e) for e in contract.eff_event)

        combined_text = "\n".join(parts)
        return self.scan(combined_text)

    def scan_skill_text(self, skill_text: str, skill_name: str = "") -> ScanResult:
        """
        扫描技能文本内容

        Args:
            skill_text: 技能正文
            skill_name: 技能名称（用于日志）

        Returns:
            ScanResult: 扫描结果
        """
        result = self.scan(skill_text)
        if result.risky:
            logger.warning(
                f"Skill content flagged: name={skill_name}, "
                f"level={result.risk_level}, score={result.score:.2f}, "
                f"patterns={[p['pattern'] for p in result.detected_patterns]}"
            )
        return result

    def regression_check(self) -> Dict[str, Any]:
        """
        用合法技能样本做回归测试，确保误报率可控

        Returns:
            Dict: 包含 tested_count, false_positive_count, false_positive_rate
        """
        fp_count = 0
        for sample in self._legitimate_samples:
            result = self.scan(sample)
            if result.risky:
                fp_count += 1
                logger.warning(f"False positive on legitimate sample: {sample[:30]}...")

        return {
            "tested_count": len(self._legitimate_samples),
            "false_positive_count": fp_count,
            "false_positive_rate": fp_count / max(len(self._legitimate_samples), 1),
        }


# ════════════════════════════════════════════════════════════════
# 审核队列（内存级，生产环境应持久化）
# ════════════════════════════════════════════════════════════════

class ReviewQueue:
    """隔离审核队列 — 存放被标记的技能合约"""

    def __init__(self, max_size: int = 500):
        self._queue: List[Dict[str, Any]] = []
        self._max_size = max_size

    def enqueue(self, proto_skill: Any, scan_result: ScanResult) -> None:
        """将风险技能加入审核队列"""
        entry = {
            "skill_id": getattr(proto_skill, "skill_id", "unknown"),
            "skill_name": getattr(proto_skill, "name", "unknown"),
            "description": getattr(proto_skill, "description", ""),
            "scan_result": scan_result.to_dict(),
            "enqueued_at": __import__("time").time(),
        }
        self._queue.append(entry)
        if len(self._queue) > self._max_size:
            self._queue = self._queue[-self._max_size:]
        logger.info(f"Skill quarantined: {entry['skill_id']} (level={scan_result.risk_level})")

    def dequeue(self) -> Optional[Dict[str, Any]]:
        """取出队首技能供人工审核"""
        if not self._queue:
            return None
        return self._queue.pop(0)

    def list_pending(self) -> List[Dict[str, Any]]:
        """列出所有待审核技能"""
        return list(self._queue)

    def size(self) -> int:
        return len(self._queue)


# ════════════════════════════════════════════════════════════════
# 来源追溯（污染回滚支持）
# ════════════════════════════════════════════════════════════════

class ProvenanceStore:
    """技能来源追溯 — 记录每个已毕业技能的来源信息，便于污染回滚"""

    def __init__(self):
        self._records: Dict[str, Dict[str, Any]] = {}

    def record(self, skill_name: str, source_info: Dict[str, Any]) -> None:
        """记录技能来源"""
        self._records[skill_name] = {
            "source": source_info.get("source", "unknown"),
            "ts": source_info.get("ts", __import__("time").time()),
            "scan_passed": source_info.get("scan_passed", True),
            "scan_score": source_info.get("scan_score", 0.0),
            "proto_skill_id": source_info.get("proto_skill_id", ""),
        }

    def lookup(self, skill_name: str) -> Optional[Dict[str, Any]]:
        """查询技能来源"""
        return self._records.get(skill_name)

    def find_contaminated(self, min_score: float = 0.5) -> List[str]:
        """找出所有扫描分数超过阈值的已毕业技能（用于回滚）"""
        return [
            name for name, info in self._records.items()
            if info.get("scan_score", 0.0) >= min_score
        ]


# ════════════════════════════════════════════════════════════════
# 模块级单例
# ════════════════════════════════════════════════════════════════

_scanner: Optional[InjectionScanner] = None
_review_queue: Optional[ReviewQueue] = None
_provenance: Optional[ProvenanceStore] = None


def get_scanner() -> InjectionScanner:
    """获取扫描器单例"""
    global _scanner
    if _scanner is None:
        _scanner = InjectionScanner()
    return _scanner


def get_review_queue() -> ReviewQueue:
    """获取审核队列单例"""
    global _review_queue
    if _review_queue is None:
        _review_queue = ReviewQueue()
    return _review_queue


def get_provenance_store() -> ProvenanceStore:
    """获取来源追溯单例"""
    global _provenance
    if _provenance is None:
        _provenance = ProvenanceStore()
    return _provenance


def scan_before_graduate(proto_skill, contract) -> ScanResult:
    """
    毕业前扫描入口 — 供 lfm_skill_bank.py 调用

    Args:
        proto_skill: ProtoSkill 实例
        contract: LfmSkillEffectsContract 实例

    Returns:
        ScanResult: 扫描结果
        - risky=True 且 risk_level="high" → 隔离，不毕业
        - risky=True 且 risk_level="medium" → 进入审核队列
        - risky=False → 放行，允许毕业
    """
    scanner = get_scanner()
    result = scanner.scan_contract(contract)

    if result.risky:
        if result.risk_level == "high":
            # 高分风险：直接隔离
            get_review_queue().enqueue(proto_skill, result)
            logger.warning(
                f"ProtoSkill QUARANTINED (high risk): "
                f"id={getattr(proto_skill, 'skill_id', '?')}, "
                f"score={result.score:.2f}"
            )
        elif result.risk_level == "medium":
            # 中分风险：进入人工审核队列
            get_review_queue().enqueue(proto_skill, result)
            logger.info(
                f"ProtoSkill sent to review (medium risk): "
                f"id={getattr(proto_skill, 'skill_id', '?')}, "
                f"score={result.score:.2f}"
            )

    return result
