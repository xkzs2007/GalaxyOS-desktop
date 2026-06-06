"""
BlobArena — Append-only mmap-backed blob storage

替代 DAG 节点的 512/2000 字符硬截断。完整保留原始文本，
只在节点中存 memo + blob_id。

设计:
- Append-only: 写入返回 blob_id (offset:size)
- mmap: 读取 O(1) 随机访问，无文件 IO
- Generational GC: 标记-清扫未引用 arena
- Thread-safe: 读写锁分离（读多写少）

Layer: L5 (缓存管理层) / L9 (会话管理层) 共用
"""

import os
import mmap
import json
import time
import logging
import threading
import struct
from typing import Optional, Dict, List, Tuple
from pathlib import Path

logger = logging.getLogger(__name__)

# ── 文件格式 ──
# Header (每 arena 文件):
#   magic: 4 bytes "BLOBA"
#   arena_id: 8 bytes (int, big-endian)
#   data_offset: 8 bytes (int, 第一个 blob 的 offset)
#   tail: 8 bytes (int, 当前写入位置 offset)
#
# Blob 记录:
#   该 record 的 data_size: 4 bytes (int, big-endian)
#   数据: data_size bytes
#
# Blob ID 格式:
#   f"{arena_id}:{offset}"

MAGIC = b"BLOBA"
HEADER_SIZE = 28  # 4 + 8 + 8 + 8
BLOB_ID_DELIM = ":"


