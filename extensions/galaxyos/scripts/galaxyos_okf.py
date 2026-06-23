#!/usr/bin/env python3
"""
GalaxyOS — OKF (Open Knowledge Format) 集成工具

三层整合：
  export  扫描 workspace 系统文件，导出 OKF Knowledge Bundle
  ingest  读取外部 OKF bundle，索引到 GalaxyOS 知识层
  verify  验证 bundle 结构和 concept 合法性

规范参考：Google Open Knowledge Format v0.1 (2026-06-12)
"""

import argparse
import json
import os
import re
import shutil
import sys
import time
import yaml
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ = timezone(timedelta(hours=8))

# ── OKF 保留文件名 ──
RESERVED_FILES = {"index.md", "log.md", "README.md", ".gitignore"}

# ── 系统文件的 type 映射 ──
FILE_TYPE_MAP = {
    "TOOLS.md": "AgentConfig",
    "AGENTS.md": "AgentGuide",
    "SOUL.md": "AgentPersona",
    "IDENTITY.md": "AgentIdentity",
    "USER.md": "UserProfile",
    "MEMORY.md": "LongTermMemory",
    "EXECUTION_CHAINS.md": "Procedure",
}

# ── 系统文件应该包含的标签 ──
FILE_TAG_MAP = {
    "TOOLS.md": ["system", "config", "tools"],
    "AGENTS.md": ["system", "rules", "agent"],
    "SOUL.md": ["system", "persona", "behavior"],
    "IDENTITY.md": ["system", "identity", "profile"],
    "USER.md": ["system", "user", "profile"],
    "MEMORY.md": ["system", "memory", "longterm"],
    "EXECUTION_CHAINS.md": ["system", "rules", "procedure"],
}

WORKSPACE = os.environ.get("OPENCLAW_WORKSPACE",
                           os.path.expanduser("~/.openclaw/workspace"))
OKF_BUNDLE_DIR = os.path.join(os.path.dirname(__file__), "..", "var", "okf-bundles")
SKILLS_DIR = os.path.join(WORKSPACE, "skills")


# ═══════════════════════════════════════════════════════════
# L1 + L2: OKF Knowledge Bundle 导出器
# ═══════════════════════════════════════════════════════════

def _detect_file_type(filepath):
    """根据文件名和路径推断 OKF type"""
    basename = os.path.basename(filepath)
    if basename in FILE_TYPE_MAP:
        return FILE_TYPE_MAP[basename]
    # Skill SKILL.md
    rel = os.path.relpath(filepath, WORKSPACE)
    if rel.endswith("/SKILL.md"):
        skill_name = os.path.basename(os.path.dirname(filepath))
        return "Skill"
    # 其他 .md 文件
    return "Document"


def _detect_tags(filepath):
    """推断文件标签"""
    basename = os.path.basename(filepath)
    if basename in FILE_TAG_MAP:
        return FILE_TAG_MAP[basename]
    rel = os.path.relpath(filepath, WORKSPACE)
    if rel.endswith("/SKILL.md"):
        skill_name = os.path.basename(os.path.dirname(filepath))
        return ["skill", skill_name]
    return ["document"]


def _make_concept_id(filepath, base_dir):
    """从文件路径生成 OKF concept ID"""
    rel = os.path.relpath(filepath, base_dir)
    # 去掉扩展名，用 / 做 ID
    if rel.endswith(".md"):
        rel = rel[:-3]
    return rel.replace("\\", "/")


def _extract_title(content, filepath):
    """从 Markdown 正文提取标题（容错）"""
    # 先看 YAML frontmatter 里的 title（安全解析）
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
    if m:
        fm = _safe_parse_yaml(m.group(1))
        if fm and "title" in fm:
            return fm["title"]
    # 再看 # 标题
    m = re.search(r'^#\s+(.+)$', content, re.MULTILINE)
    if m:
        return m.group(1).strip()
    # 用文件名
    return os.path.splitext(os.path.basename(filepath))[0]


