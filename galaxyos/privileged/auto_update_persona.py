#!/usr/bin/env python3
from typing import Dict, List, Optional
from datetime import datetime
from pathlib import Path
import sqlite3
import re
import json
import os
import logging

# ── Centralized path resolution ──
import sys as _sys
from galaxyos.shared.paths import galaxyos_home, workspace
_ws_root = workspace()
for _p in [_ws_root, "/workspace"]:
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
logger = logging.getLogger(__name__)
PERSONA_WRITE_ENABLED = os.environ.get("LLM_MEMORY_ALLOW_PERSONA_WRITE") == "1"

"""
用户画像自动更新 - 基于记忆分析自动更新 persona.md（安全修复版 + LLM增强）

安全修复：
- 移除 shell=False，使用 sqlite3 直接连接
- 使用参数化查询防止 SQL 注入
- 使用环境变量配置路径（v3.0.0 公私分离修复）

LLM 增强：
- 支持 LLM 辅助偏好提取
- LLM 语义摘要压缩 persona
- 偏好去重与合并
"""


# 配置路径（使用环境变量，支持公私分离）
CONFIG_DIR = Path(__file__).parent.parent / "config"

# 统一路径：优先使用环境变量，回退到 ~/.openclaw 默认路径
_OPENCLAW_HOME = Path(galaxyos_home())
PERSONA_FILE = Path(os.environ.get("OPENCLAW_PERSONA_FILE", str(_OPENCLAW_HOME / "workspace" / "memory" / "persona.md")))
MEMORY_FILE = Path(os.environ.get("OPENCLAW_MEMORY_FILE", str(_OPENCLAW_HOME / "workspace" / "MEMORY.md")))
VECTORS_DB = Path(os.environ.get("OPENCLAW_VECTORS_DB", str(_OPENCLAW_HOME / "memory-tdai" / "vectors.db")))
CONFIG_FILE = CONFIG_DIR / "persona_update.json"
LOG_FILE = Path(os.environ.get("OPENCLAW_LOG_DIR", str(_OPENCLAW_HOME / "memory-tdai" / ".metadata"))) / "persona_update.log"

# 默认配置
DEFAULT_CONFIG = {
    "update_interval": 86400,        # 更新间隔（秒）
    "min_memories_for_update": 5,    # 最少记忆数量才触发更新
    "max_persona_length": 2000,      # persona.md 最大长度
    "preserve_sections": [           # 保留的章节
        "核心原型",
        "基本信息",
        "长期偏好"
    ],
    "auto_update": False,            # 🔒 安全修复：默认禁用自动更新
    "require_confirmation": True,    # 🔒 更新前需要用户确认
    "llm_assisted": True,            # 是否使用 LLM 辅助
    "backup_before_update": True,    # 🔒 更新前备份 persona.md
    "max_backups": 5,                # 最多保留备份数
    "_comment": "⚠️ auto_update 默认禁用，需用户手动触发"
}


def _get_llm_client():
    """获取 LLM 客户端（延迟导入）"""
    try:
        import sys
        sys.path.insert(0, str(Path(__file__).parent))
        from llm_client import LLMClient
        client = LLMClient()
        if client.api_key:
            return client
    except Exception:
        pass
    return None


