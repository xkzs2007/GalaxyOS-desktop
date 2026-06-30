#!/usr/bin/env python3
"""
备份管理器
自动备份系统配置和数据
"""

import json
import shutil
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from datetime import datetime

logger = logging.getLogger('xiaoyi-claw-omega.backup')


class BackupManager:
    """备份管理器"""

    def __init__(self, backup_dir: Optional[Path] = None):
        self.backup_dir = backup_dir or Path(__file__).parent.parent / "backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        self.max_backups = 10  # 最大备份数量
        self.backup_prefix = "backup_"

    def create_backup(self, name: Optional[str] = None) -> Path:
        """创建备份"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{self.backup_prefix}{timestamp}"
        if name:
            backup_name = f"{backup_name}_{name}"

        backup_path = self.backup_dir / backup_name
        backup_path.mkdir(parents=True, exist_ok=True)

        # 备份配置
        self._backup_configs(backup_path)

        # 备份注册表
        self._backup_registries(backup_path)

        # 创建备份元数据
        self._create_metadata(backup_path, name)

        # 清理旧备份
        self._cleanup_old_backups()

        logger.info(f"备份已创建: {backup_path}")
        return backup_path

    def _backup_configs(self, backup_path: Path):
        """备份配置文件"""
        config_dir = Path(__file__).parent.parent / "config"
        if config_dir.exists():
            dest = backup_path / "config"
            shutil.copytree(config_dir, dest)
            logger.info(f"  ✅ 配置已备份")

    def _backup_registries(self, backup_path: Path):
        """备份注册表"""
        registry_dir = Path(__file__).parent.parent / "infrastructure" / "inventory"
        if registry_dir.exists():
            dest = backup_path / "inventory"
            shutil.copytree(registry_dir, dest)
            logger.info(f"  ✅ 注册表已备份")

    def _create_metadata(self, backup_path: Path, name: Optional[str]):
        """创建备份元数据"""
        metadata = {
            "timestamp": datetime.now().isoformat(),
            "name": name or "auto_backup",
            "version": "4.1.0",
            "files": list(str(p.relative_to(backup_path)) for p in backup_path.rglob("*") if p.is_file())
        }

        metadata_file = backup_path / "backup_metadata.json"
        with open(metadata_file, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)

    def _cleanup_old_backups(self):
        """清理旧备份"""
        backups = sorted(
            self.backup_dir.glob(f"{self.backup_prefix}*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        )

        # 保留最新的 max_backups 个备份
        for old_backup in backups[self.max_backups:]:
            shutil.rmtree(old_backup)
            logger.info(f"  🗑️ 清理旧备份: {old_backup.name}")

    def list_backups(self) -> List[Dict[str, Any]]:
        """列出所有备份"""
        backups = []

        for backup_path in sorted(
            self.backup_dir.glob(f"{self.backup_prefix}*"),
            key=lambda p: p.stat().st_mtime,
            reverse=True
        ):
            metadata_file = backup_path / "backup_metadata.json"
            if metadata_file.exists():
                with open(metadata_file, 'r', encoding='utf-8') as f:
                    metadata = json.load(f)
                backups.append({
                    "path": str(backup_path),
                    "name": metadata.get("name", "unknown"),
                    "timestamp": metadata.get("timestamp", "unknown"),
                    "version": metadata.get("version", "unknown")
                })

        return backups

    def restore_backup(self, backup_path: Path) -> bool:
        """恢复备份"""
        if not backup_path.exists():
            logger.error(f"备份不存在: {backup_path}")
            return False

        try:
            # 恢复配置
            config_backup = backup_path / "config"
            if config_backup.exists():
                config_dir = Path(__file__).parent.parent / "config"
                if config_dir.exists():
                    shutil.rmtree(config_dir)
                shutil.copytree(config_backup, config_dir)
                logger.info("  ✅ 配置已恢复")

            # 恢复注册表
            registry_backup = backup_path / "inventory"
            if registry_backup.exists():
                registry_dir = Path(__file__).parent.parent / "infrastructure" / "inventory"
                if registry_dir.exists():
                    shutil.rmtree(registry_dir)
                shutil.copytree(registry_backup, registry_dir)
                logger.info("  ✅ 注册表已恢复")

            logger.info(f"备份已恢复: {backup_path}")
            return True

        except Exception as e:
            logger.error(f"恢复失败: {e}")
            return False


def main():
    """主函数"""
    logging.basicConfig(level=logging.INFO)

    print("=" * 60)
    print("💾 备份管理器")
    print("=" * 60)
    print()

    manager = BackupManager()

    # 创建备份
    print("1️⃣ 创建备份...")
    backup_path = manager.create_backup("manual")
    print(f"   备份路径: {backup_path}")
    print()

    # 列出备份
    print("2️⃣ 备份列表:")
    backups = manager.list_backups()
    for i, backup in enumerate(backups, 1):
        print(f"   {i}. {backup['name']} - {backup['timestamp']}")
    print()

    print("=" * 60)
    print("✅ 备份完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
