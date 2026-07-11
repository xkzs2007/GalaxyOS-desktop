#!/usr/bin/env python3
"""
Smart Memory Update - 智能记忆更新
使用 LLM_GLM5 分析对话并自动更新记忆系统

改进：
- 正确检查 PERSONA_WRITE_ENABLED 写入权限
- 偏好去重
- 错误处理增强
"""

from llm_client import GLM5Client
import os
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional, Set


# ── Centralized path resolution ──
import os as _os
import sys as _sys
from galaxyos.shared.paths import galaxyos_home, workspace
_ws_root = workspace()
for _p in [_ws_root, "/workspace"]:
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
import path_resolver
PERSONA_WRITE_ENABLED = os.environ.get("LLM_MEMORY_ALLOW_PERSONA_WRITE") == "1"

# 添加脚本目录到路径
SCRIPT_DIR = Path(__file__).parent
sys.path.insert(0, str(SCRIPT_DIR))


# 路径配置（v3.0.0 公私分离：优先使用环境变量）
_OPENCLAW_HOME = Path(galaxyos_home())
WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", str(_OPENCLAW_HOME / "workspace")))
MEMORY_DIR = WORKSPACE / "memory"
PERSONA_FILE = MEMORY_DIR / "persona.md"
MEMORY_FILE = WORKSPACE / "MEMORY.md"


def _get_daily_note() -> Path:
    """获取当日记录路径（每次调用时计算，避免导入时固定日期）"""
    return MEMORY_DIR / f"{datetime.now().strftime('%Y-%m-%d')}.md"


def read_file(filepath) -> str:
    """读取文件内容"""
    if filepath.exists():
        return filepath.read_text(encoding="utf-8")
    return ""


def write_file(filepath, content):
    """写入文件"""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content, encoding="utf-8")


def append_to_file(filepath, content):
    """追加到文件"""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    with open(filepath, "a", encoding="utf-8") as f:
        f.write(content)


def _extract_existing_items(filepath: Path) -> Set[str]:
    """提取文件中已有的偏好条目（用于去重）"""
    content = read_file(filepath)
    items = set()
    for line in content.split('\n'):
        line = line.strip().lstrip('- ').lstrip('* ')
        if line and not line.startswith('#'):
            items.add(line.lower())
    return items


def _deduplicate_preferences(preferences: list, existing: Set[str]) -> list:
    """去除已有偏好中的重复项"""
    unique = []
    for p in preferences:
        if p and p.lower() not in existing:
            unique.append(p)
            existing.add(p.lower())
    return unique


# 矛盾关键词对
_CONTRADICTION_PAIRS = [
    ("简洁", "详细"), ("简单", "复杂"), ("简短", "冗长"),
    ("中文", "英文"), ("英文", "中文"),
    ("喜欢", "不喜欢"), ("不喜欢", "喜欢"),
    ("要", "不要"), ("不要", "要"),
    ("需要", "不需要"), ("不需要", "需要"),
    ("开启", "关闭"), ("关闭", "开启"),
]


def _find_contradictions(new_prefs: list, existing: Set[str]) -> list:
    """检测新偏好中与已有偏好矛盾的项，返回矛盾信息列表"""
    conflicts = []
    for new_item in new_prefs:
        new_lower = new_item.lower()
        for kw_a, kw_b in _CONTRADICTION_PAIRS:
            if kw_a in new_lower:
                for old_item in existing:
                    if kw_b in old_item:
                        conflicts.append({
                            "new": new_item,
                            "old": old_item,
                        })
                        break
    return conflicts


# 主人格保护章节（与 auto_update_persona.py 保持一致）
_PRESERVE_SECTIONS = ["核心原型", "基本信息", "长期偏好"]


