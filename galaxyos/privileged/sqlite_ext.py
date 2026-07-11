#!/usr/bin/env python3
"""
SQLite 扩展模块
支持多种 SQLite 实现，用户可自行选择

支持的实现：
1. pysqlite3-binary - 支持扩展加载（推荐）
2. pysqlite3 - 纯 Python 实现
3. sqlite3 - Python 标准库（不支持扩展）

✅ vec0.so 已内置：
- vec0.so 已包含在 extensions/ 目录中，无需额外下载
- 自动优先加载内置扩展

安装建议：
- 需要向量搜索：pip install pysqlite3-binary
- 仅需基础功能：使用标准库 sqlite3 即可
"""

import os
import sys
import json
from pathlib import Path
from typing import Optional, Any, Dict
import importlib

# 配置文件路径（v3.0.0 公私分离：优先使用环境变量）

# ── Centralized path resolution ──
import os as _os
import sys as _sys
from galaxyos.shared.paths import galaxyos_home, workspace
_ws_root = workspace()
for _p in [_ws_root, "/workspace"]:
    if _p not in _sys.path:
        _sys.path.insert(0, _p)
import path_resolver
_OPENCLAW_HOME = Path(galaxyos_home())
CONFIG_PATH = Path(os.environ.get("OPENCLAW_SQLITE_CONFIG", str(_OPENCLAW_HOME / "memory-tdai" / "config" / "extension_config.json")))

# 默认配置
DEFAULT_CONFIG = {
    "enable_native_extension": False,
    "preferred_sqlite": "auto",
    "vec0_path": None  # 用户自定义 vec0 扩展路径
}


def load_config() -> dict:
    """加载配置"""
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, 'r') as f:
                config = json.load(f)
                # 兼容旧配置字段
                if "sqlite_vec_path" in config and "vec0_path" not in config:
                    config["vec0_path"] = config.pop("sqlite_vec_path")
                return {**DEFAULT_CONFIG, **config}
        except Exception:
            pass
    return DEFAULT_CONFIG


def save_config(config: dict):
    """保存配置"""
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, 'w') as f:
        json.dump(config, f, indent=2)


# 加载配置
_config = load_config()


def detect_sqlite_implementations() -> Dict[str, Dict]:
    """
    检测可用的 SQLite 实现

    Returns:
        Dict: 可用的实现及其特性
    """
    implementations = {}

    # 1. pysqlite3-binary（推荐，支持扩展）
    try:
        mod = importlib.import_module('pysqlite3')
        implementations['pysqlite3-binary'] = {
            'module': mod,
            'supports_extension': True,
            'description': 'pysqlite3-binary - 支持扩展加载（推荐）',
            'install': 'pip install pysqlite3-binary'
        }
    except ImportError:
        pass

    # 2. pysqlite3（纯 Python）
    try:
        mod = importlib.import_module('pysqlite3')
        if 'pysqlite3-binary' not in implementations:
            implementations['pysqlite3'] = {
                'module': mod,
                'supports_extension': True,
                'description': 'pysqlite3 - 纯 Python 实现',
                'install': 'pip install pysqlite3'
            }
    except ImportError:
        pass

    # 3. 标准库 sqlite3（不支持扩展）
    try:
        import sqlite3
        implementations['sqlite3'] = {
            'module': sqlite3,
            'supports_extension': False,
            'description': 'sqlite3 - Python 标准库（不支持扩展）',
            'install': '无需安装'
        }
    except ImportError:
        pass

    return implementations


def get_best_sqlite():
    """
    获取最优的 SQLite 实现

    优先级：
    1. pysqlite3-binary（支持扩展）
    2. pysqlite3（支持扩展）
    3. sqlite3（标准库）

    Returns:
        tuple: (module, info_dict)
    """
    implementations = detect_sqlite_implementations()

    # 按优先级选择
    for name in ['pysqlite3-binary', 'pysqlite3', 'sqlite3']:
        if name in implementations:
            return implementations[name]['module'], implementations[name]

    # 回退到标准库
    import sqlite3
    return sqlite3, {
        'supports_extension': False,
        'description': 'sqlite3 - Python 标准库',
        'install': '无需安装'
    }


# 自动选择最优实现
sqlite3, SQLITE_INFO = get_best_sqlite()
HAS_PYSQLITE3 = 'pysqlite3' in str(type(sqlite3).__module__)
SUPPORTS_EXTENSION = SQLITE_INFO.get('supports_extension', False)


