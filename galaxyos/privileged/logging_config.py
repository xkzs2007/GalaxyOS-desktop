"""
统一日志配置

为所有模块提供一致的日志格式和级别管理。
"""

import logging
import sys
from typing import Optional


LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def setup_logging(
    level: str = "INFO",
    format_str: Optional[str] = None,
    log_file: Optional[str] = None,
) -> None:
    """
    配置全局日志

    Args:
        level: 日志级别 (DEBUG/INFO/WARNING/ERROR/CRITICAL)
        format_str: 日志格式字符串
        log_file: 日志文件路径（可选，None 则仅输出到 stderr）
    """
    fmt = format_str or LOG_FORMAT
    log_level = getattr(logging, level.upper(), logging.INFO)

    handlers = [logging.StreamHandler(sys.stderr)]

    if log_file:
        from pathlib import Path
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding='utf-8'))

    logging.basicConfig(
        level=log_level,
        format=fmt,
        datefmt=LOG_DATE_FORMAT,
        handlers=handlers,
        force=True,
    )


def get_logger(name: str) -> logging.Logger:
    """
    获取模块 Logger

    Args:
        name: 模块名（通常使用 __name__）

    Returns:
        logging.Logger
    """
    return logging.getLogger(name)


__all__ = ['setup_logging', 'get_logger', 'LOG_FORMAT']
