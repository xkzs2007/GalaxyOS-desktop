import React, { useState } from 'react';

interface MemorySearchResult {
  id: string;
  content: string;
  score: number;
  type: string;
  source: string;
}

interface MemorySearchProps {
  query?: string;
  results?: MemorySearchResult[];
  onSearch?: (query: string) => void;
  onFilter?: (filterType: string) => void;
}

const MemorySearch: React.FC<MemorySearchProps> = ({
  query = '',
  results = [],
  onSearch,
  onFilter,
}) => {
  const [searchQuery, setSearchQuery] = useState(query);
  const [activeFilter, setActiveFilter] = useState('all');

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
    <div className="tokui-memory-search" style={{ padding: '12px', background: '#fafafa', borderRadius: '8px', border: '1px solid #e0e0e0' }}>
      <div style={{ fontWeight: 600, fontSize: '14px', marginBottom: '8px' }}>记忆检索</div>

      <div style={{ display: 'flex', gap: '4px', marginBottom: '8px' }}>
        <input
          type="text"
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          onKeyDown={(e) => e.key === 'Enter' && handleSearch()}
          placeholder="输入搜索关键词..."
          style={{
            flex: 1,
            padding: '6px 10px',
            fontSize: '13px',
            border: '1px solid #ccc',
            borderRadius: '4px',
            outline: 'none',
          }}
        />
        <button
          onClick={handleSearch}
          style={{
            padding: '6px 14px',
            fontSize: '13px',
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

      <div style={{ display: 'flex', gap: '4px', marginBottom: '8px' }}>
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

      {results.length > 0 && (
        <div style={{ maxHeight: '200px', overflowY: 'auto' }}>
          {results.map((result) => (
            <div
              key={result.id}
              style={{
                padding: '6px 8px',
                borderBottom: '1px solid #eee',
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
              }}
            >
              <div style={{ flex: 1, fontSize: '12px', color: '#333', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {result.content.slice(0, 80)}
              </div>
              <div style={{ display: 'flex', gap: '4px', alignItems: 'center', flexShrink: 0 }}>
                <span style={{ fontSize: '10px', padding: '1px 4px', background: '#e3f2fd', color: '#1976d2', borderRadius: '3px' }}>
                  {result.type}
                </span>
                <span style={{ fontSize: '10px', color: '#888' }}>
                  {result.score.toFixed(2)}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}

      {results.length === 0 && searchQuery && (
        <div style={{ fontSize: '12px', color: '#999', textAlign: 'center', padding: '12px' }}>
          未找到匹配的记忆
        </div>
      )}
    </div>
  );
};

export default MemorySearch;