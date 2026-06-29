#!/usr/bin/env python3
"""
性能优化补丁模块 — 自动启用已安装的高性能库

在 xiaoyi_memory.py 和 memory_unified.py 入口处 import 即可全自动激活。

激活方案：
1. orjson → 猴子补丁 json 模块（10x 加速，零代码变更）
2. uvloop → 替换 asyncio 事件循环（异步 I/O 加速）
3. DuckDB → 数据统计分析引擎（按需使用）
4. sqlite-vec → SQLite 向量扩展自动加载
5. Polars → 高性能 DataFrame（按需使用）
6. ONNX Runtime → 推理加速（按需使用）

使用方法：
    import performance_patch  # 放最顶部即可
"""

import sys
import os
import importlib.util
import logging
from typing import Optional

logger = logging.getLogger("performance_patch")

# ==================== 状态追踪 ====================

_status = {
    "orjson": False,
    "uvloop": False,
    "duckdb": False,
    "sqlite_vec": False,
    "polars": False,
    "pandas": False,
    "onnxruntime": False,
}

def get_status():
    """获取补丁状态"""
    return dict(_status)


# ==================== 1. orjson → json 猴子补丁 ====================

def _patch_json_with_orjson():
    """用 orjson 替换标准 json 模块（10x 加速）"""
    try:
        import orjson
    except ImportError:
        return False

    import json as _builtin_json

    # 保存原始引用（用于 fallback）
    _status["_original_json"] = {
        "dumps": _builtin_json.dumps,
        "loads": _builtin_json.loads,
    }

    # orjson 兼容适配器
    class OrjsonEncoder(_builtin_json.JSONEncoder):
        """兼容 json.dumps(cls=) 的 encoder"""
        def encode(self, o):
            return orjson.dumps(o).decode("utf-8")
        def iterencode(self, o, _one_shot=False):
            yield orjson.dumps(o).decode("utf-8")

    # 猴子补丁 json 模块
    def _orjson_dumps(obj, *, default=None, cls=None, **kwargs):
        """替代 json.dumps，支持 cls 参数时 fallback"""
        # 如果有 cls 或者 default 参数，fallback 到原始实现
        if cls is not None or default is not None:
            return _status["_original_json"]["dumps"](obj, default=default, cls=cls, **kwargs)

        option = 0
        indent = kwargs.get("indent")
        if indent is not None and indent > 0:
            option |= orjson.OPT_INDENT_2
        if kwargs.get("ensure_ascii", True) is False:
            option |= orjson.OPT_NON_STR_KEYS

        result = orjson.dumps(obj, option=option)
        return result.decode("utf-8")

    def _orjson_loads(s, **kwargs):
        """替代 json.loads"""
        if isinstance(s, str):
            s = s.encode("utf-8")
        return orjson.loads(s)

    _builtin_json.dumps = _orjson_dumps
    _builtin_json.loads = _orjson_loads
    _builtin_json.JSONEncoder = OrjsonEncoder

    _status["orjson"] = True
    logger.info("✅ orjson: json 模块已补丁 (10x 加速)")
    return True


# ==================== 2. uvloop → asyncio 事件循环 ====================

def _patch_asyncio_with_uvloop():
    """替换 asyncio 事件循环为 uvloop"""
    try:
        import uvloop
    except ImportError:
        return False

    try:
        import asyncio
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
        _status["uvloop"] = True
        logger.info("✅ uvloop: asyncio 事件循环已替换")
        return True
    except Exception as e:
        logger.warning(f"⚠️ uvloop 替换失败: {e}")
        return False


# ==================== 3. DuckDB 分析引擎 ====================

class DuckDBAnalyzer:
    """基于 DuckDB 的记忆分析引擎"""
    
    def __init__(self):
        self.conn = None
        self._init()
    
    def _init(self):
        try:
            import duckdb
            self.conn = duckdb.connect(":memory:")
            self.conn.execute("SET enable_progress_bar=false")
            _status["duckdb"] = True
        except ImportError:
            pass
    
    def available(self) -> bool:
        return self.conn is not None
    
    def analyze_memories(self, memories: list) -> dict:
        """对记忆列表进行统计分析"""
        if not self.available() or not memories:
            return {}
        
        import pandas as pd
        df = pd.DataFrame(memories)
        self.conn.register("memories", df)
        
        result = {}
        try:
            # 按来源统计
            if "source" in df.columns:
                result["by_source"] = self.conn.execute(
                    "SELECT source, count(*) as cnt FROM memories GROUP BY source ORDER BY cnt DESC"
                ).fetchdf().to_dict("records")
            
            # 按置信度区间统计
            if "confidence" in df.columns:
                result["confidence_stats"] = self.conn.execute(
                    "SELECT min(confidence), avg(confidence), max(confidence) FROM memories"
                ).fetchdf().to_dict("records")
        except Exception:
            pass
        finally:
            self.conn.unregister("memories")
        
        return result
    
    def query(self, sql: str, params: Optional[list] = None):
        """执行 SQL 查询"""
        if not self.available():
            return None
        try:
            if params:
                return self.conn.execute(sql, params).fetchall()
            return self.conn.execute(sql).fetchall()
        except Exception as e:
            logger.error(f"DuckDB 查询错误: {e}")
            return None
    
    def __del__(self):
        if self.conn:
            try:
                self.conn.close()
            except Exception:
                pass


_analyzer: Optional[DuckDBAnalyzer] = None

def get_analyzer() -> DuckDBAnalyzer:
    """获取 DuckDB 分析器单例"""
    global _analyzer
    if _analyzer is None:
        _analyzer = DuckDBAnalyzer()
    return _analyzer


