#!/usr/bin/env python3
"""
sqlite-vec 扩展包装模块
支持多种 SQLite 实现，用户可自行选择

✅ vec0.so 已内置：
- vec0.so 已包含在 extensions/ 目录中，无需额外下载
- 自动优先加载内置扩展

安装建议：
- 需要向量搜索：pip install pysqlite3-binary
- 仅需基础功能：使用标准库 sqlite3 即可
"""

import os
import sys
from pathlib import Path
from typing import Any, Optional
import logging

logger = logging.getLogger(__name__)


def get_sqlite_module():
    """
    获取支持扩展加载的 SQLite 模块

    优先级：
    1. pysqlite3-binary（推荐）
    2. pysqlite3
    3. sqlite3（标准库，不支持扩展）

    Returns:
        sqlite3 模块
    """
    # 尝试 pysqlite3-binary
    try:
        from pysqlite3 import dbapi2 as sqlite3
        return sqlite3, True
    except ImportError:
        pass

    # 尝试 pysqlite3
    try:
        from pysqlite3 import dbapi2 as sqlite3
        return sqlite3, True
    except ImportError:
        pass

    # 回退到标准库
    import sqlite3
    return sqlite3, False


# 获取 SQLite 模块
sqlite3, SUPPORTS_EXTENSION = get_sqlite_module()


def find_vec0_extension() -> Optional[str]:
    """
    自动搜索 vec0 扩展文件（内置优先）

    搜索路径：
    1. 项目内置 extensions/ 目录（vec0.so 已内置）
    2. 包目录（与本文件同目录）
    3. 当前工作目录
    4. 项目根目录
    5. ~/.openclaw/memory-tdai/extensions/
    6. /usr/local/lib/ 和 /usr/lib/
    7. site-packages/sqlite_vec/ (pip 安装时附带)

    Returns:
        str: 扩展文件路径，未找到返回 None
    """
    # 根据平台确定扩展名
    if sys.platform == 'darwin':
        ext_names = ['vec0.dylib', 'vec0.so']
    elif sys.platform == 'win32':
        ext_names = ['vec0.dll']
    else:  # Linux 及其他
        ext_names = ['vec0.so']

    project_root = Path(__file__).parent

    # 1. 内置 extensions/ 目录（最高优先级）
    for ext_name in ext_names:
        builtin_path = project_root / "extensions" / ext_name
        if builtin_path.exists():
            return str(builtin_path)

    # 搜索路径列表
    _openclaw_home = Path(os.environ.get("OPENCLAW_HOME", str(Path.home() / ".openclaw")))
    search_paths = [
        project_root,                                        # 本文件所在目录
        Path.cwd(),                                          # 当前工作目录
        project_root.parent,                                 # 项目根目录
        _openclaw_home / "memory-tdai" / "extensions",       # 用户扩展目录
        _openclaw_home / "memory-tdai" / ".cache",           # 用户缓存目录
        Path("/usr/local/lib"),
        Path("/usr/lib"),
    ]

    # 添加 site-packages 中的 sqlite_vec 路径
    try:
        import sqlite_vec as _sv
        sp_dir = Path(_sv.__file__).parent
        search_paths.insert(0, sp_dir)
    except ImportError:
        pass

    # 在搜索路径中查找
    for search_path in search_paths:
        if not search_path.exists():
            continue
        for ext_name in ext_names:
            candidate = search_path / ext_name
            if candidate.exists():
                return str(candidate)

    # 递归搜索项目目录（深度2层）
    for ext_name in ext_names:
        matches = list(project_root.rglob(ext_name))
        if matches:
            return str(matches[0])

    return None


def connect(db_path: str, load_vec: bool = True) -> Any:
    """
    连接数据库

    当 load_vec=True 时，自动搜索并加载 vec0.so 扩展

    Args:
        db_path: 数据库文件路径
        load_vec: 是否加载 vec0 扩展

    Returns:
        数据库连接
    """
    # 展开路径
    db_path = os.path.expanduser(db_path)
    db_path = os.path.abspath(db_path)

    conn = sqlite3.connect(db_path)

    if load_vec:
        vec0_path = find_vec0_extension()
        if vec0_path:
            try:
                conn.enable_load_extension(True)
                conn.load_extension(vec0_path)
                conn.enable_load_extension(False)  # 安全：加载后禁用
            except Exception as e:
                print(f"⚠️ 加载 vec0 扩展失败: {e}")
                try:
                    conn.enable_load_extension(False)
                except Exception:
                    pass
        else:
            print("⚠️ 未找到 vec0 扩展文件")
            print("请下载 vec0 扩展：")
            print("  - https://github.com/asg017/sqlite-vec/releases")

    return conn


