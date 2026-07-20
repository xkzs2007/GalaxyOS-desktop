#!/usr/bin/env python3
"""
LLM Memory Integration - 用户画像更新脚本
使用 LLM_GLM5 分析对话历史，更新用户画像

安全修复：
- 添加写入权限检查 (PERSONA_WRITE_ENABLED)
- 添加用户确认提示
- 添加更新前备份
- 添加去重检查
"""

import os
import sys
import shutil
from datetime import datetime
from pathlib import Path


# ── Centralized path resolution ──
import sys as _sys
from galaxyos.shared.paths import galaxyos_home, workspace
_ws_root = workspace()
for _p in [_ws_root, "/workspace"]:
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
PERSONA_WRITE_ENABLED = os.environ.get("LLM_MEMORY_ALLOW_PERSONA_WRITE") == "1"

# 路径配置（v3.0.0 公私分离：优先使用环境变量）
_OPENCLAW_HOME = Path(galaxyos_home())
WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", str(_OPENCLAW_HOME / "workspace")))
MEMORY_DIR = WORKSPACE / "memory"
PERSONA_FILE = MEMORY_DIR / "persona.md"
MEMORY_FILE = WORKSPACE / "MEMORY.md"
SESSION_STATE = WORKSPACE / "SESSION-STATE.md"


def read_file(filepath):
    """读取文件内容"""
    if filepath.exists():
        return filepath.read_text(encoding="utf-8")
    return ""


def write_file(filepath, content):
    """写入文件"""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(content, encoding="utf-8")


def _extract_existing_items(filepath: Path) -> set:
    """提取文件中已有的偏好条目（用于去重）"""
    content = read_file(filepath)
    items = set()
    for line in content.split('\n'):
        line = line.strip().lstrip('- ').lstrip('* ')
        if line and not line.startswith('#'):
            items.add(line.lower())
    return items


def backup_persona():
    """备份 persona.md"""
    if not PERSONA_FILE.exists():
        return
    backup_dir = PERSONA_FILE.parent / ".persona_backups"
    backup_dir.mkdir(exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_path = backup_dir / f"persona_{timestamp}.md"
    shutil.copy2(PERSONA_FILE, backup_path)
    # 清理旧备份（最多保留5个）
    backups = sorted(backup_dir.glob("persona_*.md"))
    while len(backups) > 5:
        backups[0].unlink()
        backups = backups[1:]
    print(f"✅ 已备份 persona.md 到: {backup_path}")


def append_to_persona(preferences):
    """追加用户偏好到 persona.md（带安全检查）"""
    # 检查写入权限
    if not PERSONA_WRITE_ENABLED:
        print("⚠️ 写入已跳过：LLM_MEMORY_ALLOW_PERSONA_WRITE 未设置为 1")
        print("  设置环境变量: export LLM_MEMORY_ALLOW_PERSONA_WRITE=1")
        return

    existing = read_file(PERSONA_FILE)

    # 去重检查
    existing_items = _extract_existing_items(PERSONA_FILE)
    pref_lower = preferences.strip().lower()
    if pref_lower in existing_items:
        print("ℹ️ 偏好已存在，跳过更新")
        return

    # 用户确认
    print("\n⚠️ 即将更新 persona.md，新增内容：")
    print(f"  {preferences[:100]}{'...' if len(preferences) > 100 else ''}")
    print("是否继续？(y/N): ", end="")
    try:
        response = input().strip().lower()
        if response != 'y':
            print("已取消更新")
            return
    except Exception:
        print("无法获取用户输入，跳过更新")
        return

    # 备份
    backup_persona()

    # 添加时间戳
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    # 构建新内容
    new_section = f"""

---

## 更新记录 ({timestamp})

{preferences}
"""

    # 追加到文件末尾
    write_file(PERSONA_FILE, existing + new_section)
    print("✅ 已更新 persona.md")


def update_memory_md(preferences):
    """更新 MEMORY.md 用户画像部分"""
    existing = read_file(MEMORY_FILE)

    # 查找用户画像部分
    if "## 用户画像" in existing:
        # 在用户画像部分后追加
        lines = existing.split("\n")
        new_lines = []
        in_persona = False
        added = False

        for line in lines:
            new_lines.append(line)
            if "## 用户画像" in line:
                in_persona = True
            elif in_persona and line.startswith("## ") and not added:
                # 在下一个章节前插入
                new_lines.insert(-1, f"\n### 更新 {datetime.now().strftime('%Y-%m-%d')}\n{preferences}\n")
                added = True

        if not added:
            new_lines.append(f"\n### 更新 {datetime.now().strftime('%Y-%m-%d')}\n{preferences}\n")

        write_file(MEMORY_FILE, "\n".join(new_lines))
    else:
        # 添加用户画像部分
        persona_section = f"""

## 用户画像

### 更新 {datetime.now().strftime('%Y-%m-%d')}
{preferences}
"""
        write_file(MEMORY_FILE, existing + persona_section)

    print("✅ 已更新 MEMORY.md")


def main():
    """主函数"""
    if len(sys.argv) < 2:
        print("用法: python3 update_persona.py '<偏好内容>'")
        sys.exit(1)

    preferences = sys.argv[1]

    # 更新 persona.md
    append_to_persona(preferences)

    # 更新 MEMORY.md
    update_memory_md(preferences)

    print("用户画像更新完成")


if __name__ == "__main__":
    main()