def _extract_description(content):
    """从 Markdown 正文提取描述（第一段非空文字）"""
    # 跳过 frontmatter
    text = content
    m = re.match(r'^---\s*\n.*?\n---\s*\n', text, re.DOTALL)
    if m:
        text = text[m.end():]
    # 找第一段非空文字
    for line in text.split("\n"):
        line = line.strip()
        if line and not line.startswith("#") and not line.startswith("```"):
            # 截取前 200 字
            return line[:200].rstrip()
    return ""


def _safe_parse_yaml(text):
    """安全解析 YAML，失败返回 None"""
    try:
        result = yaml.safe_load(text)
        if isinstance(result, dict):
            return result
        return None
    except Exception:
        return None


def _extract_frontmatter(content):
    """提取已有的 YAML frontmatter（如果有）
    容错：对非标准 YAML（如直接跟列表项的）静默降级
    """
    m = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
    if m:
        fm = _safe_parse_yaml(m.group(1))
        if fm is not None:
            return fm
        # YAML 解析失败，回退到用首行提取 title
        lines = m.group(1).strip().split('\n')
        result = {}
        for line in lines:
            if ':' in line and not line.startswith('-'):
                key, _, val = line.partition(':')
                result[key.strip()] = val.strip()
        return result
    return {}


def _rewrite_content_with_fm(content, frontmatter):
    """替换或添加 YAML frontmatter"""
    pattern = r'^---\s*\n.*?\n---\s*\n'
    fm_str = "---\n" + yaml.dump(frontmatter, allow_unicode=True,
                                  default_flow_style=False).strip() + "\n---\n"
    if re.match(pattern, content, re.DOTALL):
        return re.sub(pattern, fm_str, content, count=1)
    else:
        return fm_str + content


