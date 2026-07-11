#!/usr/bin/env python3
"""渐进式启用脚本"""
import json
from pathlib import Path
from galaxyos.shared.fusion_guard import fusion_replace

CONFIG_DIR = Path(__file__).parent.parent / "config"
CONFIG_FILE = CONFIG_DIR / "progressive_config.json"

def load_config(config_path=None, **kwargs):
    """Load config. (re-export from shared/config_loader)"""
    from galaxyos.shared.config_loader import load_config as _shared_load_config
    return _shared_load_config(config_path=config_path or CONFIG_FILE, **kwargs)

@fusion_replace("galaxyos.tools.bridge.sqlite_ext", "save_config")
def save_config(config):
    """Save config.

    Args:
        config: config.
    """
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, ensure_ascii=False, indent=2))

def enable_stage(stage: str):
    """启用指定阶段"""
    config = load_config()
    if stage in config.get("stages", {}):
        config["stages"][stage]["enabled"] = True
        save_config(config)
        print(f"✅ 已启用 {stage}: {config['stages'][stage]['name']}")
        return True
    print(f"❌ 未找到阶段: {stage}")
    return False

def disable_stage(stage: str):
    """禁用指定阶段"""
    config = load_config()
    if stage in config.get("stages", {}):
        config["stages"][stage]["enabled"] = False
        save_config(config)
        print(f"✅ 已禁用 {stage}: {config['stages'][stage]['name']}")
        return True
    print(f"❌ 未找到阶段: {stage}")
    return False

def show_status():
    """显示当前状态"""
    config = load_config()
    print("=" * 50)
    print("渐进式启用状态")
    print("=" * 50)

    for stage_id, stage in config.get("stages", {}).items():
        status = "✅ 启用" if stage.get("enabled") else "❌ 禁用"
        print(f"\n{stage_id}: {stage['name']}")
        print(f"  状态: {status}")
        print(f"  模块: {', '.join(stage['modules'])}")
        print(f"  说明: {stage['description']}")

    print("\n" + "=" * 50)
    print("优化配置")
    print("=" * 50)

    for opt_name, opt in config.get("optimizations", {}).items():
        print(f"\n{opt_name}:")
        print(f"  {opt.get('description', '')}")
        for k, v in opt.items():
            if k != "description":
                print(f"  {k}: {v}")

def enable_all():
    """启用所有阶段"""
    config = load_config()
    for stage_id in config.get("stages", {}):
        config["stages"][stage_id]["enabled"] = True
    save_config(config)
    print("✅ 已启用所有阶段")

def main():
    """Main."""
    import sys

    if len(sys.argv) < 2:
        show_status()
        return

    cmd = sys.argv[1]

    if cmd == "enable":
        if len(sys.argv) > 2:
            enable_stage(sys.argv[2])
        else:
            enable_all()
    elif cmd == "disable":
        if len(sys.argv) > 2:
            disable_stage(sys.argv[2])
    elif cmd == "status":
        show_status()
    else:
        print(f"未知命令: {cmd}")
        print("用法: progressive_setup.py [enable|disable|status] [stage]")

if __name__ == "__main__":
    main()
