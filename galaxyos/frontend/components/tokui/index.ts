import { registerHandler } from '@jboltai/tokui';
import MemoryPanel from './MemoryPanel';
import RCCAMProgress from './RCCAMProgress';
import DAGTree from './DAGTree';
import MemorySearch from './MemorySearch';
import RCCAMControl from './RCCAMControl';
import DAGNodeExpand from './DAGNodeExpand';

const COMPONENT_REGISTRY = {
  'memory-panel': {
    component: MemoryPanel,
    description: '液态神经记忆状态面板',
  },
  'rccam-progress': {
    component: RCCAMProgress,
    description: 'R-CCAM 认知循环进度条',
  },
  'dag-tree': {
    component: DAGTree,
    description: 'DAG 上下文树',
  },
  'memory-search': {
    component: MemorySearch,
    description: '记忆检索交互面板',
  },
  'rccam-control': {
    component: RCCAMControl,
    description: 'R-CCAM 控制面板',
  },
  'dag-node-expand': {
    component: DAGNodeExpand,
    description: 'DAG 节点展开组件',
  },
} as const;

export type GalaxyOSComponentType = keyof typeof COMPONENT_REGISTRY;

export function registerGalaxyOSComponents(
  renderer: { register: (type: string, component: React.ComponentType<any>) => void }
): { registered: string[]; failed: string[] } {
  const registered: string[] = [];
  const failed: string[] = [];

  for (const [type, config] of Object.entries(COMPONENT_REGISTRY)) {
    try {
      renderer.register(type, config.component as React.ComponentType<any>);
      registered.push(type);
    } catch (e) {
      console.warn(`[GalaxyOS] Failed to register TokUI component "${type}":`, e);
      failed.push(type);
    }
  }

  return { registered, failed };
}

export { MemoryPanel, RCCAMProgress, DAGTree, MemorySearch, RCCAMControl, DAGNodeExpand };
export default COMPONENT_REGISTRY;