class PersonaAutoUpdater:
    def __init__(self):
        self.persona_file = PERSONA_FILE
        self.memory_file = MEMORY_FILE
        self.db_path = VECTORS_DB
        self.log_file = LOG_FILE
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        self.config = self._load_config()
        self.persona_content = self._load_persona()
        self._llm_client = None  # 延迟初始化

    @property
    def llm_client(self):
        """延迟获取 LLM 客户端"""
        if self._llm_client is None and self.config.get("llm_assisted", True):
            self._llm_client = _get_llm_client()
        return self._llm_client

    def _load_config(self) -> Dict:
        if CONFIG_FILE.exists():
            try:
                return json.loads(CONFIG_FILE.read_text())
            except Exception as e:
                # Bug 修复: logger.error 应在 except 块内
                logger.error(f"配置文件加载失败: {e}")
        return DEFAULT_CONFIG

    def _load_persona(self) -> str:
        if self.persona_file.exists():
            return self.persona_file.read_text()
        return ""

    def _save_persona(self, content: str):
        # 压缩到最大长度
        if len(content) > self.config["max_persona_length"]:
            content = self._compress_persona(content)

        if not PERSONA_WRITE_ENABLED:
            self.log("⚠️ 写入已跳过：LLM_MEMORY_ALLOW_PERSONA_WRITE 未设置为 1")
            return

        self.persona_file.write_text(content)
        self.persona_content = content

    def _compress_persona(self, content: str) -> str:
        """
        压缩 persona 到目标长度

        优先使用 LLM 语义摘要压缩，不可用时回退到行级截断。
        """
        # 尝试 LLM 语义压缩
        if self.llm_client is not None:
            try:
                compressed = self._compress_with_llm(content)
                if compressed and len(compressed) <= self.config["max_persona_length"]:
                    return compressed
            except Exception as e:
                logger.warning(f"LLM 压缩失败，回退到截断: {e}")

        # 回退：行级截断
        return self._compress_by_truncation(content)

    def _compress_with_llm(self, content: str) -> Optional[str]:
        """使用 LLM 语义摘要压缩 persona"""
        max_len = self.config["max_persona_length"]
        preserve_sections = self.config.get("preserve_sections", [])
        preserve_hint = ""
        if preserve_sections:
            preserve_hint = (
                f"- 【最重要】以下章节是用户核心人格，必须完整保留，不可删减或改写：\n"
                f"  {', '.join(preserve_sections)}\n"
            )
        prompt = (
            f"以下是一个用户画像文档，请压缩为更简洁的版本，保留核心信息：\n\n"
            f"{content}\n\n"
            f"要求：\n"
            f"{preserve_hint}"
            f"- 保留所有关键偏好和规则\n"
            f"- 合并重复内容\n"
            f"- 如果新旧偏好矛盾，以较新的偏好为准，删除旧矛盾项\n"
            f"- 控制在 {max_len // 2} 字以内\n"
            f"- 使用 Markdown 格式"
        )

        response = self.llm_client.chat(
            [{"role": "user", "content": prompt}],
            max_tokens=max_len,
            temperature=0.3,
        )
        return response.strip() if response else None

    def _compress_by_truncation(self, content: str) -> str:
        """行级截断压缩（保护主人格章节）"""
        lines = content.split('\n')
        preserve_sections = self.config.get("preserve_sections", [])
        max_len = self.config["max_persona_length"]

        # 解析章节结构，标记需要保护的行
        protected_lines = set()
        current_section = ""
        for i, line in enumerate(lines):
            if line.startswith('## ') or line.startswith('### '):
                section_title = line.lstrip('#').strip()
                current_section = section_title
            if current_section in preserve_sections:
                protected_lines.add(i)

        # 用集合跟踪已加入的行索引
        added_indices = set()
        compressed = []
        current_length = 0

        def _try_add(idx, line):
            nonlocal current_length
            if idx in added_indices:
                return
            if current_length + len(line) + 1 <= max_len:
                compressed.append(line)
                added_indices.add(idx)
                current_length += len(line) + 1

        # 第一优先级：保护章节内的所有行
        for i, line in enumerate(lines):
            if i in protected_lines:
                _try_add(i, line)

        # 第二优先级：标题行和加粗行（非保护章节）
        for i, line in enumerate(lines):
            if i in protected_lines:
                continue
            if line.startswith('#') or line.startswith('- **'):
                _try_add(i, line)

        # 第三优先级：普通行（非保护章节）
        for i, line in enumerate(lines):
            if i in protected_lines:
                continue
            _try_add(i, line)

        return '\n'.join(compressed) + "\n\n... (已压缩)"

    def log(self, message: str):
        """记录日志"""
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        try:
            with open(self.log_file, "a") as f:
                f.write(f"[{timestamp}] {message}\n")
        except Exception:
            pass

    def get_db_connection(self) -> sqlite3.Connection:
        """获取数据库连接"""
        return sqlite3.connect(str(self.db_path))

    def extract_preferences(self, memories: List[Dict]) -> Dict:
        """
        从记忆中提取偏好

        当 llm_assisted=True 且 LLM 可用时，使用 LLM 提取；
        否则回退到关键词匹配。
        """
        if self.llm_client is not None and self.config.get("llm_assisted", True):
            try:
                llm_prefs = self._extract_preferences_with_llm(memories)
                if llm_prefs:
                    return llm_prefs
            except Exception as e:
                logger.warning(f"LLM 偏好提取失败，回退到关键词: {e}")

        return self._extract_preferences_with_keywords(memories)

    def _extract_preferences_with_llm(self, memories: List[Dict]) -> Optional[Dict]:
        """使用 LLM 提取偏好"""
        memories_text = "\n".join(
            f"- [{m.get('type', '未知')}] {m.get('content', '')[:200]}"
            for m in memories[:20]
        )

        prompt = (
            f"请从以下用户记忆中提取偏好信息：\n\n"
            f"{memories_text}\n\n"
            f"以 JSON 返回：{{\"communication_style\": [...], "
            "\"work_style\": [...], \"technical_preferences\": [...], \"rules\": [...]}}\n"
            f"只返回 JSON。")

        response = self.llm_client.chat(
            [{"role": "user", "content": prompt}],
            max_tokens=800,
            temperature=0.3,
        )

        if response:
            try:
                clean = response.strip()
                if clean.startswith("```json"):
                    clean = clean[7:]
                if clean.startswith("```"):
                    clean = clean[3:]
                if clean.endswith("```"):
                    clean = clean[:-3]
                return json.loads(clean.strip())
            except (json.JSONDecodeError, ValueError):
                pass

        return None

    def _extract_preferences_with_keywords(self, memories: List[Dict]) -> Dict:
        """关键词匹配提取偏好"""
        preferences = {
            "communication_style": [],
            "work_style": [],
            "technical_preferences": [],
            "rules": []
        }

        for memory in memories:
            content = memory.get("content", "")
            memory_type = memory.get("type", "")

            if any(kw in content for kw in ["回复", "简洁", "详细", "风格"]):
                preferences["communication_style"].append(content[:100])

            if any(kw in content for kw in ["工作", "效率", "时间", "习惯"]):
                preferences["work_style"].append(content[:100])

            if any(kw in content for kw in ["配置", "设置", "技术", "工具"]):
                preferences["technical_preferences"].append(content[:100])

            if memory_type == "instruction" or any(kw in content for kw in ["必须", "不要", "以后"]):
                preferences["rules"].append(content[:100])

        return preferences

    def detect_changes(self, new_preferences: Dict) -> List[Dict]:
        """
        检测新偏好变化

        支持去重：与 persona.md 中已有内容比较，避免重复添加。
        支持矛盾检测：新偏好与已有偏好语义矛盾时标记为覆盖而非追加。
        """
        changes = []

        # 提取现有偏好条目（用于去重）
        existing_items = set()
        for line in self.persona_content.split('\n'):
            line = line.strip().lstrip('- ').lstrip('* ')
            if line and not line.startswith('#'):
                existing_items.add(line.lower())

        # 矛盾关键词对（如果新偏好包含关键词A而旧偏好包含关键词B，则视为矛盾）
        contradiction_pairs = [
            ("简洁", "详细"), ("简单", "复杂"), ("简短", "冗长"),
            ("中文", "英文"), ("英文", "中文"),
            ("喜欢", "不喜欢"), ("不喜欢", "喜欢"),
            ("要", "不要"), ("不要", "要"),
            ("需要", "不需要"), ("不需要", "需要"),
            ("开启", "关闭"), ("关闭", "开启"),
        ]

        def _find_contradiction(new_item: str, existing_set: set) -> Optional[str]:
            """检测新偏好是否与已有偏好矛盾"""
            new_lower = new_item.lower()
            for kw_a, kw_b in contradiction_pairs:
                if kw_a in new_lower:
                    for existing in existing_set:
                        if kw_b in existing:
                            return existing
            return None

        for category, items in new_preferences.items():
            for item in items:
                if not item:
                    continue
                # 去重检查
                if item.lower() not in existing_items and item not in self.persona_content:
                    # 矛盾检查
                    contradicted = _find_contradiction(item, existing_items)
                    change = {
                        "category": category,
                        "content": item,
                        "timestamp": datetime.now().isoformat()
                    }
                    if contradicted:
                        change["replaces"] = contradicted
                        change["conflict_note"] = (
                            f"⚠️ 此偏好与已有偏好矛盾，建议覆盖旧偏好：{contradicted}"
                        )
                    changes.append(change)
                    # 添加到已存在集合，避免同一批次内重复
                    existing_items.add(item.lower())

        return changes

    def backup_persona(self):
        """备份 persona.md"""
        if not self.persona_file.exists():
            return None

        backup_dir = self.persona_file.parent / ".persona_backups"
        backup_dir.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = backup_dir / f"persona_{timestamp}.md"

        import shutil
        shutil.copy2(self.persona_file, backup_path)

        # 清理旧备份
        backups = sorted(backup_dir.glob("persona_*.md"))
        while len(backups) > self.config.get("max_backups", 5):
            backups[0].unlink()
            backups = backups[1:]

        self.log(f"✅ 已备份 persona.md 到: {backup_path}")
        return backup_path

    def update_persona(self, changes: List[Dict]):
        """更新 persona.md（带确认、备份和矛盾处理）"""
        if not changes:
            self.log("无新变化，跳过更新")
            return

        # 检查是否有矛盾项
        conflict_changes = [c for c in changes if "replaces" in c]
        non_conflict_changes = [c for c in changes if "replaces" not in c]

        # ⚠️ 检查是否需要用户确认
        if self.config.get("require_confirmation", True):
            print("\n" + "=" * 60)
            print("⚠️ 即将更新 persona.md")
            print("=" * 60)
            if non_conflict_changes:
                print(f"\n📋 新增偏好 ({len(non_conflict_changes)} 条)：\n")
                for i, change in enumerate(non_conflict_changes[:5], 1):
                    print(f"  {i}. [{change['category']}] {change['content'][:60]}...")
                if len(non_conflict_changes) > 5:
                    print(f"  ... 还有 {len(non_conflict_changes) - 5} 条")
            if conflict_changes:
                print(f"\n⚠️ 矛盾偏好 ({len(conflict_changes)} 条，将覆盖旧偏好)：\n")
                for i, change in enumerate(conflict_changes[:5], 1):
                    print(f"  {i}. [{change['category']}] {change['content'][:60]}...")
                    print(f"     替代旧偏好: {change['replaces'][:60]}...")
                if len(conflict_changes) > 5:
                    print(f"  ... 还有 {len(conflict_changes) - 5} 条")
            print("\n是否继续更新？(y/N): ", end="")

            try:
                response = input().strip().lower()
                if response != 'y':
                    self.log("❌ 用户取消更新")
                    print("已取消更新")
                    return
            except Exception:
                self.log("❌ 无法获取用户输入，跳过更新")
                return

        # ⚠️ 备份
        if self.config.get("backup_before_update", True):
            self.backup_persona()

        self.log(f"📝 检测到 {len(changes)} 条新偏好（其中 {len(conflict_changes)} 条覆盖旧偏好）")

        # 处理矛盾：从 persona 中移除被替代的旧偏好
        new_content = self.persona_content
        for change in conflict_changes:
            old_pref = change["replaces"]
            # 按行移除包含旧偏好的行
            lines = new_content.split('\n')
            new_lines = [
                line for line in lines
                if old_pref.lower() not in line.lower()
            ]
            new_content = '\n'.join(new_lines)

        # 构建更新内容
        update_section = f"\n### 更新 {datetime.now().strftime('%Y-%m-%d %H:%M')}\n"

        for change in changes:
            category = change["category"]
            content = change["content"]
            suffix = " (覆盖旧偏好)" if "replaces" in change else ""
            update_section += f"- **{category}**: {content[:80]}{suffix}\n"

        # 追加到 persona
        if "### 更新" in new_content:
            parts = new_content.split("### 更新", 1)
            new_content = parts[0] + update_section + "### 更新" + parts[1]
        else:
            new_content += "\n" + update_section

        self._save_persona(new_content)
        self.log(f"✅ persona.md 已更新 ({len(changes)} 条新偏好, {len(conflict_changes)} 条覆盖)")
        print(f"✅ persona.md 已更新 ({len(changes)} 条新偏好, {len(conflict_changes)} 条覆盖)")

    def run_update_cycle(self):
        """执行更新周期"""
        # 🔒 安全修复：检查是否启用自动更新
        if not self.config.get("auto_update", False):
            self.log("⚠️ 自动更新已禁用，请手动运行: auto_update_persona.py run")
            print("⚠️ 自动更新已禁用，请手动运行: python3 auto_update_persona.py run")
            return

        self.log("🔄 开始用户画像更新周期")

        try:
            conn = self.get_db_connection()
            try:
                cursor = conn.cursor()

                cursor.execute("""
                    SELECT content, type, scene_name, created_at
                    FROM l1_records
                    WHERE type = 'instruction'
                    ORDER BY created_at DESC
                    LIMIT 20
                """)

                memories = []
                for row in cursor.fetchall():
                    if len(row) >= 4:
                        memories.append({
                            "content": row[0] or "",
                            "type": row[1] or "",
                            "scene": row[2] or "",
                            "timestamp": row[3] or ""
                        })
            finally:
                conn.close()

            if len(memories) < self.config["min_memories_for_update"]:
                self.log(f"记忆数量不足 ({len(memories)} < {self.config['min_memories_for_update']})，跳过更新")
                return

            # 2. 提取偏好
            preferences = self.extract_preferences(memories)

            # 3. 检测变化
            changes = self.detect_changes(preferences)

            # 4. 更新 persona（带确认和备份）
            self.update_persona(changes)

        except Exception as e:
            self.log(f"❌ 更新失败: {e}")

    def show_status(self):
        """显示状态"""
        print("=" * 60)
        print("用户画像自动更新状态")
        print("=" * 60)
        print(f"persona.md: {self.persona_file}")
        print(f"当前长度: {len(self.persona_content)} 字符")
        print(f"最大长度: {self.config['max_persona_length']} 字符")
        print(f"自动更新: {'✅ 启用' if self.config.get('auto_update', False) else '❌ 禁用（默认）'}")
        print(f"需要确认: {'✅ 是' if self.config.get('require_confirmation', True) else '❌ 否'}")
        print(f"更新前备份: {'✅ 是' if self.config.get('backup_before_update', True) else '❌ 否'}")
        print(
            f"LLM 辅助: "
            f"{'✅ 启用（可用）' if self.llm_client else '⚠️ 启用但不可用' if self.config.get('llm_assisted') else '❌ 禁用'}")
        print(f"写入权限: {'✅ 允许' if PERSONA_WRITE_ENABLED else '❌ 未设置 LLM_MEMORY_ALLOW_PERSONA_WRITE=1'}")
        print(f"更新间隔: {self.config.get('update_interval', 86400)} 秒")

        if "### 更新" in self.persona_content:
            updates = re.findall(r'### 更新 (\d{4}-\d{2}-\d{2} \d{2}:\d{2})', self.persona_content)
            if updates:
                print(f"\n最近更新: {updates[-1]}")

        print("\n⚠️ 安全提示:")
        print("  - 自动更新默认禁用，需手动启用")
        print("  - 更新前会备份 persona.md")
        print("  - 更新时会请求用户确认")
        print("  - 设置 LLM_MEMORY_ALLOW_PERSONA_WRITE=1 启用写入")


def main():
    import sys

    updater = PersonaAutoUpdater()

    if len(sys.argv) < 2:
        updater.show_status()
        return

    cmd = sys.argv[1]

    if cmd == "status":
        updater.show_status()
    elif cmd == "run":
        updater.run_update_cycle()
    elif cmd == "show":
        print(updater.persona_content)
    else:
        print(f"未知命令: {cmd}")
        print("用法: auto_update_persona.py [status|run|show]")


if __name__ == "__main__":
    main()
