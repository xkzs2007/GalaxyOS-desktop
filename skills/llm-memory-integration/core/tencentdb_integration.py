#!/usr/bin/env python3
"""
腾讯云记忆插件集成 (TencentDB Memory Integration)

整合腾讯云四层记忆系统：
- L0: 原始对话 (conversations/)
- L1: 结构化记忆 (memories.db)
- L2: 场景归纳 (scene_blocks/)
- L3: 用户画像 (persona.md)

数据位置: ~/.openclaw/memory-tdai/

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-04-21
"""

import os
import sys
import json
import sqlite3
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)


class TencentDBMemory:
    """
    腾讯云记忆插件集成
    
    四层记忆架构:
    - L0: 原始对话记录 (conversations/)
    - L1: 结构化记忆 (memories.db + vectors.db)
    - L2: 场景归纳 (scene_blocks/)
    - L3: 用户画像 (persona.md)
    """
    
    def __init__(self, data_path: str = None):
        self.data_path = Path(data_path or 
            os.path.expanduser("~/.openclaw/memory-tdai"))
        
        # 数据库路径
        self.memories_db = self.data_path / "memories.db"
        self.vectors_db = self.data_path / "vectors.db"
        self.persona_file = self.data_path / "persona.md"
        self.scene_blocks_dir = self.data_path / "scene_blocks"
        self.conversations_dir = self.data_path / "conversations"
        
        # 检查数据是否存在
        self._check_data()
        
        logger.info(f"✅ 腾讯云记忆插件已初始化: {self.data_path}")
    
    def _check_data(self):
        """检查数据目录"""
        if not self.data_path.exists():
            logger.warning(f"数据目录不存在: {self.data_path}")
            return
        
        stats = {
            "memories_db": self.memories_db.exists(),
            "vectors_db": self.vectors_db.exists(),
            "persona": self.persona_file.exists(),
            "scene_blocks": self.scene_blocks_dir.exists(),
            "conversations": self.conversations_dir.exists(),
        }
        
        logger.debug(f"数据状态: {stats}")
    
    # ==================== L1: 结构化记忆 ====================
    
    def get_memories(self, limit: int = 100) -> List[Dict]:
        """获取所有 L1 记忆"""
        if not self.memories_db.exists():
            return []
        
        try:
            conn = sqlite3.connect(str(self.memories_db))
            cursor = conn.cursor()
            cursor.execute("""
                SELECT id, content, metadata, created_at, updated_at
                FROM memories
                ORDER BY created_at DESC
                LIMIT ?
            """, (limit,))
            
            memories = []
            for row in cursor.fetchall():
                memories.append({
                    "id": row[0],
                    "content": row[1],
                    "metadata": json.loads(row[2]) if row[2] else {},
                    "created_at": row[3],
                    "updated_at": row[4]
                })
            
            conn.close()
            return memories
            
        except Exception as e:
            logger.error(f"获取记忆失败: {e}")
            return []
    
    def search_memories(self, query: str, limit: int = 10) -> List[Dict]:
        """全文搜索记忆 (FTS)"""
        if not self.memories_db.exists():
            return []
        
        try:
            conn = sqlite3.connect(str(self.memories_db))
            cursor = conn.cursor()
            
            # 使用 FTS 搜索
            cursor.execute("""
                SELECT m.id, m.content, m.metadata, m.created_at,
                       bm25(memories_fts) as score
                FROM memories m
                JOIN memories_fts fts ON m.rowid = fts.rowid
                WHERE memories_fts MATCH ?
                ORDER BY score
                LIMIT ?
            """, (query, limit))
            
            results = []
            for row in cursor.fetchall():
                results.append({
                    "id": row[0],
                    "content": row[1],
                    "metadata": json.loads(row[2]) if row[2] else {},
                    "created_at": row[3],
                    "score": -row[4]  # BM25 分数是负的，越小越好
                })
            
            conn.close()
            return results
            
        except Exception as e:
            logger.error(f"搜索记忆失败: {e}")
            return []
    
    def add_memory(self, content: str, metadata: Dict = None) -> Optional[str]:
        """添加新记忆"""
        if not self.memories_db.exists():
            logger.warning("memories.db 不存在")
            return None
        
        try:
            import uuid
            memory_id = str(uuid.uuid4())
            now = datetime.now().isoformat()
            metadata = metadata or {}
            
            conn = sqlite3.connect(str(self.memories_db))
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO memories (id, content, metadata, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
            """, (memory_id, content, json.dumps(metadata, ensure_ascii=False), now, now))
            conn.commit()
            conn.close()
            
            logger.info(f"添加记忆: {memory_id}")
            return memory_id
            
        except Exception as e:
            logger.error(f"添加记忆失败: {e}")
            return None
    
    # ==================== L2: 场景归纳 ====================
    
    def get_scene_blocks(self) -> List[Dict]:
        """获取所有场景块"""
        if not self.scene_blocks_dir.exists():
            return []
        
        scenes = []
        for f in self.scene_blocks_dir.glob("*.md"):
            try:
                content = f.read_text(encoding="utf-8")
                scenes.append({
                    "name": f.stem,
                    "path": str(f),
                    "content": content[:500],
                    "size": len(content)
                })
            except Exception as e:
                logger.warning(f"读取场景块失败 {f}: {e}")
        
        return scenes
    
    def get_scene_block(self, name: str) -> Optional[str]:
        """获取指定场景块内容"""
        scene_file = self.scene_blocks_dir / f"{name}.md"
        if scene_file.exists():
            return scene_file.read_text(encoding="utf-8")
        return None
    
    # ==================== L3: 用户画像 ====================
    
    def get_persona(self) -> Optional[str]:
        """获取用户画像"""
        if self.persona_file.exists():
            return self.persona_file.read_text(encoding="utf-8")
        return None
    
    def update_persona(self, content: str) -> bool:
        """更新用户画像"""
        try:
            self.persona_file.write_text(content, encoding="utf-8")
            logger.info("用户画像已更新")
            return True
        except Exception as e:
            logger.error(f"更新用户画像失败: {e}")
            return False
    
    # ==================== L0: 原始对话 ====================
    
    def get_conversations(self, limit: int = 10) -> List[Dict]:
        """获取对话列表"""
        if not self.conversations_dir.exists():
            return []
        
        conversations = []
        for f in sorted(self.conversations_dir.glob("*.jsonl"), reverse=True)[:limit]:
            try:
                # 读取最后几行
                lines = f.read_text(encoding="utf-8").strip().split("\n")
                conversations.append({
                    "file": f.name,
                    "messages": len(lines),
                    "last_message": json.loads(lines[-1]) if lines else None
                })
            except Exception as e:
                logger.warning(f"读取对话失败 {f}: {e}")
        
        return conversations
    
    # ==================== 向量搜索 ====================
    
    def vector_search(self, query_vector: List[float], top_k: int = 10) -> List[Dict]:
        """向量搜索（需要 sqlite-vec 扩展）"""
        if not self.vectors_db.exists():
            return []
        
        try:
            # 尝试加载 sqlite-vec 扩展
            conn = sqlite3.connect(str(self.vectors_db))
            
            # 查找扩展路径
            ext_path = Path.home() / ".openclaw/extensions/memory-tencentdb/node_modules/sqlite-vec-linux-x64/vec0.so"
            if ext_path.exists():
                conn.enable_load_extension(True)
                conn.load_extension(str(ext_path))
            
            # 执行向量搜索
            # 注意：实际表结构可能不同，需要根据实际情况调整
            cursor = conn.cursor()
            
            # 简单查询，返回所有向量
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = cursor.fetchall()
            
            results = []
            for table in tables:
                table_name = table[0]
                if 'vector' in table_name.lower() or 'embedding' in table_name.lower():
                    cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                    count = cursor.fetchone()[0]
                    results.append({"table": table_name, "count": count})
            
            conn.close()
            return results
            
        except Exception as e:
            logger.error(f"向量搜索失败: {e}")
            return []
    
    # ==================== 统计信息 ====================
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        stats = {
            "data_path": str(self.data_path),
            "exists": self.data_path.exists(),
            "l1_memories": 0,
            "l2_scenes": 0,
            "l3_persona": False,
            "l0_conversations": 0,
            "vectors_db_size": 0,
        }
        
        # L1 记忆数量
        if self.memories_db.exists():
            try:
                conn = sqlite3.connect(str(self.memories_db))
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM memories")
                stats["l1_memories"] = cursor.fetchone()[0]
                conn.close()
            except:
                pass
        
        # L2 场景数量
        if self.scene_blocks_dir.exists():
            stats["l2_scenes"] = len(list(self.scene_blocks_dir.glob("*.md")))
        
        # L3 画像
        stats["l3_persona"] = self.persona_file.exists()
        
        # L0 对话数量
        if self.conversations_dir.exists():
            stats["l0_conversations"] = len(list(self.conversations_dir.glob("*.jsonl")))
        
        # 向量库大小
        if self.vectors_db.exists():
            stats["vectors_db_size"] = self.vectors_db.stat().st_size
        
        return stats
    
    # ==================== 与小艺记忆系统集成 ====================
    
    def sync_to_xiaoyi_memory(self) -> Dict:
        """同步到小艺记忆系统"""
        result = {
            "synced_memories": 0,
            "synced_scenes": 0,
            "errors": []
        }
        
        try:
            # 导入小艺记忆系统（走 XiaoYiClawLLM 统一出口）
            sys.path.insert(0, str(Path(__file__).parent))
            from xiaoyi_claw_api import get_xiaoyi_claw
            
            claw = get_xiaoyi_claw()
            memory = claw.memory_v2 if hasattr(claw, 'memory_v2') else None
            if not memory:
                raise RuntimeError("XiaoyiMemoryV2 未初始化")
            
            # 同步 L1 记忆
            memories = self.get_memories(limit=100)
            for m in memories:
                try:
                    memory.store(
                        content=m["content"],
                        source="tencentdb_sync",
                        context={"original_id": m["id"], "created_at": m["created_at"]}
                    )
                    result["synced_memories"] += 1
                except Exception as e:
                    result["errors"].append(f"同步记忆失败 {m['id']}: {e}")
            
            logger.info(f"同步完成: {result['synced_memories']} 条记忆")
            
        except Exception as e:
            result["errors"].append(f"同步失败: {e}")
        
        return result


# CLI 接口
def main():
    """命令行接口"""
    import argparse
    
    parser = argparse.ArgumentParser(description="腾讯云记忆插件集成")
    parser.add_argument("command", choices=[
        "stats", "memories", "search", "scenes", "persona", "sync"
    ])
    parser.add_argument("--query", help="搜索查询")
    parser.add_argument("--limit", type=int, default=10, help="返回数量")
    
    args = parser.parse_args()
    
    tdb = TencentDBMemory()
    
    if args.command == "stats":
        stats = tdb.get_stats()
        print("腾讯云记忆插件统计:")
        print(f"  数据路径: {stats['data_path']}")
        print(f"  L1 记忆: {stats['l1_memories']} 条")
        print(f"  L2 场景: {stats['l2_scenes']} 个")
        print(f"  L3 画像: {'✅' if stats['l3_persona'] else '❌'}")
        print(f"  L0 对话: {stats['l0_conversations']} 个")
        print(f"  向量库: {stats['vectors_db_size'] / 1024 / 1024:.1f} MB")
    
    elif args.command == "memories":
        memories = tdb.get_memories(limit=args.limit)
        print(f"L1 记忆 ({len(memories)} 条):")
        for m in memories:
            print(f"  - [{m['created_at'][:10]}] {m['content'][:50]}...")
    
    elif args.command == "search":
        if not args.query:
            print("错误: 需要提供 --query")
            return
        results = tdb.search_memories(args.query, limit=args.limit)
        print(f"搜索结果 ({len(results)} 条):")
        for r in results:
            print(f"  - [{r['score']:.2f}] {r['content'][:50]}...")
    
    elif args.command == "scenes":
        scenes = tdb.get_scene_blocks()
        print(f"L2 场景 ({len(scenes)} 个):")
        for s in scenes:
            print(f"  - {s['name']} ({s['size']} 字符)")
    
    elif args.command == "persona":
        persona = tdb.get_persona()
        if persona:
            print("L3 用户画像:")
            print(persona[:500] + "..." if len(persona) > 500 else persona)
        else:
            print("用户画像不存在")
    
    elif args.command == "sync":
        result = tdb.sync_to_xiaoyi_memory()
        print(f"同步结果:")
        print(f"  同步记忆: {result['synced_memories']} 条")
        if result['errors']:
            print(f"  错误: {len(result['errors'])} 个")


if __name__ == "__main__":
    main()