def _validate_extension_path(extension_path: str) -> bool:
    """
    校验扩展文件路径安全性

    仅允许加载名为 vec0 的 SQLite 扩展，防止任意共享库加载

    Args:
        extension_path: 扩展文件路径

    Returns:
        bool: 路径是否安全
    """
    path = Path(extension_path).resolve()
    stem = path.stem
    if stem != 'vec0':
        print(f"⚠️ 安全限制：仅允许加载 vec0 扩展，拒绝: {stem}")
        return False
    return True


def connect_with_extension(db_path: str, extension_path: str = None) -> Any:
    """
    连接数据库并加载 vec0 扩展

    Args:
        db_path: 数据库文件路径
        extension_path: vec0 扩展文件路径（如不指定则自动搜索，仅允许 vec0 扩展）

    Returns:
        数据库连接
    """
    db_path = os.path.expanduser(db_path)
    db_path = os.path.abspath(db_path)

    conn = sqlite3.connect(db_path)

    # 如果未指定路径，自动搜索
    if extension_path is None:
        extension_path = find_vec0_extension()

    if extension_path is None:
        print("⚠️ 未找到 vec0 扩展文件")
        print("请下载 vec0 扩展：")
        print("  - https://github.com/asg017/sqlite-vec/releases")
        return conn

    if not os.path.exists(extension_path):
        print(f"⚠️ 扩展文件不存在: {extension_path}")
        return conn

    # 安全校验：仅允许 vec0 扩展
    if not _validate_extension_path(extension_path):
        return conn

    if not SUPPORTS_EXTENSION:
        print("⚠️ 当前 SQLite 实现不支持扩展加载")
        print("请安装: pip install pysqlite3-binary")
        return conn

    # 加载扩展
    try:
        conn.enable_load_extension(True)
        conn.load_extension(extension_path)
    except Exception as e:
        print(f"⚠️ 加载 vec0 扩展失败: {e}")

    return conn


def get_vec_version(conn: Any) -> str:
    """获取 vec0 扩展版本"""
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT vec_version()")
        return cursor.fetchone()[0]
    except Exception as e:
        return f"获取版本失败: {e}"


def is_vec_available(conn: Any) -> bool:
    """检查 vec0 扩展是否可用"""
    try:
        version = get_vec_version(conn)
        return bool(version and not version.startswith("获取版本失败"))
    except Exception:
        return False


def get_installation_guide() -> str:
    """
    获取 vec0 扩展安装指南

    Returns:
        str: 安装指南
    """
    return """
# vec0 扩展安装指南

vec0 是 sqlite-vec 的 SQLite 原生扩展，提供高性能向量搜索能力。

## 安装方式

### 1. 下载预编译版本（推荐）
- GitHub Releases: https://github.com/asg017/sqlite-vec/releases
- 选择对应平台的版本：
  - Linux: vec0.so
  - macOS: vec0.dylib
  - Windows: vec0.dll

下载后放置到以下任一位置：
  - 项目根目录
  - ~/.openclaw/memory-tdai/extensions/
  - /usr/local/lib/

### 2. pip 安装（附带 vec0.so）
```bash
pip install sqlite-vec
```
安装后 vec0.so 通常位于 site-packages/sqlite_vec/ 目录下。

### 3. 从源码编译
```bash
git clone https://github.com/asg017/sqlite-vec
cd sqlite-vec
make loadable
```

## 使用示例

```python

## 官方资源

- GitHub: https://github.com/asg017/sqlite-vec
- 文档: https://sqlite-vec.com
"""


def print_status():
    """打印状态"""
    print("=== SQLite 状态 ===")
    print(f"支持扩展: {'✅ 是' if SUPPORTS_EXTENSION else '❌ 否'}")

    vec0_path = find_vec0_extension()
    if vec0_path:
        print(f"vec0 扩展: ✅ 已找到 ({vec0_path})")
    else:
        print("vec0 扩展: ❌ 未找到")
        print("  请下载 vec0 扩展：")
        print("  - https://github.com/asg017/sqlite-vec/releases")
    print("==================")


# 测试
if __name__ == "__main__":
    print_status()