def _protect_master_persona(content: str, new_content: str) -> str:
    """确保主人格章节不被新内容覆盖或截断"""
    # 解析现有内容中的保护章节
    protected_sections = {}
    current_section = None
    section_lines = []

    for line in content.split('\n'):
        if line.startswith('## ') or line.startswith('### '):
            if current_section and current_section in _PRESERVE_SECTIONS:
                protected_sections[current_section] = '\n'.join(section_lines)
            section_title = line.lstrip('#').strip()
            current_section = section_title
            section_lines = [line]
        elif current_section:
            section_lines.append(line)
    # 处理最后一个章节
    if current_section and current_section in _PRESERVE_SECTIONS:
        protected_sections[current_section] = '\n'.join(section_lines)

    if not protected_sections:
        return new_content

    # 检查新内容是否保留了这些章节
    for section_name, section_content in protected_sections.items():
        # 如果保护章节在新内容中不存在，将其重新插入
        section_header = None
        for line in section_content.split('\n'):
            if line.startswith('## ') or line.startswith('### '):
                section_header = line
                break
        if section_header and section_header.strip() not in new_content:
            # 保护章节被删除了，在文件末尾重新添加
            new_content += f"\n\n{section_content}"

    return new_content


def update_persona_with_llm(conversation: str) -> bool:
    """使用 LLM 分析对话并更新用户画像"""
    # 检查写入权限
    if not PERSONA_WRITE_ENABLED:
        print("⚠️ 写入已跳过：LLM_MEMORY_ALLOW_PERSONA_WRITE 未设置为 1")
        print("  设置环境变量: export LLM_MEMORY_ALLOW_PERSONA_WRITE=1")
        return False

    client = GLM5Client()

    print("🔍 正在分析对话...")
    result = client.analyze_conversation(conversation, "extract_preferences")

    if "error" in result:
        print(f"❌ 分析失败: {result['error']}")
        if "raw_response" in result:
            print(f"原始响应: {result['raw_response']}")
        return False

    # 提取结果
    preferences = result.get("preferences", [])
    habits = result.get("habits", [])
    characteristics = result.get("characteristics", [])
    summary = result.get("summary", "")

    # 去重
    existing = _extract_existing_items(PERSONA_FILE)
    preferences = _deduplicate_preferences(preferences, existing)
    habits = _deduplicate_preferences(habits, existing)
    characteristics = _deduplicate_preferences(characteristics, existing)

    # 矛盾检测
    all_new = preferences + habits + characteristics
    conflicts = _find_contradictions(all_new, existing)
    if conflicts:
        print(f"\n⚠️ 检测到 {len(conflicts)} 条矛盾偏好：")
        for c in conflicts:
            print(f"  新偏好: {c['new'][:60]}...")
            print(f"  旧偏好: {c['old'][:60]}...")
        print("  → 矛盾偏好将覆盖旧偏好")

    if not preferences and not habits and not characteristics:
        print("ℹ️ 未发现新的偏好信息")
        return True

    print("\n📊 分析结果:")
    print(f"  新偏好: {', '.join(preferences) if preferences else '无'}")
    print(f"  新习惯: {', '.join(habits) if habits else '无'}")
    print(f"  新特征: {', '.join(characteristics) if characteristics else '无'}")
    print(f"  总结: {summary}")

    # 更新 persona.md
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 矛盾标记
    conflict_suffix = f" (覆盖 {len(conflicts)} 条旧偏好)" if conflicts else ""

    persona_update = f"""

---

## LLM 分析更新 ({timestamp}){conflict_suffix}

### 偏好
{chr(10).join(f'- {p}' for p in preferences)}

### 习惯
{chr(10).join(f'- {h}' for h in habits)}

### 特征
{chr(10).join(f'- {c}' for c in characteristics)}

### 总结
{summary}
"""

    # 备份现有 persona
    old_persona = read_file(PERSONA_FILE)
    if old_persona:
        backup_dir = PERSONA_FILE.parent / ".persona_backups"
        backup_dir.mkdir(exist_ok=True)
        backup_path = backup_dir / f"persona_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
        import shutil
        shutil.copy2(PERSONA_FILE, backup_path)

    try:
        append_to_file(PERSONA_FILE, persona_update)

        # 主人格保护：确保保护章节未被破坏
        updated_content = read_file(PERSONA_FILE)
        protected_content = _protect_master_persona(old_persona, updated_content)
        if protected_content != updated_content:
            write_file(PERSONA_FILE, protected_content)
            print("⚠️ 已恢复被意外覆盖的主人格章节")

        # 移除矛盾旧偏好
        if conflicts:
            lines = updated_content.split('\n')
            remaining = []
            for line in lines:
                keep = True
                for c in conflicts:
                    if c['old'].lower() in line.lower():
                        keep = False
                        break
                if keep:
                    remaining.append(line)
            write_file(PERSONA_FILE, '\n'.join(remaining))

        print("\n✅ 已更新 persona.md")
    except Exception as e:
        print(f"❌ 写入 persona.md 失败: {e}")
        return False

    # 更新 MEMORY.md
    memory_update = f"""

### 更新 {datetime.now().strftime('%Y-%m-%d')} (LLM 分析)
- **偏好**: {', '.join(preferences)}
- **习惯**: {', '.join(habits)}
- **特征**: {', '.join(characteristics)}
- **总结**: {summary}
"""

    try:
        existing = read_file(MEMORY_FILE)
        if "## 用户画像" in existing:
            append_to_file(MEMORY_FILE, memory_update)
            print("✅ 已更新 MEMORY.md")
        else:
            persona_section = f"""

## 用户画像
{memory_update}
"""
            append_to_file(MEMORY_FILE, persona_section)
            print("✅ 已创建 MEMORY.md 用户画像部分")
    except Exception as e:
        print(f"❌ 写入 MEMORY.md 失败: {e}")
        return False

    return True