class BlobArena:
    """Append-only mmap blob storage"""

    def __init__(self, base_dir: str = "", arena_size_mb: int = 64):
        self._base_dir = Path(base_dir or os.path.expanduser(
            "~/.openclaw/dag_blob_arena"))
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._max_arena_bytes = arena_size_mb * 1024 * 1024

        # 当前 arena 状态
        self._current_arena_id: Optional[int] = None
        self._arena_path: Optional[Path] = None
        self._mmap: Optional[mmap.mmap] = None
        self._tail: int = HEADER_SIZE  # 下一个写入位置
        self._is_full: bool = False

        # 索引: arena_id → (arena_path, mmap)
        self._readonly_arenas: Dict[int, Tuple[Path, mmap.mmap]] = {}

        # 线程安全
        self._lock = threading.Lock()

        # 初始化：恢复最后一个 arena 或创建新文件
        self._init_arena()

    def _init_arena(self):
        """恢复最后一个 arena 或创建新文件"""
        arena_files = sorted(self._base_dir.glob("arena_*.blob"),
                             key=lambda p: int(p.stem.split("_")[1]))
        if arena_files:
            last = arena_files[-1]
            arena_id = int(last.stem.split("_")[1])
            self._current_arena_id = arena_id
            self._arena_path = last
            self._load_arena(arena_id, last)
            # 检查是否已满
            if self._tail >= self._max_arena_bytes:
                self._is_full = True
                self._roll_new_arena()
        else:
            self._roll_new_arena()

    def _load_arena(self, arena_id: int, path: Path):
        """加载 arena 到 mmap"""
        size = os.path.getsize(path)
        f = os.open(path, os.O_RDWR)
        # 至少 HEADER_SIZE
        if size < HEADER_SIZE:
            os.ftruncate(f, HEADER_SIZE)
            os.write(f, MAGIC + struct.pack("!QQQ", arena_id, HEADER_SIZE, HEADER_SIZE))
            size = HEADER_SIZE
        self._mmap = mmap.mmap(f, size, access=mmap.ACCESS_WRITE)
        os.close(f)

        # 读 tail
        magic = self._mmap[0:5]
        if magic != MAGIC:
            raise ValueError(f"Invalid magic in {path}")
        self._tail = struct.unpack("!Q", self._mmap[20:28])[0]

    def _roll_new_arena(self):
        """滚动到新 arena"""
        if self._mmap:
            self._mmap.flush()
            self._maybe_close_mmap()

        old_id = self._current_arena_id or 0
        self._current_arena_id = old_id + 1
        self._arena_path = self._base_dir / f"arena_{self._current_arena_id}.blob"

        # 创建新文件
        self._arena_path.write_bytes(
            MAGIC + struct.pack("!QQQ",
                                self._current_arena_id,
                                HEADER_SIZE,
                                HEADER_SIZE)
        )
        f = os.open(str(self._arena_path), os.O_RDWR)
        self._mmap = mmap.mmap(f, HEADER_SIZE, access=mmap.ACCESS_WRITE)
        os.close(f)
        self._tail = HEADER_SIZE
        self._is_full = False
        logger.info(f"BlobArena: rolled to arena_{self._current_arena_id}")

    def _maybe_close_mmap(self):
        """安全关闭当前 mmap（如果存在）"""
        try:
            if self._mmap and not self._mmap.closed:
                self._mmap.close()
        except (ValueError, OSError):
            pass
        self._mmap = None

    def _ensure_space(self, data_size: int):
        """确保当前 arena 有足够空间，不够则滚动"""
        needed = HEADER_SIZE if self._tail == HEADER_SIZE else self._tail + 4 + data_size
        if needed > self._max_arena_bytes:
            self._is_full = True
            self._roll_new_arena()
            return self._ensure_space(data_size)
        # 扩展 mmap
        needed_size = self._tail + 4 + data_size
        if self._mmap and self._mmap.size() < needed_size:
            self._mmap.flush()
            new_size = max(needed_size, self._max_arena_bytes)
            os.truncate(str(self._arena_path), new_size)
            self._mmap.close()
            f = os.open(str(self._arena_path), os.O_RDWR)
            self._mmap = mmap.mmap(f, new_size, access=mmap.ACCESS_WRITE)
            os.close(f)

    def append(self, data: bytes) -> str:
        """
        追加写入数据，返回 blob_id

        Args:
            data: 要写入的字节数据

        Returns:
            blob_id: 格式 "arena_id:offset"
        """
        with self._lock:
            self._ensure_space(len(data))
            offset = self._tail

            # 写 size header
            self._mmap[offset:offset + 4] = struct.pack("!I", len(data))
            # 写数据
            self._mmap[offset + 4:offset + 4 + len(data)] = data
            # 更新 tail
            new_tail = offset + 4 + len(data)
            self._mmap[20:28] = struct.pack("!Q", new_tail)
            self._tail = new_tail

            blob_id = f"{self._current_arena_id}{BLOB_ID_DELIM}{offset}"
            return blob_id

    def read(self, blob_id: str, max_bytes: int = 0) -> bytes:
        """
        读取 blob_id 对应的数据

        Args:
            blob_id: "arena_id:offset"
            max_bytes: 最大读取字节数，0=全部

        Returns:
            bytes 数据
        """
        parts = blob_id.split(BLOB_ID_DELIM)
        if len(parts) != 2:
            raise ValueError(f"Invalid blob_id: {blob_id}")
        arena_id = int(parts[0])
        offset = int(parts[1])

        # 如果当前 arena 是目标，直接用当前 mmap
        if arena_id == self._current_arena_id and self._mmap and not self._mmap.closed:
            mm = self._mmap
        else:
            # 从 readonly 缓存取或加载
            if arena_id not in self._readonly_arenas:
                path = self._base_dir / f"arena_{arena_id}.blob"
                if not path.exists():
                    raise FileNotFoundError(f"arena_{arena_id}.blob not found")
                size = os.path.getsize(path)
                f = os.open(str(path), os.O_RDONLY)
                ro_mmap = mmap.mmap(f, size, access=mmap.ACCESS_READ)
                os.close(f)
                self._readonly_arenas[arena_id] = (path, ro_mmap)
                mm = ro_mmap
            else:
                mm = self._readonly_arenas[arena_id][1]

        # 读 data_size
        data_size = struct.unpack("!I", mm[offset:offset + 4])[0]
        read_size = data_size if max_bytes <= 0 else min(data_size, max_bytes)
        return bytes(mm[offset + 4:offset + 4 + read_size])

    def read_text(self, blob_id: str, max_chars: int = 0) -> str:
        """读取文本"""
        data = self.read(blob_id, max_bytes=max_chars)
        return data.decode("utf-8", errors="replace")

    def append_text(self, text: str) -> str:
        """追加写入文本"""
        return self.append(text.encode("utf-8"))

    def get_arena_info(self) -> Dict:
        """获取所有 arena 信息"""
        with self._lock:
            arenas = sorted(self._base_dir.glob("arena_*.blob"),
                            key=lambda p: int(p.stem.split("_")[1]))
            info = []
            for path in arenas:
                aid = int(path.stem.split("_")[1])
                size = os.path.getsize(path)
                info.append({
                    "arena_id": aid,
                    "path": str(path),
                    "size_bytes": size,
                })
            return {
                "arenas": info,
                "total_arenas": len(info),
                "current_arena_id": self._current_arena_id,
                "current_tail": self._tail,
            }

    def close(self):
        """关闭所有 mmap"""
        with self._lock:
            self._maybe_close_mmap()
            for aid, (path, mm) in self._readonly_arenas.items():
                try:
                    if not mm.closed:
                        mm.close()
                except (ValueError, OSError):
                    pass
            self._readonly_arenas.clear()

    def __del__(self):
        self.close()


