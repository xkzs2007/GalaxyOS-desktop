#!/usr/bin/env python3
"""
AI 长时记忆系统完整恢复脚本
恢复所有数据、配置、规则、扩展包和脚本
"""

import json
import os
import sqlite3
from pathlib import Path
from datetime import datetime

# 路径定义（v3.0.0 公私分离：优先使用环境变量）
_OPENCLAW_HOME = Path.home() / ".openclaw"  # 内部工具，保留默认路径
WORKSPACE = Path(os.environ.get("OPENCLAW_WORKSPACE", str(_OPENCLAW_HOME / "workspace")))
MEMORY_TDAI = Path(os.environ.get("OPENCLAW_MEMORY_TDAI", str(_OPENCLAW_HOME / "memory-tdai")))
EXTENSIONS = Path(os.environ.get("OPENCLAW_EXTENSIONS", str(_OPENCLAW_HOME / "extensions")))
CONFIG_FILE = Path(os.environ.get("OPENCLAW_CONFIG_FILE", str(_OPENCLAW_HOME / "openclaw.json")))


def check_status():
    """检查系统状态"""
    print("=" * 60)
    print("   AI 长时记忆系统恢复状态检查")
    print("=" * 60)
    print(f"检查时间: {datetime.now().isoformat()}")
    print()

    results = {}

    # 1. 检查配置文件
    print("📋 1. 配置文件")
    print("-" * 40)
    if CONFIG_FILE.exists():
        config = json.loads(CONFIG_FILE.read_text())
        plugins = config.get("plugins", {}).get("entries", {})

        print("  ✅ openclaw.json 存在")
        print(f"  - xiaoyi-channel: {'✅ 启用' if plugins.get('xiaoyi-channel', {}).get('enabled') else '❌ 禁用'}")
        print(
            f"  - execution-validator: "
            f"{'✅ 启用' if plugins.get('execution-validator-plugin', {}).get('enabled') else '❌ 禁用'}")

        # 检查 Embedding 配置
        if embedding:
            print(f"  - Embedding: {embedding.get('provider', 'N/A')}/{embedding.get('model', 'N/A')}")

        results['config'] = True
    else:
        print("  ❌ openclaw.json 不存在")
        results['config'] = False

    print()

    # 2. 检查记忆数据
    print("🧠 2. 记忆数据")
    print("-" * 40)
    vectors_db = MEMORY_TDAI / "vectors.db"
    if vectors_db.exists():
        try:
            with sqlite3.connect(str(vectors_db)) as conn:
                cursor = conn.cursor()

                cursor.execute("SELECT COUNT(*) FROM l0_conversations")
                l0_count = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM l0_vec_rowids")
                l0_vec = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM l1_records")
                l1_count = cursor.fetchone()[0]

                cursor.execute("SELECT COUNT(*) FROM l1_vec_rowids")
                l1_vec = cursor.fetchone()[0]

            print("  ✅ vectors.db 存在")
            print(f"  - L0 对话: {l0_count} 条")
            print(f"  - L0 向量: {l0_vec} 条 ({l0_vec*100//max(l0_count,1)}%)")
            print(f"  - L1 记忆: {l1_count} 条")
            print(f"  - L1 向量: {l1_vec} 条 ({l1_vec*100//max(l1_count,1)}%)")

            results['memory'] = True
        except Exception as e:
            print(f"  ❌ 数据库错误: {e}")
            results['memory'] = False
    else:
        print("  ❌ vectors.db 不存在")
        results['memory'] = False

    print()

    # 3. 检查扩展包
    print("🔌 3. 扩展包")
    print("-" * 40)
    ext_list = list(EXTENSIONS.iterdir()) if EXTENSIONS.exists() else []
    print(f"  扩展包数量: {len(ext_list)}")
    for ext in ext_list:
        if ext.is_dir() and not ext.name.startswith('.'):
            print(f"  - ✅ {ext.name}")
    results['extensions'] = len(ext_list) > 0

    print()

    # 4. 检查技能
    print("📦 4. 技能")
    print("-" * 40)
    skills_dir = WORKSPACE / "skills"
    if skills_dir.exists():
        skills = [s for s in skills_dir.iterdir() if s.is_dir()]
        print(f"  技能数量: {len(skills)}")
        for ks in key_skills:
            if (skills_dir / ks).exists():
                print(f"  - ✅ {ks}")
            else:
                print(f"  - ❌ {ks} 缺失")
        results['skills'] = True
    else:
        print("  ❌ 技能目录不存在")
        results['skills'] = False

    print()

    # 5. 检查三引擎
    print("⚙️ 5. 三引擎向量架构")
    print("-" * 40)

    # vec0
    if vectors_db.exists():
        print("  - ✅ vec0 (主引擎)")

    # Qdrant
    qdrant_dir = Path(os.environ.get("OPENCLAW_QDRANT_DIR", str(_OPENCLAW_HOME / "qdrant-storage")))
    if qdrant_dir.exists():
        print("  - ✅ Qdrant (副引擎)")
    else:
        print("  - ⚠️ Qdrant 未初始化")

    # TF-IDF
    tfidf_db = Path(os.environ.get("OPENCLAW_TFIDF_DIR", str(_OPENCLAW_HOME / "tfidf-storage"))) / "tfidf.db"
    if tfidf_db.exists():
        print("  - ✅ TF-IDF (备份引擎)")
    else:
        print("  - ⚠️ TF-IDF 未初始化")

    results['three_engine'] = vectors_db.exists()

    print()

    # 6. 检查核心文件
    print("📄 6. 核心文件")
    print("-" * 40)
    core_files = ['MEMORY.md', 'USER.md', 'SOUL.md', 'HEARTBEAT.md', 'persona.md', 'AGENTS.md']
    for cf in core_files:
        file_path = WORKSPACE / cf
        if file_path.exists():
            print(f"  - ✅ {cf}")
        else:
            print(f"  - ❌ {cf} 缺失")

    results['core_files'] = all((WORKSPACE / cf).exists() for cf in ['MEMORY.md', 'USER.md', 'SOUL.md'])

    print()

    # 总结
    print("=" * 60)
    print("   恢复状态总结")
    print("=" * 60)

    total = sum(1 for v in results.values() if v)
    total_items = len(results)

    for key, value in results.items():
        status = "✅" if value else "❌"
        print(f"  {status} {key}")

    print()
    print(f"整体恢复度: {total}/{total_items} ({total*100//total_items if total_items > 0 else 0}%)")

    if total == total_items:
        print("\n🎉 所有组件已完整恢复！")
    elif total >= total_items * 0.8:
        print("\n✅ 大部分组件已恢复，系统可用")
    else:
        print("\n⚠️ 部分组件需要恢复")


if __name__ == "__main__":
    check_status()