# ==================== 4. Polars 高性能 DataFrame ====================

class PolarsHelper:
    """Polars 高性能数据处理辅助"""
    
    @staticmethod
    def available() -> bool:
        return "polars" in _status and _status["polars"]
    
    def __init__(self):
        try:
            import polars as pl
            self.pl = pl
            _status["polars"] = True
        except ImportError:
            self.pl = None
    
    def from_dicts(self, data: list) -> Optional[object]:
        """从字典列表创建 DataFrame"""
        if not self.pl or not data:
            return None
        return self.pl.DataFrame(data)
    
    def to_dicts(self, df) -> list:
        """DataFrame 转字典列表"""
        if not self.pl:
            return []
        return df.to_dicts()
    
    def search_text(self, df, column: str, keyword: str) -> Optional[object]:
        """Polars 文本搜索（比纯 Python 快 10x+）"""
        if not self.pl:
            return None
        return df.filter(self.pl.col(column).str.contains(keyword))


_polars: Optional[PolarsHelper] = None

def get_polars() -> PolarsHelper:
    """获取 Polars 辅助单例"""
    global _polars
    if _polars is None:
        _polars = PolarsHelper()
    return _polars


# ==================== 5. Pandas 辅助 ====================

class PandasHelper:
    """Pandas 数据处理辅助"""
    
    @staticmethod
    def available() -> bool:
        return "pandas" in _status and _status["pandas"]
    
    def __init__(self):
        try:
            import pandas as pd
            self.pd = pd
            _status["pandas"] = True
        except ImportError:
            self.pd = None
    
    def from_dicts(self, data: list) -> Optional[object]:
        if not self.pd or not data:
            return None
        return self.pd.DataFrame(data)
    
    def to_dicts(self, df) -> list:
        if not self.pd:
            return []
        return df.to_dict("records")
    
    def group_count(self, df, column: str) -> Optional[dict]:
        """按列统计计数"""
        if not self.pd:
            return None
        return df[column].value_counts().to_dict()


_pandas: Optional[PandasHelper] = None

def get_pandas() -> PandasHelper:
    global _pandas
    if _pandas is None:
        _pandas = PandasHelper()
    return _pandas


# ==================== 6. ONNX Runtime 推理加速 ====================

class ONNXInference:
    """ONNX Runtime 推理加速（用于 Embedding 模型）"""
    
    @staticmethod
    def available() -> bool:
        return "onnxruntime" in _status and _status["onnxruntime"]
    
    def __init__(self):
        self.session = None
        try:
            import onnxruntime as ort
            self.ort = ort
            _status["onnxruntime"] = True
        except ImportError:
            pass
    
    def load_model(self, model_path: str) -> bool:
        """加载 ONNX 模型"""
        if not hasattr(self, "ort") or not self.ort:
            return False
        try:
            self.session = self.ort.InferenceSession(
                model_path,
                providers=["CPUExecutionProvider"]
            )
            return True
        except Exception as e:
            logger.error(f"ONNX 模型加载失败: {e}")
            return False
    
    def run(self, input_name: str, input_data):
        """运行推理"""
        if not self.session:
            return None
        return self.session.run(None, {input_name: input_data})


_onnx: Optional[ONNXInference] = None

def get_onnx() -> ONNXInference:
    global _onnx
    if _onnx is None:
        _onnx = ONNXInference()
    return _onnx


# ==================== 7. sqlite-vec 自动加载 ====================

def _enable_sqlite_vec():
    """确保 sqlite-vec 扩展可用"""
    import os
    try:
        from sqlite_vec import get_sqlite_module
        sqlite3, has_ext = get_sqlite_module()
        if not has_ext:
            return False
        
        # 从 pip 的 sqlite_vec 包加载 vec0.so
        pip_vec = os.path.expanduser(
            "~/.openclaw/workspace/repo/lib/python3.12/site-packages/sqlite_vec/vec0.so"
        )
        alt_vecs = [
            pip_vec,
            os.path.expanduser("~/.openclaw/node_modules/sqlite-vec-linux-x64/vec0.so"),
            os.path.expanduser("~/.openclaw/extensions/memory-tencentdb/node_modules/sqlite-vec-linux-x64/vec0.so"),
        ]
        
        conn = sqlite3.connect(":memory:")
        conn.enable_load_extension(True)
        
        for path in alt_vecs:
            if os.path.exists(path):
                conn.execute(f"SELECT load_extension('{path}')")
                _status["sqlite_vec"] = True
                logger.info(f"✅ sqlite-vec: {path} 已加载")
                break
        
        conn.close()
        return _status["sqlite_vec"]
    except Exception as e:
        logger.debug(f"sqlite-vec 加载跳过: {e}")
        return False


# ==================== 汇总输出 ====================

def patch_all():
    """一键激活所有可用优化"""
    results = []
    
    results.append(("orjson", _patch_json_with_orjson()))
    results.append(("uvloop", _patch_asyncio_with_uvloop()))
    results.append(("sqlite-vec", _enable_sqlite_vec()))
    
    # DuckDB/Polars/Pandas 懒加载，首次使用时自动初始化
    results.append(("duckdb", importlib.util.find_spec("duckdb") is not None))
    results.append(("polars", importlib.util.find_spec("polars") is not None))
    results.append(("pandas", importlib.util.find_spec("pandas") is not None))
    results.append(("onnxruntime", importlib.util.find_spec("onnxruntime") is not None))
    
    enabled = [name for name, ok in results if ok]
    logger.info(f"性能补丁完成: {', '.join(enabled)}")
    return _status


# ==================== 自动激活 ====================

# import 时自动执行
patch_all()
