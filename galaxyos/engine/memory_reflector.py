#!/usr/bin/env python3
"""
记忆反思模块 (Memory Reflection Module)

让 AI 具备元认知能力：
- 主动发现错误
- 从错误中学习
- 持续优化行为规则
- 避免重复犯错

Author: GalaxyOS
Version: 1.0.0
Created: 2026-04-19
"""

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, List, Any, Tuple
from dataclasses import dataclass, asdict, field
from enum import Enum
import hashlib
from galaxyos.shared.paths import workspace


# ==================== 数据结构 ====================

class ErrorType(Enum):
    """错误类型"""
    COMMAND_FAILURE = "command_failure"      # 命令执行失败
    API_ERROR = "api_error"                  # API 调用异常
    USER_CORRECTION = "user_correction"      # 用户纠正
    KNOWLEDGE_OUTDATED = "knowledge_outdated"  # 知识过时
    BEHAVIOR_VIOLATION = "behavior_violation"  # 违反规则
    UNDERSTANDING_ERROR = "understanding_error"  # 理解错误


class Priority(Enum):
    """优先级"""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class ReflectionStatus(Enum):
    """反思状态"""
    DETECTED = "detected"        # 已检测
    ANALYZED = "analyzed"        # 已分析
    APPLIED = "applied"          # 已应用
    VERIFIED = "verified"        # 已验证
    ROLLED_BACK = "rolled_back"  # 已回滚


@dataclass
class Error:
    """错误记录"""
    id: str
    timestamp: str
    type: ErrorType
    context: str
    detail: str
    source: str  # command, user, self
    session_key: Optional[str] = None
    related_files: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "timestamp": self.timestamp,
            "type": self.type.value,
            "context": self.context,
            "detail": self.detail,
            "source": self.source,
            "session_key": self.session_key,
            "related_files": self.related_files
        }


@dataclass
class Pattern:
    """错误模式"""
    id: str
    error_type: ErrorType
    pattern_signature: str  # 模式签名（用于匹配）
    occurrence_count: int
    first_seen: str
    last_seen: str
    example_errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "id": self.id,
            "error_type": self.error_type.value,
            "pattern_signature": self.pattern_signature,
            "occurrence_count": self.occurrence_count,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
            "example_errors": self.example_errors
        }


@dataclass
class Improvement:
    """改进建议"""
    id: str
    timestamp: str
    priority: Priority
    status: ReflectionStatus

    # 检测信息
    error_id: str
    pattern_id: Optional[str]

    # 分析结果
    root_cause: str
    affected_areas: List[str]

    # 改进建议
    suggestion: str
    target_file: str  # AGENTS.md, SOUL.md, TOOLS.md, MEMORY.md
    target_section: Optional[str] = None
    old_content: Optional[str] = None
    new_content: Optional[str] = None

    # 应用信息
    auto_applicable: bool = False
    applied_at: Optional[str] = None
    backup_path: Optional[str] = None

    # 验证信息
    verified_at: Optional[str] = None
    test_result: Optional[str] = None

    def to_dict(self) -> Dict:
        result = asdict(self)
        # 转换枚举类型为字符串
        result["priority"] = self.priority.value
        result["status"] = self.status.value
        return result


# ==================== 反思记录器 ====================