def find_vec0_extension() -> Optional[str]:
    """
    自动搜索 vec0 扩展文件（内置优先）

    搜索优先级：
    1. 项目内置 extensions/ 目录（vec0.so 已内置）
    2. 配置文件中指定的路径 (vec0_path)
    3. 包目录（与本文件同目录）
    4. 当前工作目录
    5. 项目根目录
    6. ~/.openclaw/memory-tdai/extensions/
    7. /usr/local/lib/ 和 /usr/lib/
    8. site-packages/sqlite_vec/ (pip 安装时附带)

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

    # 2. 配置文件指定的路径
    configured_path = _config.get("vec0_path")
    if configured_path and os.path.exists(configured_path):
        return configured_path

    # 搜索路径列表
    _openclaw_home_ext = Path(galaxyos_home())
    search_paths = [
        project_root,                                        # 本文件所在目录
        Path.cwd(),                                          # 当前工作目录
        project_root.parent,                                 # 项目根目录
        _openclaw_home_ext / "memory-tdai" / "extensions",   # 用户扩展目录
        _openclaw_home_ext / "memory-tdai" / ".cache",       # 用户缓存目录
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


def print_sqlite_status():
    """打印 SQLite 状态"""
    print("=== SQLite 实现状态 ===")
    print(f"当前使用: {SQLITE_INFO['description']}")
    print(f"支持扩展: {'✅ 是' if SUPPORTS_EXTENSION else '❌ 否'}")

    implementations = detect_sqlite_implementations()
    print("\n可用实现:")
    for name, info in implementations.items():
        marker = " (当前)" if info['module'] == sqlite3 else ""
        print(f"  - {info['description']}{marker}")
        print(f"    安装: {info['install']}")

    vec0_path = find_vec0_extension()
    if vec0_path:
        print(f"\n✅ vec0 扩展: {vec0_path}")
    else:
        print("\n📦 vec0 扩展安装指南:")
        print("  vec0 是 sqlite-vec 的原生扩展，提供向量搜索能力")
        print("  安装方式:")
        print("  1. 下载预编译版本: https://github.com/asg017/sqlite-vec/releases")
        print("  2. pip install sqlite-vec (附带 vec0.so)")
        print("  3. 从源码编译: git clone https://github.com/asg017/sqlite-vec && make loadable")
    print("=====================")


def get_sqlite_module():
    """
    获取当前 SQLite 模块

    Returns:
        sqlite3 模块
    """
    return sqlite3


def connect(db_path: str, load_vec: bool = False) -> Any:
    """
    连接数据库

    当 load_vec=True 时，自动搜索并加载 vec0 扩展

    Args:
        db_path: 数据库文件路径
        load_vec: 是否加载 vec0 扩展

    Returns:
        数据库连接
    """
    # 展开路径
    db_path = os.path.expanduser(db_path)
    db_path = os.path.abspath(db_path)

    # 连接数据库
    conn = sqlite3.connect(db_path)

    if load_vec:
        vec0_path = find_vec0_extension()
        if vec0_path and SUPPORTS_EXTENSION:
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
        elif not SUPPORTS_EXTENSION:
            print("⚠️ 当前 SQLite 实现不支持扩展加载")
            print("  请安装: pip install pysqlite3-binary")
        else:
            print("⚠️ 未找到 vec0 扩展文件")
            print("  下载地址: https://github.com/asg017/sqlite-vec/releases")

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
    # 仅允许 vec0 扩展
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
        print("  下载地址: https://github.com/asg017/sqlite-vec/releases")
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


def get_vec_installation_guide() -> str:
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
import sqlite3

# 加载扩展
conn = sqlite3.connect('vectors.db')
conn.enable_load_extension(True)
conn.load_extension('./vec0')  # 或 vec0.dll / vec0.so

# 创建向量表
conn.execute('''
    CREATE VIRTUAL TABLE vec_items USING vec0(
        embedding FLOAT[384]
    )
''')

# 插入向量
conn.execute(
    'INSERT INTO vec_items(rowid, embedding) VALUES (?, ?)',
    (1, '[0.1, 0.2, 0.3, ...]')
)

# 向量搜索
results = conn.execute('''
    SELECT rowid, distance
    FROM vec_items
    WHERE embedding MATCH '[0.1, 0.2, 0.3, ...]'
    ORDER BY distance
    LIMIT 10
''')
```

## 官方资源

- GitHub: https://github.com/asg017/sqlite-vec
- 文档: https://sqlite-vec.com
- 示例: https://github.com/asg017/sqlite-vec/tree/main/examples
"""


def is_extension_supported() -> bool:
    """检查是否支持扩展加载"""
    return SUPPORTS_EXTENSION


# 导出
__all__ = [
    'sqlite3',
    'connect',
    'connect_with_extension',
    'get_sqlite_module',
    'detect_sqlite_implementations',
    'get_best_sqlite',
    'print_sqlite_status',
    'get_vec_installation_guide',
    'is_extension_supported',
    'find_vec0_extension',
    'HAS_PYSQLITE3',
    'SUPPORTS_EXTENSION'
]


# 测试
if __name__ == "__main__":
    print_sqlite_status()
    print()
    print("=== vec0 扩展安装指南 ===")
    print(get_vec_installation_guide())
