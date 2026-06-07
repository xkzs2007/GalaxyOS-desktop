"""pytest 全局配置"""
import sys
from pathlib import Path

# 确保 ltc_synapse 可被导入
_LTC_PATH = Path(__file__).parent.parent / "skills" / "llm-memory-integration" / "core"
if _LTC_PATH.exists():
    sys.path.insert(0, str(_LTC_PATH))