def export_bundle(bundle_name="galaxyos-system", output_dir=None,
                  include_skills=True, rewrite_source=False, force=False):
    """
    导出系统 Knowledge Bundle

    扫描以下来源：
    1. workspace 根目录的系统 .md 文件
    2. skills/ 下所有 SKILL.md（可选）
    """
    if not output_dir:
        output_dir = os.path.join(OKF_BUNDLE_DIR, bundle_name)

    output_path = Path(output_dir)
    if output_path.exists():
        if not force:
            print(f"⚠️  {output_dir} 已存在，使用 --force 覆盖")
            return False
        shutil.rmtree(output_path)

    # 收集源文件
    sources = []

    # 1. 系统根目录文件
    for fname in FILE_TYPE_MAP:
        fpath = os.path.join(WORKSPACE, fname)
        if os.path.isfile(fpath):
            sources.append(fpath)

    # 2. SKILL.md（可选）
    if include_skills and os.path.isdir(SKILLS_DIR):
        for root, dirs, files in os.walk(SKILLS_DIR):
            # 跳过 __pycache__ 和 node_modules
            dirs[:] = [d for d in dirs if d not in ("__pycache__", "node_modules")]
            if "SKILL.md" in files:
                sources.append(os.path.join(root, "SKILL.md"))

    print(f"\n📦 导出 Knowledge Bundle: {bundle_name}")
    print(f"   扫描到 {len(sources)} 个源文件")

    # 创建目录结构
    concepts_dir = output_path / "concepts"
    concepts_dir.mkdir(parents=True, exist_ok=True)
    system_dir = concepts_dir / "system"
    system_dir.mkdir(exist_ok=True)
    skills_dir_out = concepts_dir / "skills"
    if include_skills:
        skills_dir_out.mkdir(exist_ok=True)

    concepts = []
    errors = []

    for fpath in sources:
        basename = os.path.basename(fpath)
        try:
            with open(fpath, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            errors.append(f"  读取失败: {fpath} ({e})")
            continue

        # 判断 type 和 ID
        concept_type = _detect_file_type(fpath)
        tag_list = _detect_tags(fpath)
        concept_id = _make_concept_id(fpath, WORKSPACE)

        # 提取已有 frontmatter
        existing_fm = _extract_frontmatter(content)

        # 构建 OKF frontmatter
        fm = {
            "type": existing_fm.get("type", concept_type),
            "title": _extract_title(content, fpath),
            "description": _extract_description(content),
            "timestamp": datetime.now(TZ).isoformat(),
            "tags": tag_list,
        }
        if "resource" in existing_fm:
            fm["resource"] = existing_fm["resource"]

        # 写入 concept 文件（去掉源文件的 frontmatter + 用 OKF frontmatter）
        clean_content = content
        m = re.match(r'^---\s*\n.*?\n---\s*\n', clean_content, re.DOTALL)
        if m:
            clean_content = clean_content[m.end():]

        concept_fm_str = "---\n" + yaml.dump(fm, allow_unicode=True,
                                              default_flow_style=False).strip() + "\n---\n"
        concept_content = concept_fm_str + clean_content

        # 确定输出路径
        is_skill = "SKILL.md" in fpath and "skills/" in fpath.replace("\\", "/")
        if is_skill:
            skill_name = os.path.basename(os.path.dirname(fpath))
            out_subdir = skills_dir_out / skill_name
        elif basename in FILE_TYPE_MAP:
            out_subdir = system_dir
        else:
            out_subdir = system_dir

        out_subdir.mkdir(parents=True, exist_ok=True)
        out_file = out_subdir / f"{concept_id.replace('/', '_')}.md"

        with open(out_file, "w", encoding="utf-8") as f:
            f.write(concept_content)

        # 如果启用了改写源文件
        if rewrite_source:
            new_content = _rewrite_content_with_fm(content, fm)
            with open(fpath, "w", encoding="utf-8") as f:
                f.write(new_content)

        concepts.append({
            "id": concept_id,
            "type": fm["type"],
            "title": fm["title"],
            "file": str(out_file),
        })

    # 生成 index.md
    index_lines = [
        "---",
        f"type: BundleIndex",
        f"title: {bundle_name}",
        f"description: GalaxyOS 系统 Knowledge Bundle（自动生成 {datetime.now(TZ).isoformat()}）",
        "tags: [galaxyos, system, okf]",
        "---",
        "",
        f"# {bundle_name}",
        "",
        f"自动生成时间: {datetime.now(TZ).isoformat()}",
        "",
        "## 概念索引",
        "",
    ]
    for c in sorted(concepts, key=lambda x: x["type"]):
        rel_path = os.path.relpath(c["file"], output_path)
        index_lines.append(f"- [{c['title']}]({rel_path}) — `{c['type']}`")

    with open(output_path / "index.md", "w", encoding="utf-8") as f:
        f.write("\n".join(index_lines) + "\n")

    # 生成 log.md
    log_lines = [
        "---",
        "type: Changelog",
        f"title: {bundle_name} 变更日志",
        "---",
        "",
        f"## {datetime.now(TZ).strftime('%Y-%m-%d %H:%M')}",
        f"- 初始导出，共 {len(concepts)} 个 concept",
    ]
    if errors:
        log_lines.append(f"- {len(errors)} 个文件读取失败")
        log_lines.append("")
        log_lines.append("### 失败列表")
        for e in errors:
            log_lines.append(f"- {e}")

    with open(output_path / "log.md", "w", encoding="utf-8") as f:
        f.write("\n".join(log_lines) + "\n")

    print(f"\n✅ Bundle 导出完成: {output_path}")
    print(f"   concepts: {len(concepts)}")
    print(f"   目录结构:")
    _print_tree(output_path)
    return True


def _print_tree(path, prefix=""):
    """打印目录树"""
    entries = sorted(os.listdir(path))
    for i, entry in enumerate(entries):
        full = os.path.join(path, entry)
        is_last = i == len(entries) - 1
        connector = "└── " if is_last else "├── "
        if os.path.isdir(full):
            print(f"{prefix}{connector}{entry}/")
            ext = "    " if is_last else "│   "
            _print_tree(full, prefix + ext)
        else:
            print(f"{prefix}{connector}{entry}")


# ═══════════════════════════════════════════════════════════
# L3: OKF Consumer — 消费外部 Knowledge Bundle
# ═══════════════════════════════════════════════════════════

def _is_okf_concept(path):
    """判断文件是否为 OKF concept（.md 且含 frontmatter + type）"""
    if not path.endswith(".md"):
        return False
    basename = os.path.basename(path)
    if basename in RESERVED_FILES:
        return False
    try:
        with open(path, "r", encoding="utf-8") as f:
            head = f.read(4096)
        m = re.match(r'^---\s*\n(.*?)\n---\s*\n', head, re.DOTALL)
        if not m:
            return False
        fm = yaml.safe_load(m.group(1))
        return isinstance(fm, dict) and "type" in fm
    except:
        return False


def _parse_concept(path):
    """解析一个 OKF concept 文件"""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    m = re.match(r'^---\s*\n(.*?)\n---\s*\n', content, re.DOTALL)
    fm = yaml.safe_load(m.group(1)) if m else {}
    body = content[m.end():] if m else content

    # 生成 concept ID（相对于 bundle 根）
    bundle_root = _find_bundle_root(path)
    if bundle_root:
        concept_id = os.path.relpath(path, bundle_root)
        if concept_id.endswith(".md"):
            concept_id = concept_id[:-3]
    else:
        concept_id = os.path.basename(path)

    return {
        "id": concept_id,
        "type": fm.get("type", "Unknown"),
        "title": fm.get("title", os.path.splitext(os.path.basename(path))[0]),
        "description": fm.get("description", ""),
        "tags": fm.get("tags", []),
        "resource": fm.get("resource", ""),
        "timestamp": fm.get("timestamp", ""),
        "body": body,
        "source": path,
    }


def _find_bundle_root(path):
    """从文件路径向上找 Bundle 根目录（有 index.md 的）"""
    current = os.path.dirname(os.path.abspath(path))
    for _ in range(10):  # 最多上溯 10 层
        if os.path.isfile(os.path.join(current, "index.md")):
            return current
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent
    return None


def ingest_bundle(bundle_path, index_dir=None, register_module=True):
    """
    消费一个 OKF Knowledge Bundle

    1. 遍历所有 concept 文件
    2. 解析 frontmatter + body
    3. 写入索引（JSON + 文本）
    4. 可选注册到 GalaxyOS 知识层
    """
    bundle_path = os.path.abspath(bundle_path)
    if not os.path.isdir(bundle_path):
        print(f"❌ 目录不存在: {bundle_path}")
        return False

    if not os.path.isfile(os.path.join(bundle_path, "index.md")):
        print(f"⚠️  不是有效的 OKF Bundle（缺少 index.md）: {bundle_path}")
        return False

    print(f"\n📖 消费 OKF Bundle: {bundle_path}")

    # 扫描 concept 文件
    concept_files = []
    for root, dirs, files in os.walk(bundle_path):
        # 跳过 __pycache__ 等
        dirs[:] = [d for d in dirs if not d.startswith(".") and d != "__pycache__"]
        for f in files:
            full_path = os.path.join(root, f)
            if _is_okf_concept(full_path):
                concept_files.append(full_path)

    print(f"   发现 {len(concept_files)} 个 concept 文件")

    # 解析
    concepts = []
    for cf in concept_files:
        try:
            c = _parse_concept(cf)
            concepts.append(c)
        except Exception as e:
            print(f"   ⚠️  解析失败: {cf} ({e})")

    # 按 type 分组
    by_type = {}
    for c in concepts:
        by_type.setdefault(c["type"], []).append(c)

    print(f"\n   类型分布:")
    for t, clist in sorted(by_type.items(), key=lambda x: -len(x[1])):
        print(f"     {t}: {len(clist)}")

    # 写入索引
    if not index_dir:
        index_dir = os.path.join(os.path.dirname(__file__), "..", "var", "okf-index")

    os.makedirs(index_dir, exist_ok=True)

    # 写入完整索引（JSON）
    bundle_name = os.path.basename(bundle_path)
    index_file = os.path.join(index_dir, f"{bundle_name}.json")
    with open(index_file, "w", encoding="utf-8") as f:
        json.dump({
            "bundle": bundle_name,
            "source": bundle_path,
            "ingested_at": datetime.now(TZ).isoformat(),
            "concept_count": len(concepts),
            "concepts": concepts,
        }, f, ensure_ascii=False, indent=2)
    print(f"\n   ✅ 索引写入: {index_file}")

    # 写入纯文本版（供检索用）
    text_file = os.path.join(index_dir, f"{bundle_name}.txt")
    with open(text_file, "w", encoding="utf-8") as f:
        for c in concepts:
            f.write(f"=== {c['id']} ({c['type']}) ===\n")
            f.write(f"Title: {c['title']}\n")
            f.write(f"Tags: {', '.join(c['tags'])}\n")
            f.write(f"{c['body']}\n\n")
    print(f"   ✅ 文本索引: {text_file}")

    # 写入按 type 分类的索引
    type_index_file = os.path.join(index_dir, f"{bundle_name}_by_type.json")
    type_index = {}
    for c in concepts:
        type_index.setdefault(c["type"], []).append({
            "id": c["id"],
            "title": c["title"],
            "description": c["description"],
            "tags": c["tags"],
        })
    with open(type_index_file, "w", encoding="utf-8") as f:
        json.dump(type_index, f, ensure_ascii=False, indent=2)
    print(f"   ✅ 分类索引: {type_index_file}")

    # 注册到 unified_coordinator（可选）
    if register_module:
        _register_to_coordinator(concepts, bundle_name, index_file)

    print(f"\n✅ 消费完成: {len(concepts)} concepts 已索引")
    return True


def _register_to_coordinator(concepts, bundle_name, index_file):
    """
    将 OKF 概念注册到 GalaxyOS unified_coordinator 的静态知识层

    通过写入 knowledge_assets 目录，让 claw_recall 等检索路径能访问
    """
    assets_dir = os.path.join(os.path.dirname(__file__), "..", "var", "knowledge_assets")
    os.makedirs(assets_dir, exist_ok=True)

    asset_file = os.path.join(assets_dir, f"okf_{bundle_name}.json")
    asset_data = []
    for c in concepts:
        asset_data.append({
            "id": f"okf:{bundle_name}:{c['id']}",
            "type": c["type"],
            "title": c["title"],
            "description": c["description"],
            "tags": c.get("tags", []),
            "content_snippet": c["body"][:500],
            "source": "okf",
            "bundle": bundle_name,
            "timestamp": c.get("timestamp", datetime.now(TZ).isoformat()),
        })

    with open(asset_file, "w", encoding="utf-8") as f:
        json.dump(asset_data, f, ensure_ascii=False, indent=2)
    print(f"   ✅ 已注册到知识资产: {asset_file} ({len(asset_data)} 条)")


# ═══════════════════════════════════════════════════════════
# 验证工具
# ═══════════════════════════════════════════════════════════

def verify_bundle(bundle_path, strict=False):
    """验证 OKF Bundle 结构和 concept 合法性"""
    bundle_path = os.path.abspath(bundle_path)
    print(f"\n🔍 验证 OKF Bundle: {bundle_path}")

    issues = []
    warnings = []

    # 1. 必须有 index.md
    if not os.path.isfile(os.path.join(bundle_path, "index.md")):
        issues.append("缺少 index.md（必需）")
    else:
        print("  ✅ index.md 存在")

    # 2. 扫描 concept
    concept_count = 0
    for root, dirs, files in os.walk(bundle_path):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in files:
            full = os.path.join(root, f)
            if f in RESERVED_FILES:
                continue
            if not f.endswith(".md"):
                continue

            concept_count += 1

            # 检查 frontmatter
            try:
                with open(full, "r", encoding="utf-8") as fh:
                    head = fh.read(4096)
                m = re.match(r'^---\s*\n(.*?)\n---\s*\n', head, re.DOTALL)
                if not m:
                    issues.append(f"  ❌ 无 frontmatter: {os.path.relpath(full, bundle_path)}")
                    continue
                fm = yaml.safe_load(m.group(1))
                if not isinstance(fm, dict):
                    issues.append(f"  ❌ frontmatter 非字典: {os.path.relpath(full, bundle_path)}")
                    continue
                if "type" not in fm:
                    issues.append(f"  ❌ 缺少 type 字段: {os.path.relpath(full, bundle_path)}")
                else:
                    print(f"  ✅ {os.path.relpath(full, bundle_path)} → type={fm['type']}")
            except Exception as e:
                issues.append(f"  ❌ 读取失败: {os.path.relpath(full, bundle_path)} ({e})")

    if concept_count == 0 and not issues:
        warnings.append("没有 concept 文件（空 bundle）")

    print(f"\n   总计: {concept_count} concepts")
    if issues:
        print(f"   ❌ 问题 ({len(issues)}):")
        for i in issues:
            print(f"     {i}")
    if warnings:
        print(f"   ⚠️  警告 ({len(warnings)}):")
        for w in warnings:
            print(f"     {w}")
    if not issues and not warnings:
        print("   ✅ 全部通过")
    return len(issues) == 0


# ═══════════════════════════════════════════════════════════
# Main
# ═══════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="GalaxyOS OKF 集成工具 — 导出/消费/验证 Open Knowledge Format Bundle",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 导出系统 Knowledge Bundle
  python3 galaxyos_okf.py export

  # 导出并包含 skills
  python3 galaxyos_okf.py export --include-skills --force

  # 消费外部 OKF Bundle
  python3 galaxyos_okf.py ingest /path/to/bundle

  # 验证 Bundle
  python3 galaxyos_okf.py verify /path/to/bundle
        """,
    )

    subparsers = parser.add_subparsers(dest="command", help="子命令")

    # export
    export_parser = subparsers.add_parser("export", help="导出 OKF Knowledge Bundle")
    export_parser.add_argument("--name", default="galaxyos-system",
                               help="Bundle 名称（默认 galaxyos-system）")
    export_parser.add_argument("--output", "-o", default=None,
                               help="输出目录（默认 var/okf-bundles/<name>/）")
    export_parser.add_argument("--include-skills", action="store_true",
                               help="包含 skills/ 下的所有 SKILL.md")
    export_parser.add_argument("--rewrite-source", action="store_true",
                               help="将 OKF frontmatter 写回源文件（危险！）")
    export_parser.add_argument("--force", "-f", action="store_true",
                               help="覆盖已存在的目录")

    # ingest
    ingest_parser = subparsers.add_parser("ingest", help="消费 OKF Knowledge Bundle")
    ingest_parser.add_argument("bundle_path", help="Bundle 目录路径")
    ingest_parser.add_argument("--index-dir", default=None,
                               help="索引输出目录（默认 var/okf-index/）")
    ingest_parser.add_argument("--no-register", action="store_true",
                               help="不注册到 GalaxyOS 知识层")

    # verify
    verify_parser = subparsers.add_parser("verify", help="验证 OKF Bundle")
    verify_parser.add_argument("bundle_path", help="Bundle 目录路径")
    verify_parser.add_argument("--strict", action="store_true",
                               help="严格模式（检查字段完整性）")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    if args.command == "export":
        success = export_bundle(
            bundle_name=args.name,
            output_dir=args.output,
            include_skills=args.include_skills,
            rewrite_source=args.rewrite_source,
            force=args.force,
        )
        sys.exit(0 if success else 1)

    elif args.command == "ingest":
        success = ingest_bundle(
            bundle_path=args.bundle_path,
            index_dir=args.index_dir,
            register_module=not args.no_register,
        )
        sys.exit(0 if success else 1)

    elif args.command == "verify":
        success = verify_bundle(
            bundle_path=args.bundle_path,
            strict=args.strict,
        )
        sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