class ReflectionRecorder:
    """反思记录器 - 记录错误、模式、改进"""

    def __init__(self, workspace_path: str = None):
        self.workspace_path = Path(workspace_path or workspace())
        self.learnings_path = self.workspace_path / ".learnings"
        self.reflections_path = self.learnings_path / "REFLECTIONS.jsonl"
        self.patterns_path = self.learnings_path / "PATTERNS.jsonl"
        self.backups_path = self.learnings_path / "backups"

        # 确保目录存在
        self.learnings_path.mkdir(parents=True, exist_ok=True)
        self.backups_path.mkdir(parents=True, exist_ok=True)

        # 初始化文件
        for path in [self.reflections_path, self.patterns_path]:
            if not path.exists():
                path.touch()

    def _generate_id(self, prefix: str) -> str:
        """生成唯一 ID"""
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
        random_suffix = hashlib.md5(str(datetime.now().timestamp()).encode()).hexdigest()[:8]
        return f"{prefix}-{timestamp}-{random_suffix}"

    def _get_timestamp(self) -> str:
        """获取 ISO-8601 时间戳"""
        return datetime.now(timezone.utc).isoformat()

    def record_error(self, error: Error) -> str:
        """记录错误"""
        error.id = self._generate_id("ERR")
        error.timestamp = self._get_timestamp()

        # 追加到反思记录
        with open(self.reflections_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": "error",
                "data": error.to_dict()
            }, ensure_ascii=False) + "\n")

        return error.id

    def record_pattern(self, pattern: Pattern) -> str:
        """记录模式"""
        pattern.id = self._generate_id("PAT")

        # 追加到模式记录
        with open(self.patterns_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(pattern.to_dict(), ensure_ascii=False) + "\n")

        return pattern.id

    def record_improvement(self, improvement: Improvement) -> str:
        """记录改进"""
        improvement.id = self._generate_id("IMP")
        improvement.timestamp = self._get_timestamp()

        # 追加到反思记录
        with open(self.reflections_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({
                "type": "improvement",
                "data": improvement.to_dict()
            }, ensure_ascii=False) + "\n")

        return improvement.id

    def update_improvement(self, improvement: Improvement):
        """更新改进记录"""
        # 读取所有记录
        records = []
        with open(self.reflections_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    record = json.loads(line)
                    if record["type"] == "improvement" and record["data"]["id"] == improvement.id:
                        record["data"] = improvement.to_dict()
                    records.append(record)

        # 重写文件
        with open(self.reflections_path, "w", encoding="utf-8") as f:
            for record in records:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def get_errors(self, limit: int = 100) -> List[Error]:
        """获取错误列表"""
        errors = []
        if not self.reflections_path.exists():
            return errors

        with open(self.reflections_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    record = json.loads(line)
                    if record["type"] == "error":
                        data = record["data"]
                        errors.append(Error(
                            id=data["id"],
                            timestamp=data["timestamp"],
                            type=ErrorType(data["type"]),
                            context=data["context"],
                            detail=data["detail"],
                            source=data["source"],
                            session_key=data.get("session_key"),
                            related_files=data.get("related_files", [])
                        ))

        return errors[-limit:]

    def get_patterns(self) -> List[Pattern]:
        """获取模式列表"""
        patterns = []
        if not self.patterns_path.exists():
            return patterns

        with open(self.patterns_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    data = json.loads(line)
                    patterns.append(Pattern(
                        id=data["id"],
                        error_type=ErrorType(data["error_type"]),
                        pattern_signature=data["pattern_signature"],
                        occurrence_count=data["occurrence_count"],
                        first_seen=data["first_seen"],
                        last_seen=data["last_seen"],
                        example_errors=data.get("example_errors", [])
                    ))

        return patterns

    def get_pending_improvements(self) -> List[Improvement]:
        """获取待处理的改进"""
        improvements = []
        if not self.reflections_path.exists():
            return improvements

        with open(self.reflections_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    record = json.loads(line)
                    if record["type"] == "improvement":
                        data = record["data"]
                        if data["status"] in ["detected", "analyzed"]:
                            improvements.append(Improvement(
                                id=data["id"],
                                timestamp=data["timestamp"],
                                priority=Priority(data["priority"]),
                                status=ReflectionStatus(data["status"]),
                                error_id=data["error_id"],
                                pattern_id=data.get("pattern_id"),
                                root_cause=data["root_cause"],
                                affected_areas=data["affected_areas"],
                                suggestion=data["suggestion"],
                                target_file=data["target_file"],
                                target_section=data.get("target_section"),
                                old_content=data.get("old_content"),
                                new_content=data.get("new_content"),
                                auto_applicable=data.get("auto_applicable", False),
                                applied_at=data.get("applied_at"),
                                backup_path=data.get("backup_path"),
                                verified_at=data.get("verified_at"),
                                test_result=data.get("test_result")
                            ))

        return improvements


# ==================== 模式识别器 ====================

class PatternDetector:
    """模式识别器 - 检测重复错误模式"""

    def __init__(self, recorder: ReflectionRecorder):
        self.recorder = recorder
        self.pattern_threshold = 3  # 出现 3 次触发反思

    def _extract_signature(self, error: Error) -> str:
        """提取错误签名（用于模式匹配）"""
        # 简化错误详情，提取关键特征
        detail = error.detail.lower()

        # 移除具体数值、路径等可变信息
        detail = re.sub(r'\d+', 'N', detail)
        detail = re.sub(r'/[\w/.-]+', '/PATH', detail)
        detail = re.sub(r'0x[0-9a-f]+', '0xADDR', detail)

        # 提取关键词
        keywords = []
        error_patterns = [
            r'error[:：]\s*([^\n]+)',
            r'failed[:：]\s*([^\n]+)',
            r'cannot\s+(\w+)',
            r'not\s+found',
            r'dimension\s+mismatch',
            r'timeout',
            r'permission\s+denied',
        ]

        for pattern in error_patterns:
            matches = re.findall(pattern, detail, re.IGNORECASE)
            keywords.extend(matches)

        # 组合签名
        signature = f"{error.type.value}:{':'.join(sorted(set(keywords)))}"
        return signature

    def detect_pattern(self, error: Error) -> Optional[Pattern]:
        """检测错误模式"""
        signature = self._extract_signature(error)
        existing_patterns = self.recorder.get_patterns()

        # 查找匹配的现有模式
        for pattern in existing_patterns:
            if pattern.pattern_signature == signature:
                # 更新模式
                pattern.occurrence_count += 1
                pattern.last_seen = self.recorder._get_timestamp()
                pattern.example_errors.append(error.id)

                # 更新记录
                self._update_pattern(pattern)

                # 检查是否达到阈值
                if pattern.occurrence_count >= self.pattern_threshold:
                    return pattern
                return None

        # 创建新模式
        pattern = Pattern(
            id="",  # 将由 recorder 分配
            error_type=error.type,
            pattern_signature=signature,
            occurrence_count=1,
            first_seen=self.recorder._get_timestamp(),
            last_seen=self.recorder._get_timestamp(),
            example_errors=[error.id]
        )

        self.recorder.record_pattern(pattern)
        return None

    def _update_pattern(self, pattern: Pattern):
        """更新模式记录"""
        # 读取所有模式
        patterns_data = []
        with open(self.recorder.patterns_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    data = json.loads(line)
                    if data["id"] == pattern.id:
                        data = pattern.to_dict()
                    patterns_data.append(data)

        # 重写文件
        with open(self.recorder.patterns_path, "w", encoding="utf-8") as f:
            for data in patterns_data:
                f.write(json.dumps(data, ensure_ascii=False) + "\n")


# ==================== 改进生成器 ====================

class ImprovementGenerator:
    """改进生成器 - 分析错误并生成改进建议"""

    # 规则文件映射
    RULE_FILES = {
        "behavior": "AGENTS.md",
        "personality": "SOUL.md",
        "tools": "TOOLS.md",
        "memory": "MEMORY.md",
        "identity": "IDENTITY.md",
        "user": "USER.md"
    }

    # 高优先级关键词（自动应用）
    HIGH_PRIORITY_KEYWORDS = [
        "安全", "security", "删除", "delete", "rm -rf",
        "数据仓库", "repository", "api key", "token",
        "权限", "permission", "禁止", "forbidden"
    ]

    def __init__(self, recorder: ReflectionRecorder, workspace_path: str = None):
        self.recorder = recorder
        self.workspace_path = Path(workspace_path or workspace())

    def analyze_error(self, error: Error, pattern: Optional[Pattern] = None) -> Improvement:
        """分析错误并生成改进建议"""

        # 分析根本原因
        root_cause = self._analyze_root_cause(error)

        # 确定影响范围
        affected_areas = self._determine_affected_areas(error)

        # 生成改进建议
        suggestion, target_file, target_section = self._generate_suggestion(error, root_cause)

        # 判断优先级
        priority = self._determine_priority(error, pattern)

        # 判断是否可自动应用
        auto_applicable = self._can_auto_apply(error, priority)

        improvement = Improvement(
            id="",  # 将由 recorder 分配
            timestamp="",
            priority=priority,
            status=ReflectionStatus.DETECTED,
            error_id=error.id,
            pattern_id=pattern.id if pattern else None,
            root_cause=root_cause,
            affected_areas=affected_areas,
            suggestion=suggestion,
            target_file=target_file,
            target_section=target_section,
            auto_applicable=auto_applicable
        )

        return improvement

    def _analyze_root_cause(self, error: Error) -> str:
        """分析根本原因"""
        detail = error.detail.lower()

        # 常见错误模式分析
        if "dimension" in detail or "维度" in detail:
            return "向量维度配置不一致，需要统一检查"
        elif "timeout" in detail or "超时" in detail:
            return "操作超时，需要增加超时时间或优化性能"
        elif "permission" in detail or "权限" in detail:
            return "权限不足，需要检查访问权限配置"
        elif "not found" in detail or "未找到" in detail:
            return "资源不存在，需要检查路径或先创建资源"
        elif error.type == ErrorType.USER_CORRECTION:
            return "AI 理解或知识有误，需要更新规则或知识"
        elif error.type == ErrorType.BEHAVIOR_VIOLATION:
            return "违反行为规则，需要加强规则执行"
        else:
            return f"未知原因，需要进一步分析: {error.context}"

    def _determine_affected_areas(self, error: Error) -> List[str]:
        """确定影响范围"""
        areas = []

        if error.related_files:
            for file in error.related_files:
                if "memory" in file.lower():
                    areas.append("memory")
                if "skill" in file.lower():
                    areas.append("skills")
                if "config" in file.lower():
                    areas.append("config")

        if not areas:
            areas = ["general"]

        return areas

    def _generate_suggestion(self, error: Error, root_cause: str) -> Tuple[str, str, Optional[str]]:
        """生成改进建议"""
        detail = error.detail.lower()

        # 根据错误类型生成建议
        if "dimension" in detail or "维度" in detail:
            return (
                "在 TOOLS.md 中添加：embedding 调用必须指定 dimensions=4096",
                "TOOLS.md",
                "### Embedding 配置"
            )
        elif error.type == ErrorType.USER_CORRECTION:
            # 分析用户纠正的内容
            if "仓库" in error.context or "repository" in error.context.lower():
                return (
                    f"更新 MEMORY.md 中的数据仓库地址: {error.detail}",
                    "MEMORY.md",
                    "### 数据仓库配置"
                )
            elif "不对" in error.context or "错了" in error.context:
                return (
                    f"根据用户纠正更新规则: {error.detail}",
                    "AGENTS.md",
                    None
                )
        elif error.type == ErrorType.BEHAVIOR_VIOLATION:
            if "tdai_memory_search" in error.detail:
                return (
                    "在 AGENTS.md 中加强规则：涉及历史信息时必须使用 tdai_memory_search",
                    "AGENTS.md",
                    "### 记忆检索协议"
                )

        # 默认建议
        return (
            f"记录此错误以避免重复: {root_cause}",
            "AGENTS.md",
            None
        )

    def _determine_priority(self, error: Error, pattern: Optional[Pattern]) -> Priority:
        """判断优先级"""
        detail = error.detail.lower()
        context = error.context.lower()

        # 检查高优先级关键词
        for keyword in self.HIGH_PRIORITY_KEYWORDS:
            if keyword in detail or keyword in context:
                return Priority.HIGH

        # 重复错误提高优先级
        if pattern and pattern.occurrence_count >= 3:
            return Priority.HIGH
        elif pattern and pattern.occurrence_count >= 2:
            return Priority.MEDIUM

        # 用户纠正优先级较高
        if error.type == ErrorType.USER_CORRECTION:
            return Priority.MEDIUM

        return Priority.LOW

    def _can_auto_apply(self, error: Error, priority: Priority) -> bool:
        """判断是否可自动应用"""
        # 高优先级可以自动应用
        if priority == Priority.HIGH or priority == Priority.CRITICAL:
            # 但排除敏感操作
            detail = error.detail.lower()
            sensitive_keywords = ["api key", "token", "password", "secret", "私钥"]
            for keyword in sensitive_keywords:
                if keyword in detail:
                    return False
            return True

        return False


# ==================== 规则更新器 ====================

class RuleUpdater:
    """规则更新器 - 应用改进到规则文件"""

    def __init__(self, recorder: ReflectionRecorder, workspace_path: str = None):
        self.recorder = recorder
        self.workspace_path = Path(workspace_path or workspace())

    def apply_improvement(self, improvement: Improvement) -> bool:
        """应用改进"""
        target_path = self.workspace_path / improvement.target_file

        if not target_path.exists():
            print(f"目标文件不存在: {target_path}")
            return False

        # 备份原文件
        backup_path = self._backup_file(target_path)
        improvement.backup_path = str(backup_path)

        try:
            # 读取原内容
            with open(target_path, "r", encoding="utf-8") as f:
                content = f.read()

            improvement.old_content = content

            # 应用改进
            if improvement.target_section:
                # 在指定章节后添加
                new_content = self._add_to_section(
                    content,
                    improvement.target_section,
                    improvement.suggestion
                )
            else:
                # 在文件末尾添加
                new_content = content + f"\n\n## 自动改进 ({improvement.id})\n\n{improvement.suggestion}\n"

            improvement.new_content = new_content

            # 写入新内容
            with open(target_path, "w", encoding="utf-8") as f:
                f.write(new_content)

            # 更新状态
            improvement.status = ReflectionStatus.APPLIED
            improvement.applied_at = self.recorder._get_timestamp()

            self.recorder.update_improvement(improvement)

            print(f"✅ 已应用改进到 {improvement.target_file}")
            return True

        except Exception as e:
            print(f"❌ 应用改进失败: {e}")
            # 回滚
            if backup_path:
                self._restore_backup(target_path, backup_path)
            return False

    def _backup_file(self, file_path: Path) -> Path:
        """备份文件"""
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup_name = f"{file_path.name}.{timestamp}.bak"
        backup_path = self.recorder.backups_path / backup_name

        shutil.copy2(file_path, backup_path)
        print(f"📦 已备份到: {backup_path}")

        return backup_path

    def _restore_backup(self, file_path: Path, backup_path: Path):
        """恢复备份"""
        shutil.copy2(backup_path, file_path)
        print(f"🔄 已恢复备份: {backup_path}")

    def _add_to_section(self, content: str, section: str, suggestion: str) -> str:
        """在指定章节后添加内容"""
        lines = content.split("\n")
        result = []
        added = False

        for i, line in enumerate(lines):
            result.append(line)

            # 找到目标章节
            if not added and section.lower() in line.lower() and line.startswith("#"):
                # 在章节后添加内容
                result.append("")
                result.append(f"**自动改进**: {suggestion}")
                added = True

        if not added:
            # 未找到章节，添加到末尾
            result.append("")
            result.append(f"## {section}")
            result.append("")
            result.append(f"**自动改进**: {suggestion}")

        return "\n".join(result)

    def rollback_improvement(self, improvement: Improvement) -> bool:
        """回滚改进"""
        if not improvement.backup_path:
            print("没有备份文件，无法回滚")
            return False

        target_path = self.workspace_path / improvement.target_file
        backup_path = Path(improvement.backup_path)

        if not backup_path.exists():
            print(f"备份文件不存在: {backup_path}")
            return False

        try:
            self._restore_backup(target_path, backup_path)

            # 更新状态
            improvement.status = ReflectionStatus.ROLLED_BACK
            self.recorder.update_improvement(improvement)

            return True
        except Exception as e:
            print(f"回滚失败: {e}")
            return False


# ==================== 主类：记忆反思器 ====================

class MemoryReflector:
    """
    记忆反思器 - 统一接口

    使用示例:
        reflector = MemoryReflector()

        # 记录错误
        error_id = reflector.record_error(
            type="command_failure",
            context="执行向量插入",
            detail="Expected 4096 dimensions but received 1024"
        )

        # 检测模式
        pattern = reflector.detect_pattern(error_id)

        # 如果检测到模式，生成改进
        if pattern:
            improvement = reflector.generate_improvement(error_id, pattern)

            # 应用改进
            if improvement.auto_applicable:
                reflector.apply_improvement(improvement)
            else:
                # 提示用户确认
                print(f"建议改进: {improvement.suggestion}")
    """

    def __init__(self, workspace_path: str = None):
        self.workspace_path = Path(workspace_path or workspace())

        # 初始化组件
        self.recorder = ReflectionRecorder(str(self.workspace_path))
        self.pattern_detector = PatternDetector(self.recorder)
        self.improvement_generator = ImprovementGenerator(self.recorder, str(self.workspace_path))
        self.rule_updater = RuleUpdater(self.recorder, str(self.workspace_path))

    def record_error(
        self,
        type: str,
        context: str,
        detail: str,
        source: str = "command",
        session_key: str = None,
        related_files: List[str] = None
    ) -> str:
        """
        记录错误

        Args:
            type: 错误类型 (command_failure, api_error, user_correction, etc.)
            context: 错误上下文
            detail: 错误详情
            source: 错误来源 (command, user, self)
            session_key: 会话 ID
            related_files: 相关文件列表

        Returns:
            错误 ID
        """
        error = Error(
            id="",  # 将由 recorder 分配
            timestamp="",
            type=ErrorType(type),
            context=context,
            detail=detail,
            source=source,
            session_key=session_key,
            related_files=related_files or []
        )

        error_id = self.recorder.record_error(error)

        # 自动检测模式
        pattern = self.pattern_detector.detect_pattern(error)

        # 如果检测到模式，自动生成改进
        if pattern:
            improvement = self.improvement_generator.analyze_error(error, pattern)
            improvement_id = self.recorder.record_improvement(improvement)

            # 如果可自动应用，立即应用
            if improvement.auto_applicable:
                self.rule_updater.apply_improvement(improvement)
                print(f"🔄 已自动应用改进: {improvement.suggestion}")

        return error_id

    def detect_pattern(self, error_id: str) -> Optional[Pattern]:
        """检测错误模式"""
        # 获取错误
        errors = self.recorder.get_errors()
        error = next((e for e in errors if e.id == error_id), None)

        if not error:
            return None

        return self.pattern_detector.detect_pattern(error)

    def generate_improvement(self, error_id: str, pattern_id: str = None) -> Optional[Improvement]:
        """生成改进建议"""
        # 获取错误
        errors = self.recorder.get_errors()
        error = next((e for e in errors if e.id == error_id), None)

        if not error:
            return None

        # 获取模式
        pattern = None
        if pattern_id:
            patterns = self.recorder.get_patterns()
            pattern = next((p for p in patterns if p.id == pattern_id), None)

        return self.improvement_generator.analyze_error(error, pattern)

    def apply_improvement(self, improvement_id: str = None, improvement: Improvement = None) -> bool:
        """应用改进"""
        if improvement:
            return self.rule_updater.apply_improvement(improvement)

        if improvement_id:
            improvements = self.recorder.get_pending_improvements()
            improvement = next((i for i in improvements if i.id == improvement_id), None)

            if improvement:
                return self.rule_updater.apply_improvement(improvement)

        return False

    def verify_improvement(self, improvement_id: str, test_result: str) -> bool:
        """验证改进效果"""
        improvements = self.recorder.get_pending_improvements()
        improvement = next((i for i in improvements if i.id == improvement_id), None)

        if not improvement:
            return False

        improvement.status = ReflectionStatus.VERIFIED
        improvement.verified_at = self.recorder._get_timestamp()
        improvement.test_result = test_result

        self.recorder.update_improvement(improvement)

        return True

    def rollback_improvement(self, improvement_id: str) -> bool:
        """回滚改进"""
        # 这里需要从所有改进中查找，不只是 pending
        # 简化实现：从文件读取
        improvements = []
        with open(self.recorder.reflections_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    record = json.loads(line)
                    if record["type"] == "improvement":
                        data = record["data"]
                        improvements.append(Improvement(
                            id=data["id"],
                            timestamp=data["timestamp"],
                            priority=Priority(data["priority"]),
                            status=ReflectionStatus(data["status"]),
                            error_id=data["error_id"],
                            pattern_id=data.get("pattern_id"),
                            root_cause=data["root_cause"],
                            affected_areas=data["affected_areas"],
                            suggestion=data["suggestion"],
                            target_file=data["target_file"],
                            target_section=data.get("target_section"),
                            old_content=data.get("old_content"),
                            new_content=data.get("new_content"),
                            auto_applicable=data.get("auto_applicable", False),
                            applied_at=data.get("applied_at"),
                            backup_path=data.get("backup_path"),
                            verified_at=data.get("verified_at"),
                            test_result=data.get("test_result")
                        ))

        improvement = next((i for i in improvements if i.id == improvement_id), None)

        if not improvement:
            return False

        return self.rule_updater.rollback_improvement(improvement)

    def get_reflection_summary(self) -> Dict[str, Any]:
        """获取反思摘要"""
        errors = self.recorder.get_errors(limit=100)
        patterns = self.recorder.get_patterns()
        pending_improvements = self.recorder.get_pending_improvements()

        # 统计错误类型
        error_types = {}
        for error in errors:
            type_name = error.type.value
            error_types[type_name] = error_types.get(type_name, 0) + 1

        return {
            "total_errors": len(errors),
            "error_types": error_types,
            "total_patterns": len(patterns),
            "patterns_above_threshold": len([p for p in patterns if p.occurrence_count >= 3]),
            "pending_improvements": len(pending_improvements),
            "auto_applicable": len([i for i in pending_improvements if i.auto_applicable])
        }


# ==================== CLI 接口 ====================

def main():
    """命令行接口"""
    import argparse

    parser = argparse.ArgumentParser(description="记忆反思模块")
    parser.add_argument("command", choices=["record", "summary", "apply", "rollback", "verify"])
    parser.add_argument("--type", help="错误类型")
    parser.add_argument("--context", help="错误上下文")
    parser.add_argument("--detail", help="错误详情")
    parser.add_argument("--id", help="改进 ID")
    parser.add_argument("--result", help="验证结果")

    args = parser.parse_args()

    reflector = MemoryReflector()

    if args.command == "record":
        if not all([args.type, args.context, args.detail]):
            print("错误: 需要提供 --type, --context, --detail")
            return

        error_id = reflector.record_error(
            type=args.type,
            context=args.context,
            detail=args.detail
        )
        print(f"已记录错误: {error_id}")

    elif args.command == "summary":
        summary = reflector.get_reflection_summary()
        print(json.dumps(summary, indent=2, ensure_ascii=False))

    elif args.command == "apply":
        if not args.id:
            print("错误: 需要提供 --id")
            return

        success = reflector.apply_improvement(improvement_id=args.id)
        print("应用成功" if success else "应用失败")

    elif args.command == "rollback":
        if not args.id:
            print("错误: 需要提供 --id")
            return

        success = reflector.rollback_improvement(args.id)
        print("回滚成功" if success else "回滚失败")

    elif args.command == "verify":
        if not all([args.id, args.result]):
            print("错误: 需要提供 --id 和 --result")
            return

        success = reflector.verify_improvement(args.id, args.result)
        print("验证成功" if success else "验证失败")


if __name__ == "__main__":
    main()
