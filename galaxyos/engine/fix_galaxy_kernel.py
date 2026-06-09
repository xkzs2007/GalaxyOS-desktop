#!/usr/bin/env python3
"""调查 Galaxy Kernel 引用的所有模块，输出实际可用的类和函数"""
import sys, importlib, inspect, os

SCRIPTS = '/home/sandbox/.openclaw/workspace/skills/xiaoyi-claw-omega-final/scripts'
sys.path.insert(0, SCRIPTS)

checks = {
    # (模块名, 代码中要导入的名称, 实际类/函数搜索关键词)
    'chain_of_verification': {
        'imports': ['ChainOfVerificationEngine'],
        'hooks': ['verify', 'check', 'CoVe', 'ChainOfVerification'],
    },
    'cognitive_map': {
        'imports': ['CognitiveMapBuilder'],
        'hooks': ['CognitiveMapBuilder', 'MapBuilder', 'build_cognitive'],
    },
    'graph_of_thoughts': {
        'imports': ['GoTBuilder'],
        'hooks': ['GoTBuilder', 'GraphOfThoughts', 'GoT', 'build_got'],
    },
    'causal_reasoning': {
        'imports': ['CausalReasoningEngine'],
        'hooks': ['CausalReasoningEngine', 'ReasoningEngine', 'CausalEngine'],
    },
    'plan_solve': {
        'imports': ['PlanSolveEngine'],
        'hooks': ['PlanSolveEngine', 'PlanSolve', 'PlanEngine'],
    },
    'tree_of_thought': {
        'imports': ['TreeOfThoughtEngine'],
        'hooks': ['TreeOfThoughtEngine', 'TreeOfThought', 'ToTEngine'],
    },
    'multi_path': {
        'imports': ['MultiPathEngine'],
        'hooks': ['MultiPath', 'MultiPathEngine'],
    },
    'multi_agent_debate': {
        'imports': ['MultiAgentDebate'],
        'hooks': ['MultiAgentDebate', 'AgentDebate', 'DebateEngine'],
    },
    'code_aware_reasoning': {
        'imports': ['CodeAwareEngine'],
        'hooks': ['CodeAwareEngine', 'CodeAware', 'CodeReasoning'],
    },
    'spatial_topology': {
        'imports': ['AriGraphBuilder'],
        'hooks': ['AriGraphBuilder', 'SpatialTopology', 'TopologyBuilder'],
    },
    'engine_integration': {
        'imports': ['get_engine_integration'],
        'hooks': ['EngineIntegration', 'get_engine'],
    },
}

for mod_name, info in checks.items():
    print(f"\n{'='*60}")
    print(f"模块: {mod_name}")
    print(f"{'='*60}")
    
    mod_path = os.path.join(SCRIPTS, f"{mod_name}.py")
    if not os.path.exists(mod_path):
        print(f"  ❌ 文件不存在: {mod_path}")
        continue
    print(f"  ✅ 文件存在")
    
    try:
        spec = importlib.util.spec_from_file_location(mod_name, mod_path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except Exception as e:
        print(f"  ❌ 加载失败: {e}")
        # 尝试读取文件内容看看有哪些类和函数定义
        with open(mod_path) as f:
            content = f.read()
        # 找类定义
        import re
        classes = re.findall(r'^class (\w+)', content, re.MULTILINE)
        funcs = re.findall(r'^def (\w+)', content, re.MULTILINE)
        if classes: print(f"  定义中的类: {classes}")
        if funcs: print(f"  定义中的顶层函数: {funcs}")
        continue
    
    # 列出所有公共类和函数
    members = [(n, o) for n, o in inspect.getmembers(mod) 
               if not n.startswith('_') and (inspect.isclass(o) or inspect.isfunction(o))]
    if members:
        print(f"  可导出成员:")
        for n, o in members:
            kind = 'class' if inspect.isclass(o) else 'function'
            print(f"    {kind}: {n}")
    else:
        print(f"  警告: 无公共成员")
    
    # 检查预期的导入是否可用
    for imp in info['imports']:
        if hasattr(mod, imp):
            print(f"  ✅ 预期导入 {imp} 可用")
        else:
            print(f"  ❌ 预期导入 {imp} 缺失")

# 特别检查 paper_integration 的 emotion_tracker 依赖
print(f"\n{'='*60}")
print("paper_integration 依赖检查")
from paper_integration import get_integration
pi_path = os.path.join(SCRIPTS, 'paper_integration.py')
with open(pi_path) as f:
    pi_content = f.read()
import re
imports = re.findall(r'^(?:from|import)\s+(\S+)', pi_content, re.MULTILINE)
print(f"paper_integration 导入: {imports}")
# 检查 emotion_tracker
if 'emotion_tracker' in pi_content:
    print("  ❌ 引用了 emotion_tracker")
    # 检查 emotion_tracker.py 是否存在
    et_path = os.path.join(SCRIPTS, 'emotion_tracker.py')
    print(f"  emotion_tracker.py: {'✅ 存在' if os.path.exists(et_path) else '❌ 不存在'}")
else:
    print("  ✅ 无 emotion_tracker 引用")