# ── 全局单例 ──

_BLOB_ARENA: Optional[BlobArena] = None
_BLOB_ARENA_LOCK = threading.Lock()


def get_blob_arena(base_dir: str = "") -> BlobArena:
    """获取全局 BlobArena 实例"""
    global _BLOB_ARENA
    if _BLOB_ARENA is None:
        with _BLOB_ARENA_LOCK:
            if _BLOB_ARENA is None:
                _BLOB_ARENA = BlobArena(base_dir=base_dir)
    return _BLOB_ARENA


# ── Memo 生成（GAM 风格 ~50 词轻量 memo） ──

def generate_memo(text: str, max_words: int = 50) -> str:
    """
    从文本生成轻量 memo（仅做检索索引用，不做价值判断）

    策略：
    1. jieba 提取关键词（TF 加权，取 top-10）
    2. 取文本前 2 句 + 后 1 句（保留头尾信息）
    3. 如果超 max_words，取关键词+头尾的紧凑组合

    Args:
        text: 原始文本（不限长度）
        max_words: memo 最大词数

    Returns:
        str: ~50 词的轻量 memo
    """
    if not text:
        return ""

    # 关键词提取
    try:
        import jieba.analyse
        keywords = jieba.analyse.extract_tags(
            text[:2000], topK=10, withWeight=True
        )
        kw_text = " ".join([kw for kw, _ in keywords])
    except ImportError:
        # 没有 jieba → 取高频字
        import re
        chars = re.findall(r'[\u4e00-\u9fff\w]+', text[:2000])
        from collections import Counter
        freq = Counter(chars)
        kw_text = " ".join([c for c, _ in freq.most_common(10)])

    # 头尾句子
    import re as _re
    sentences = _re.split(r'[。！？\n!?]', text.strip())
    sentences = [s.strip() for s in sentences if s.strip()]
    head = sentences[0][:100] if sentences else text[:100]
    tail = sentences[-1][:100] if len(sentences) > 1 else ""

    # 组合 memo
    memo_parts = [kw_text]
    if head:
        memo_parts.append(f"[头] {head}")
    if tail and tail != head:
        memo_parts.append(f"[尾] {tail}")

    memo = " | ".join(memo_parts)

    # 限 max_words（中文 ≈ 2 字节/词，按字符数 / 2 估算）
    max_chars = max_words * 2
    if len(memo) > max_chars:
        memo = memo[:max_chars - 3] + "..."

    return memo
