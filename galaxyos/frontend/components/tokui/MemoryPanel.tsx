import React, { useState } from 'react';

interface MemoryPanelProps {
  engramCount: number;
  neuralCount: number;
  synapseCount: number;
  consolidationStatus: 'idle' | 'active' | 'completed';
  onSearch?: (query: string) => void;
  onFilter?: (filterType: string) => void;
}

const MemoryPanel: React.FC<MemoryPanelProps> = ({
  engramCount,
  neuralCount,
  synapseCount,
  consolidationStatus,
  onSearch,
  onFilter,
}) => {
  const [searchQuery, setSearchQuery] = useState('');
  const [activeFilter, setActiveFilter] = useState('all');

  const total = engramCount + neuralCount + synapseCount;
  const maxCount = Math.max(engramCount, neuralCount, synapseCount, 1);

  const statusColor = {
    idle: '#9e9e9e',
    active: '#4caf50',
    completed: '#2e7d32',
  };

  const handleSearch = () => {
    if (onSearch && searchQuery.trim()) {
      onSearch(searchQuery.trim());
    }
  };

  const handleFilter = (type: string) => {
    setActiveFilter(type);
    onFilter?.(type);
  };

  return (
    <div className="tokui-memory-panel" style={{ padding: '12px', background: '#fafafa', borderRadius: '8px', border: '1px solid #e0e0e0' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
        <span style={{ fontWeight: 600, fontSize: '14px' }}>液态神经记忆</span>
        <span style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '12px', color: statusColor[consolidationStatus] }}>
          <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: statusColor[consolidationStatus], display: 'inline-block' }} />
          {consolidationStatus === 'active' ? '巩固中' : consolidationStatus === 'completed' ? '已巩固' : '空闲'}
        </span>
      </div>

      <div style={{ display: 'flex', gap: '8px', marginBottom: '8px' }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: '11px', color: '#888', marginBottom: '2px' }}>Engram</div>
          <div style={{ background: '#e0e0e0', borderRadius: '4px', height: '6px' }}>
            <div style={{ background: '#1976d2', borderRadius: '4px', height: '6px', width: `${(engramCount / maxCount) * 100}%`, transition: 'width 0.3s' }} />
          </div>
          <div style={{ fontSize: '12px', color: '#333', marginTop: '2px' }}>{engramCount}</div>
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: '11px', color: '#888', marginBottom: '2px' }}>Neural</div>
          <div style={{ background: '#e0e0e0', borderRadius: '4px', height: '6px' }}>
            <div style={{ background: '#388e3c', borderRadius: '4px', height: '6px', width: `${(neuralCount / maxCount) * 100}%`, transition: 'width 0.3s' }} />
          </div>
          <div style={{ fontSize: '12px', color: '#333', marginTop: '2px' }}>{neuralCount}</div>
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: '11px', color: '#888', marginBottom: '2px' }}>Synapse</div>
          <div style={{ background: '#e0e0e0', borderRadius: '4px', height: '6px' }}>
            <div style={{ background: '#f57c00', borderRadius: '4px', height: '6px', width: `${(synapseCount / maxCount) * 100}%`, transition: 'width 0.3s' }} />
          </div>
          <div style={{ fontSize: '12px', color: '#333', marginTop: '2px' }}>{synapseCount}</div>
        </div>
      </div>

      <div style={{ display: 'flex', gap: '4px', marginBottom: '6px' }}>
        {['all', 'engram', 'neural', 'synapse'].map((type) => (
          <button
            key={type}
            onClick={() => handleFilter(type)}
            style={{
              padding: '2px 8px',
              fontSize: '11px',
              borderRadius: '12px',
              border: activeFilter === type ? '1px solid #1976d2' : '1px solid #ccc',
              background: activeFilter === type ? '#e3f2fd' : '#fff',
              color: activeFilter === type ? '#1976d2' : '#666',
              cursor: 'pointer',
            }}
          >
            {type === 'all' ? '全部' : type.charAt(0).toUpperCase() + type.slice(1)}
          </button>
        ))}
      </div>

      <div style={{ display: 'flex', gap: '4px' }}>
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
          placeholder="搜索记忆..."
          style={{
            flex: 1,
            padding: '4px 8px',
            fontSize: '12px',
            border: '1px solid #ccc',
            borderRadius: '4px',
            outline: 'none',
          }}
        />
        <button
          onClick={handleSearch}
          style={{
            padding: '4px 10px',
            fontSize: '12px',
            background: '#1976d2',
            color: '#fff',
            border: 'none',
            borderRadius: '4px',
            cursor: 'pointer',
          }}
        >
          搜索
        </button>
      </div>

      <div style={{ fontSize: '11px', color: '#888', marginTop: '6px' }}>
        共 {total} 条记忆
      </div>
    </div>
  );
};

export default MemoryPanel;