import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';

interface DAGNodeData {
  id: string;
  role: string;
  content: string;
  importance: number;
  summary?: string;
  children?: DAGNodeData[];
}

interface DAGTreeProps {
  nodes?: DAGNodeData[];
  activeNodeId?: string;
  onExpand?: (nodeId: string) => void;
  onCollapse?: (nodeId: string) => void;
  onSummary?: (nodeId: string) => void;
}

const importanceColor = (imp: number): string => {
  if (imp >= 1.5) return '#c62828';
  if (imp >= 1.2) return '#f57c00';
  if (imp >= 1.0) return '#1976d2';
  return '#9e9e9e';
};

const DAGNodeItem: React.FC<{
  node: DAGNodeData;
  depth: number;
  activeNodeId?: string;
  onExpand?: (nodeId: string) => void;
  onCollapse?: (nodeId: string) => void;
  onSummary?: (nodeId: string) => void;
}> = ({ node, depth, activeNodeId, onExpand, onCollapse, onSummary }) => {
  const { t } = useTranslation('cognitive-panel');
  const [expanded, setExpanded] = useState(depth < 1);
  const hasChildren = node.children && node.children.length > 0;
  const isActive = node.id === activeNodeId;

  const toggleExpand = () => {
    setExpanded(!expanded);
    if (!expanded) {
      onExpand?.(node.id);
    } else {
      onCollapse?.(node.id);
    }
  };

  return (
    <div style={{ marginLeft: depth * 16 }}>
      <div
        style={{
          display: 'flex',
          alignItems: 'center',
          gap: '4px',
          padding: '3px 6px',
          borderRadius: '4px',
          background: isActive ? '#e3f2fd' : 'transparent',
          border: isActive ? '1px solid #1976d2' : '1px solid transparent',
          cursor: hasChildren ? 'pointer' : 'default',
          fontSize: '12px',
        }}
        onClick={hasChildren ? toggleExpand : undefined}
      >
        {hasChildren && (
          <span style={{ fontSize: '10px', color: '#888', width: '12px' }}>
            {expanded ? '▼' : '▶'}
          </span>
        )}
        {!hasChildren && <span style={{ width: '12px' }} />}
        <span
          style={{
            width: '8px',
            height: '8px',
            borderRadius: '50%',
            background: importanceColor(node.importance),
            flexShrink: 0,
          }}
        />
        <span style={{ color: '#666', fontSize: '10px', minWidth: '36px' }}>{node.role}</span>
        <span style={{ color: '#333', flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
          {node.content.slice(0, 50)}
        </span>
        {node.summary && (
          <button
            onClick={(e) => { e.stopPropagation(); onSummary?.(node.id); }}
            style={{ fontSize: '10px', padding: '1px 4px', background: '#f5f5f5', border: '1px solid #ddd', borderRadius: '3px', cursor: 'pointer' }}
          >
            {t('summary', { defaultValue: '摘要' })}
          </button>
        )}
      </div>
      {expanded && hasChildren && node.children!.map((child) => (
        <DAGNodeItem
          key={child.id}
          node={child}
          depth={depth + 1}
          activeNodeId={activeNodeId}
          onExpand={onExpand}
          onCollapse={onCollapse}
          onSummary={onSummary}
        />
      ))}
    </div>
  );
};

const DAGTree: React.FC<DAGTreeProps> = ({ nodes = [], activeNodeId, onExpand, onCollapse, onSummary }) => {
  const { t } = useTranslation('cognitive-panel');
  return (
    <div className="tokui-dag-tree" style={{ padding: '12px', background: '#fafafa', borderRadius: '8px', border: '1px solid #e0e0e0' }}>
      <div style={{ fontWeight: 600, fontSize: '14px', marginBottom: '8px' }}>{t('dag_tree')}</div>
      {nodes.length === 0 ? (
        <div style={{ fontSize: '12px', color: '#999', textAlign: 'center', padding: '12px' }}>{t('no_data')}</div>
      ) : (
        nodes.map((node) => (
          <DAGNodeItem
            key={node.id}
            node={node}
            depth={0}
            activeNodeId={activeNodeId}
            onExpand={onExpand}
            onCollapse={onCollapse}
            onSummary={onSummary}
          />
        ))
      )}
    </div>
  );
};

export default DAGTree;