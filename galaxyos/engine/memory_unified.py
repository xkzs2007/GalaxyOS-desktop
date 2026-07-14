#!/usr/bin/env python3
"""
统一记忆接口（桥接层）— 已整合至 XiaoYiClawLLM

此文件保持向后兼容，所有调用桥接到 XiaoYiClawLLM 统一接口。
完整功能见 skills/galaxyos-engine/skills/llm-memory-integration/core/xiaoyi_claw_api.py
"""

import json
import sys
import os
from pathlib import Path
from typing import List, Dict, Optional
from datetime import datetime
from galaxyos.shared.paths import workspace


class UnifiedMemory:
    """统一记忆接口（桥接至 XiaoYiClawLLM）"""

    def __init__(self):
        self._claw = None

    def _get_claw(self):
        """懒加载 XiaoYiClawLLM 实例"""
        if self._claw is None:
            raise RuntimeError("xiaoyi_claw_api has been removed; use openjiuwen equivalents")
        return self._claw

    def recall(self, query: str, max_results: int = 10) -> List[Dict]:
        """统一召回（桥接至 XiaoYiClawLLM.recall）"""
        return self._get_claw().recall(query, top_k=max_results)

    def store(self, content: str, memory_type: str = "info", importance: str = "normal") -> bool:
        """统一存储（桥接至 XiaoYiClawLLM.remember）"""
        memory_id = self._get_claw().remember(
            content=content,
            metadata={
                "memory_type": memory_type,
                "importance": importance,
                "source": "memory_unified"
            },
            source="user"
        )
        return bool(memory_id)

    def classify_knowledge(self, content: str) -> Dict:
        """知识分类（桥接至 XiaoYiClawLLM.classify_knowledge）"""
        return self._get_claw().classify_knowledge(content)

    def detect_conflicts(self, new_memory: Dict) -> List[Dict]:
        """冲突检测（桥接至 XiaoYiClawLLM.learn + 简单检查）"""
        # 通过 learn 接口检测记忆冲突
        content = new_memory.get("content", "")
        memory_id = new_memory.get("memory_id", "")
        if content and memory_id:
            # 检查是否为修正
            try:
                existing = self._get_claw().recall(content, top_k=1)
                if existing:
                    return [{"content": existing[0].get("content", ""), "conflict_type": "possible_duplicate"}]
            except Exception:
                pass
        return []

    def push_notification(self, title: str, content: str, channel: str = "today-task"):
        """推送通知（保持原有实现，不桥接）"""
        try:
            if channel == "today-task":
                push_script = Path(workspace()) / "skills" / "today-task" / "scripts" / "task_push.py"
                if push_script.exists():
                    temp_json = Path("/tmp/push_data.json")
                    data = {"title": title, "content": content, "timestamp": datetime.now().isoformat()}
                    temp_json.write_text(json.dumps(data, ensure_ascii=False))
                    import subprocess
                    subprocess.run(["python3", str(push_script), "--data", str(temp_json)], capture_output=True, timeout=10)
        except Exception as e:
            print(f"推送失败: {e}")

    def health_check(self) -> Dict:
        """健康检查（桥接至 XiaoYiClawLLM.health_check）"""
        return self._get_claw().health_check()


# 统一接口函数（桥接至 XiaoYiClawLLM）
_unified_memory = None

def get_unified_memory() -> UnifiedMemory:
    """获取统一记忆接口单例"""
    global _unified_memory
    if _unified_memory is None:
        _unified_memory = UnifiedMemory()
    return _unified_memory

def recall(query: str, max_results: int = 10) -> List[Dict]:
    """统一召回（桥接至 XiaoYiClawLLM）"""
    return get_unified_memory().recall(query, max_results)

def store(content: str, memory_type: str = "info", importance: str = "normal") -> bool:
    """统一存储（桥接至 XiaoYiClawLLM）"""
    return get_unified_memory().store(content, memory_type, importance)

def health_check() -> Dict:
    """健康检查（桥接至 XiaoYiClawLLM）"""
    return get_unified_memory().health_check()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("用法: python memory_unified.py <command> [args]")
        print("命令: recall, store, health")
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "recall":
        query = sys.argv[2] if len(sys.argv) > 2 else "测试"
        results = recall(query)
        print(json.dumps(results, ensure_ascii=False, indent=2))
    elif cmd == "store":
        content = sys.argv[2] if len(sys.argv) > 2 else "测试记忆"
        success = store(content)
        print(f"存储: {'成功' if success else '失败'}")
    elif cmd == "health":
        status = health_check()
        print(json.dumps(status, ensure_ascii=False, indent=2))
