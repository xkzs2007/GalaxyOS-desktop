"""
GalaxyOS 一键安装脚本 — Windows PowerShell 版

自动检测系统环境、安装依赖、克隆仓库、构建项目
"""

import subprocess
import sys
import platform
from pathlib import Path


def check_python_version() -> bool:
    version = sys.version_info
    if version.major == 3 and version.minor >= 11:
        print(f"  [OK] Python {version.major}.{version.minor}.{version.micro}")
        return True
    print(f"  [FAIL] Python 3.11+ required, got {version.major}.{version.minor}")
    return False


def check_node_version() -> bool:
    try:
        result = subprocess.run(["node", "--version"], capture_output=True, text=True)
        version = result.stdout.strip().lstrip("v")
        major = int(version.split(".")[0])
        if major >= 22:
            print(f"  [OK] Node.js {version}")
            return True
        print(f"  [FAIL] Node.js 22+ required, got {version}")
        return False
    except FileNotFoundError:
        print("  [FAIL] Node.js not found")
        return False


def install_python_deps() -> bool:
    print("  Installing Python dependencies...")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "openjiuwen", "fastmcp", "pyyaml"],
            check=True,
        )
        print("  [OK] Python dependencies installed")
        return True
    except subprocess.CalledProcessError:
        print("  [FAIL] Python dependencies installation failed")
        return False


def install_skills() -> bool:
    print("  Installing mattpocock/skills...")
    try:
        from galaxyos.skill_infra.skill_installer import SkillInstaller
        installer = SkillInstaller()
        report = installer.install_from_github(
            repo_url="https://github.com/mattpocock/skills",
            target_dir="_eval/skills",
            scope="user",
        )
        print(f"  [OK] {report.installed_skills} skills installed")
        return True
    except Exception as e:
        print(f"  [FAIL] Skills installation failed: {e}")
        return False


def main():
    print("=" * 60)
    print("GalaxyOS Installation")
    print("=" * 60)
    print(f"Platform: {platform.system()} {platform.machine()}")
    print()

    print("[1/4] Checking environment...")
    python_ok = check_python_version()
    node_ok = check_node_version()

    if not python_ok:
        print("\nPlease install Python 3.11+ and try again.")
        sys.exit(1)

    print("\n[2/4] Installing Python dependencies...")
    deps_ok = install_python_deps()

    print("\n[3/4] Installing mattpocock/skills...")
    skills_ok = install_skills()

    print("\n[4/4] Verifying installation...")
    try:
        from galaxyos.kernel.agent_core_bridge import AgentCoreBridge
        from galaxyos.skill_infra.skill_md_parser import SKILLMDParser
        from galaxyos.skill_infra.skill_discovery import SkillDiscovery
        print("  [OK] GalaxyOS modules importable")
    except ImportError as e:
        print(f"  [FAIL] Module import failed: {e}")
        skills_ok = False

    print("\n" + "=" * 60)
    if all([python_ok, node_ok, deps_ok, skills_ok]):
        print("Installation completed successfully!")
    else:
        print("Installation completed with warnings. Check above for details.")
    print("=" * 60)


if __name__ == "__main__":
    main()