def extract_scene_with_llm(conversation: str) -> bool:
    """使用 LLM 提取场景"""
    client = GLM5Client()

    print("🔍 正在提取场景...")
    result = client.analyze_conversation(conversation, "extract_scene")

    if "error" in result:
        print(f"❌ 提取失败: {result['error']}")
        return False

    scene_name = result.get("scene_name", "未命名场景")
    scene_type = result.get("scene_type", "其他")
    key_points = result.get("key_points", [])
    outcome = result.get("outcome", "")

    print("\n📊 场景信息:")
    print(f"  名称: {scene_name}")
    print(f"  类型: {scene_type}")
    print(f"  要点: {', '.join(key_points)}")
    print(f"  结果: {outcome}")

    # 更新每日记录
    daily_note = _get_daily_note()
    timestamp = datetime.now().strftime("%H:%M:%S")
    scene_block = f"""

---

## 场景: {scene_name}
**时间**: {timestamp}
**类型**: {scene_type}

### 要点
{chr(10).join(f'- {p}' for p in key_points)}

### 结果
{outcome}
"""

    try:
        if not daily_note.exists():
            today = datetime.now().strftime("%Y-%m-%d")
            header = f"# {today} 每日记录\n\n> 自动记录的场景和事件\n"
            write_file(daily_note, header)

        append_to_file(daily_note, scene_block)
        print(f"\n✅ 已记录场景到 {daily_note.name}")
    except Exception as e:
        print(f"❌ 写入每日记录失败: {e}")
        return False

    return True


def summarize_with_llm(conversation: str) -> Optional[dict]:
    """使用 LLM 总结对话"""
    client = GLM5Client()

    print("🔍 正在总结对话...")
    result = client.analyze_conversation(conversation, "summarize")

    if "error" in result:
        print(f"❌ 总结失败: {result['error']}")
        return None

    summary = result.get("summary", "")
    key_topics = result.get("key_topics", [])
    decisions = result.get("decisions", [])
    action_items = result.get("action_items", [])

    print("\n📊 总结:")
    print(f"  概要: {summary}")
    print(f"  主题: {', '.join(key_topics)}")
    print(f"  决策: {', '.join(decisions)}")
    print(f"  待办: {', '.join(action_items)}")

    return result


def main():
    """主函数"""
    if len(sys.argv) < 3:
        print("用法:")
        print("  python3 smart_memory_update.py persona '<对话内容>'")
        print("  python3 smart_memory_update.py scene '<对话内容>'")
        print("  python3 smart_memory_update.py summarize '<对话内容>'")
        sys.exit(1)

    command = sys.argv[1]
    conversation = sys.argv[2]

    if command == "persona":
        update_persona_with_llm(conversation)
    elif command == "scene":
        extract_scene_with_llm(conversation)
    elif command == "summarize":
        result = summarize_with_llm(conversation)
        if result:
            print("\n📝 完整结果:")
            print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"未知命令: {command}")
        sys.exit(1)


if __name__ == "__main__":
    main()
