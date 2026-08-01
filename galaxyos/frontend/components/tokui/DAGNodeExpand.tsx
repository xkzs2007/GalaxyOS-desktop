import React, { useState } from 'react';
import { useTranslation } from 'react-i18next';

interface DAGNodeExpandProps {
  nodeId: string;
  nodeContent: string;
  children?: Array<{ id: string; content: string; importance: number }>;
  summary?: string;
  onExpand?: (nodeId: string) => void;
  onCollapse?: (nodeId: string) => void;
  onSummary?: (nodeId: string) => void;
}

const DAGNodeExpand: React.FC<DAGNodeExpandProps> = ({
  nodeId,
  nodeContent,
  children = [],
  summary,
  onExpand,
  onCollapse,
  onSummary,
}) => {
  const { t } = useTranslation('cognitive-panel');
  const [expanded, setExpanded] = useState(false);

  const toggle = () => {
    setExpanded(!expanded);
    if (!expanded) {
      onExpand?.(nodeId);
    } else {
      onCollapse?.(nodeId);
    }
  };

  return (
    <div className="tokui-dag-node-expand" style={{ padding: '10px', background: '#fafafa', borderRadius: '6px', border: '1px solid #e0e0e0' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '6px' }}>
        <span style={{ fontSize: '12px', color: '#888' }}>{t('node', { defaultValue: '节点' })}: {nodeId}</span>
        <div style={{ display: 'flex', gap: '4px' }}>
          <button
            onClick={toggle}
            style={{ fontSize: '11px', padding: '2px 6px', background: '#e3f2fd', border: '1px solid #1976d2', borderRadius: '3px', cursor: 'pointer', color: '#1976d2' }}
          >
            {expanded ? t('collapse', { defaultValue: '折叠' }) : t('expand', { defaultValue: '展开' })}
          </button>
          {summary && (
            <button
              onClick={() => onSummary?.(nodeId)}
              style={{ fontSize: '11px', padding: '2px 6px', background: '#f3e5f5', border: '1px solid #7b1fa2', borderRadius: '3px', cursor: 'pointer', color: '#7b1fa2' }}
            >
              {t('summary', { defaultValue: '摘要' })}
            </button>
          )}
        </div>
      </div>

      <div style={{ fontSize: '13px', color: '#333', lineHeight: '1.5', marginBottom: '6px' }}>
        {nodeContent}
      </div>

      {expanded && children.length > 0 && (
        <div style={{ marginLeft: '12px', borderLeft: '2px solid #e0e0e0', paddingLeft: '8px' }}>
          {children.map((child) => (
            <div key={child.id} style={{ padding: '4px 0', fontSize: '12px', color: '#555' }}>
              <span style={{ display: 'inline-block', width: '6px', height: '6px', borderRadius: '50%', background: child.importance >= 1.2 ? '#f57c00' : '#9e9e9e', marginRight: '4px' }} />
              {child.content.slice(0, 60)}
            </div>
          ))}
        </div>
      )}

      {expanded && summary && (
        <div style={{ marginTop: '6px', padding: '6px 8px', background: '#f3e5f5', borderRadius: '4px', fontSize: '12px', color: '#555' }}>
          <strong>{t('summary', { defaultValue: '摘要' })}:</strong> {summary}
        </div>
      )}
    </div>
  );
};

export default DAGNodeExpand;