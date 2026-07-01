#!/usr/bin/env python3
"""
自主任务集成器 (Autonomous Tasks Integrator)

整合自主任务系统：
- autonomous-tasks
- hz-proactive-agent
- proactive-tasks
- natural-language-planner

Author: 小艺 Claw
Version: 1.0.0
Created: 2026-04-21
"""

import os
import sys
import json
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Any
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class AutonomousTasksIntegrator:
    """
    自主任务集成器
    
    整合多个自主任务相关技能。
    """
    
    def __init__(self, workspace_path: str = None):
        self.workspace_path = Path(workspace_path or 
            os.path.expanduser("~/.openclaw/workspace"))
        
        # 技能路径
        self.skills_dir = self.workspace_path / "skills"
        self.proactive_tasks_dir = self.skills_dir / "proactive-tasks"
        self.autonomous_tasks_dir = self.skills_dir / "autonomous-tasks"
        self.hz_proactive_dir = self.skills_dir / "hz-proactive-agent"
        self.nl_planner_dir = self.skills_dir / "natural-language-planner"
        self.today_task_dir = self.skills_dir / "today-task"
        
        logger.info("自主任务集成器初始化完成")
    
    # ==================== Proactive Tasks ====================
    
    def get_next_proactive_task(self) -> Optional[Dict]:
        """获取下一个主动任务"""
        task_manager = self.proactive_tasks_dir / "scripts" / "task_manager.py"
        
        if not task_manager.exists():
            logger.warning("task_manager.py 不存在")
            return None
        
        try:
            result = subprocess.run(
                ["python3", str(task_manager), "next-task"],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(self.proactive_tasks_dir)
            )
            
            if result.returncode == 0 and result.stdout.strip():
                return json.loads(result.stdout)
            
        except Exception as e:
            logger.warning(f"获取主动任务失败: {e}")
        
        return None
    
    def complete_proactive_task(self, task_id: str, result: str) -> bool:
        """完成主动任务"""
        task_manager = self.proactive_tasks_dir / "scripts" / "task_manager.py"
        
        if not task_manager.exists():
            return False
        
        try:
            proc_result = subprocess.run(
                ["python3", str(task_manager), "complete-task", task_id, "--result", result],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(self.proactive_tasks_dir)
            )
            
            return proc_result.returncode == 0
            
        except Exception as e:
            logger.warning(f"完成任务失败: {e}")
            return False
    
    # ==================== Today Task Push ====================
    
    def push_to_hiboard(self, title: str, content: str) -> bool:
        """推送到负一屏"""
        push_script = self.today_task_dir / "scripts" / "task_push.py"
        
        if not push_script.exists():
            logger.warning("task_push.py 不存在")
            return False
        
        try:
            # 创建临时数据文件
            temp_file = self.today_task_dir / "temp_push.json"
            data = {
                "title": title,
                "content": content,
                "timestamp": datetime.now().isoformat()
            }
            
            with open(temp_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
            
            result = subprocess.run(
                ["python3", str(push_script), "--data", str(temp_file)],
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(self.today_task_dir)
            )
            
            # 清理临时文件
            if temp_file.exists():
                temp_file.unlink()
            
            return result.returncode == 0
            
        except Exception as e:
            logger.warning(f"推送失败: {e}")
            return False
    
    # ==================== Natural Language Planner ====================
    
    def start_kanban_dashboard(self, port: int = 8080) -> Optional[subprocess.Popen]:
        """启动 Kanban 仪表板"""
        dashboard_script = self.nl_planner_dir / "scripts" / "dashboard_server.py"
        
        if not dashboard_script.exists():
            logger.warning("dashboard_server.py 不存在")
            return None
        
        try:
            process = subprocess.Popen(
                ["python3", str(dashboard_script), "--port", str(port)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=str(self.nl_planner_dir)
            )
            
            logger.info(f"Kanban 仪表板已启动: http://localhost:{port}")
            return process
            
        except Exception as e:
            logger.warning(f"启动仪表板失败: {e}")
            return None
    
    # ==================== Git Notes Memory ====================
    
    def get_git_notes_memory(self) -> List[Dict]:
        """获取 Git 笔记记忆"""
        git_memory = self.skills_dir / "git-notes-memory" / "memory.py"
        
        if not git_memory.exists():
            return []
        
        try:
            # 导入并调用
            sys.path.insert(0, str(git_memory.parent))
            from memory import GitNotesMemory
            
            memory = GitNotesMemory()
            return memory.get_all_notes()
            
        except Exception as e:
            logger.warning(f"获取 Git 笔记失败: {e}")
            return []
    
    # ==================== Brain 知识库 ====================
    
    def get_brain_entries(self, category: str = None) -> List[Dict]:
        """获取 Brain 知识库条目"""
        brain_dir = self.workspace_path / "brain"
        
        if not brain_dir.exists():
            return []
        
        entries = []
        
        # 遍历所有分类
        categories = [category] if category else ["people", "orgs", "ideas", "tech", "places", "events", "games", "media"]
        
        for cat in categories:
            cat_dir = brain_dir / cat
            if not cat_dir.exists():
                continue
            
            for f in cat_dir.glob("*.md"):
                try:
                    content = f.read_text(encoding="utf-8")
                    entries.append({
                        "category": cat,
                        "name": f.stem,
                        "path": str(f),
                        "content": content[:500],
                        "size": len(content)
                    })
                except Exception as e:
                    logger.warning(f"读取 {f} 失败: {e}")
        
        return entries
    
    # ==================== 统一接口 ====================
    
    def get_autonomous_status(self) -> Dict:
        """获取自主任务状态"""
        status = {
            "proactive_tasks": {
                "available": self.proactive_tasks_dir.exists(),
                "next_task": None
            },
            "autonomous_tasks": {
                "available": self.autonomous_tasks_dir.exists()
            },
            "hz_proactive": {
                "available": self.hz_proactive_dir.exists()
            },
            "nl_planner": {
                "available": self.nl_planner_dir.exists()
            },
            "today_task": {
                "available": self.today_task_dir.exists()
            },
            "brain_entries": 0,
            "git_notes": 0
        }
        
        # 获取下一个任务
        if status["proactive_tasks"]["available"]:
            status["proactive_tasks"]["next_task"] = self.get_next_proactive_task()
        
        # 统计 Brain 条目
        status["brain_entries"] = len(self.get_brain_entries())
        
        return status
    
    def run_heartbeat_tasks(self) -> Dict:
        """运行心跳任务"""
        result = {
            "tasks_checked": 0,
            "tasks_executed": 0,
            "pushes_sent": 0,
            "errors": []
        }
        
        # 1. 检查主动任务
        next_task = self.get_next_proactive_task()
        result["tasks_checked"] = 1
        
        if next_task:
            # TODO: 执行任务
            result["tasks_executed"] = 1
            logger.info(f"发现待执行任务: {next_task.get('title', 'unknown')}")
        
        return result


# CLI 接口
def main():
    """命令行接口"""
    import argparse
    
    parser = argparse.ArgumentParser(description="自主任务集成器")
    parser.add_argument("command", choices=["status", "next-task", "push", "brain", "heartbeat"])
    parser.add_argument("--title", help="推送标题")
    parser.add_argument("--content", help="推送内容")
    parser.add_argument("--category", help="Brain 分类")
    
    args = parser.parse_args()
    
    integrator = AutonomousTasksIntegrator()
    
    if args.command == "status":
        status = integrator.get_autonomous_status()
        print("自主任务状态:")
        print(f"  Proactive Tasks: {'✅' if status['proactive_tasks']['available'] else '❌'}")
        print(f"  Autonomous Tasks: {'✅' if status['autonomous_tasks']['available'] else '❌'}")
        print(f"  Hz Proactive: {'✅' if status['hz_proactive']['available'] else '❌'}")
        print(f"  NL Planner: {'✅' if status['nl_planner']['available'] else '❌'}")
        print(f"  Today Task: {'✅' if status['today_task']['available'] else '❌'}")
        print(f"  Brain 条目: {status['brain_entries']}")
        
        if status['proactive_tasks']['next_task']:
            task = status['proactive_tasks']['next_task']
            print(f"\n下一个任务: {task.get('title', 'unknown')}")
    
    elif args.command == "next-task":
        task = integrator.get_next_proactive_task()
        if task:
            print(f"下一个任务: {task.get('title', 'unknown')}")
            print(f"  描述: {task.get('description', 'N/A')}")
            print(f"  优先级: {task.get('priority', 'N/A')}")
        else:
            print("没有待执行任务")
    
    elif args.command == "push":
        if not args.title or not args.content:
            print("错误: 需要提供 --title 和 --content")
            return
        
        success = integrator.push_to_hiboard(args.title, args.content)
        if success:
            print("✅ 推送成功")
        else:
            print("❌ 推送失败")
    
    elif args.command == "brain":
        entries = integrator.get_brain_entries(category=args.category)
        print(f"Brain 知识库 ({len(entries)} 条):")
        for e in entries:
            print(f"  [{e['category']}] {e['name']} ({e['size']} 字符)")
    
    elif args.command == "heartbeat":
        result = integrator.run_heartbeat_tasks()
        print(f"心跳任务执行结果:")
        print(f"  检查任务: {result['tasks_checked']}")
        print(f"  执行任务: {result['tasks_executed']}")
        if result['errors']:
            print(f"  错误: {len(result['errors'])} 个")


if __name__ == "__main__":
    main()
