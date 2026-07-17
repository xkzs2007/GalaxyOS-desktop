import React, { useState, useEffect } from 'react';
import { useTranslation } from 'react-i18next';
import MemoryPanel from './tokui/MemoryPanel';
import RCCAMProgress from './tokui/RCCAMProgress';
import DAGTree from './tokui/DAGTree';
import { TokUIThemeBridge } from './tokui/TokUIThemeBridge';

interface CognitivePanelState {
  rccam: {
    currentStage: string;
    stagesCompleted: number;
    totalStages: number;
    isRunning: boolean;
    strategy: string;
    depth: number;
  };
  memory: {
    engramCount: number;
    neuralCount: number;
    synapseCount: number;
    consolidationStatus: 'idle' | 'active' | 'completed';
  };
  dag: {
    totalNodes: number;
    sessions: number;
  };
  workspaceId: string;
}

interface CognitivePanelProps {
  workspaceId: string;
  position?: 'sidebar' | 'inline' | 'floating';
  defaultOpen?: boolean;
  onMemorySearch?: (query: string) => void;
  onRCCAMPause?: () => void;
  onRCCAMResume?: () => void;
  onRCCAMDepthChange?: (depth: number) => void;
  onRCCAMStrategyChange?: (strategy: string) => void;
  onDAGExpand?: (nodeId: string) => void;
  onDAGCollapse?: (nodeId: string) => void;
  onDAGSummary?: (nodeId: string) => void;
}

const STORAGE_KEY = 'galaxyos-cognitive-panel';

function loadPanelState(): { isOpen: boolean; position: string } {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored) return JSON.parse(stored);
  } catch {}
  return { isOpen: false, position: 'sidebar' };
}

function savePanelState(state: { isOpen: boolean; position: string }): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(state));
  } catch {}
}

const CognitivePanel: React.FC<CognitivePanelProps> = ({
  workspaceId,
  position = 'sidebar',
  defaultOpen = false,
  onMemorySearch,
  onRCCAMPause,
  onRCCAMResume,
  onRCCAMDepthChange,
  onRCCAMStrategyChange,
  onDAGExpand,
  onDAGCollapse,
  onDAGSummary,
}) => {
  const { t } = useTranslation('cognitive-panel');
  const savedState = loadPanelState();
  const [isOpen, setIsOpen] = useState(defaultOpen || savedState.isOpen);
  const [panelPosition, setPanelPosition] = useState(position || savedState.position);
  const [state, setState] = useState<CognitivePanelState>({
    rccam: { currentStage: 'idle', stagesCompleted: 0, totalStages: 5, isRunning: false, strategy: 'direct_reply', depth: 3 },
    memory: { engramCount: 0, neuralCount: 0, synapseCount: 0, consolidationStatus: 'idle' },
    dag: { totalNodes: 0, sessions: 0 },
    workspaceId,
  });

  useEffect(() => {
    savePanelState({ isOpen, position: panelPosition });
  }, [isOpen, panelPosition]);

  const togglePanel = () => setIsOpen(!isOpen);

  const positionStyles: Record<string, React.CSSProperties> = {
    sidebar: {
      position: 'fixed',
      right: 0,
      top: 0,
      bottom: 0,
      width: '320px',
      background: '#fff',
      borderLeft: '1px solid #e0e0e0',
      overflowY: 'auto',
      zIndex: 1000,
      boxShadow: '-2px 0 8px rgba(0,0,0,0.08)',
      transition: 'transform 0.3s ease',
      transform: isOpen ? 'translateX(0)' : 'translateX(100%)',
    },
    inline: {
      width: '100%',
      maxWidth: '600px',
      background: '#fff',
      border: '1px solid #e0e0e0',
      borderRadius: '8px',
      overflowY: 'auto',
      maxHeight: '400px',
    },
    floating: {
      position: 'fixed',
      right: '16px',
      bottom: '16px',
      width: '360px',
      maxHeight: '500px',
      background: '#fff',
      border: '1px solid #e0e0e0',
      borderRadius: '8px',
      overflowY: 'auto',
      zIndex: 1000,
      boxShadow: '0 4px 16px rgba(0,0,0,0.12)',
    },
  };

  return (
    <>
      <button
        onClick={togglePanel}
        style={{
          position: panelPosition === 'floating' ? 'fixed' : 'relative',
          right: panelPosition === 'floating' ? 16 : undefined,
          bottom: panelPosition === 'floating' ? 16 : undefined,
          padding: '6px 12px',
          fontSize: '12px',
          background: isOpen ? '#e3f2fd' : '#fff',
          border: '1px solid #1976d2',
          borderRadius: '4px',
          cursor: 'pointer',
          color: '#1976d2',
          zIndex: 1001,
        }}
      >
        {isOpen ? t('close_panel') : t('title')}
      </button>

      {isOpen && (
        <div style={positionStyles[panelPosition] || positionStyles.sidebar}>
          <div style={{ padding: '12px', borderBottom: '1px solid #eee', display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <span style={{ fontWeight: 600, fontSize: '15px' }}>{t('title')}</span>
            <div style={{ display: 'flex', gap: '4px' }}>
              {(['sidebar', 'inline', 'floating'] as const).map((pos) => (
                <button
                  key={pos}
                  onClick={() => setPanelPosition(pos)}
                  style={{
                    padding: '2px 6px',
                    fontSize: '10px',
                    border: panelPosition === pos ? '1px solid #1976d2' : '1px solid #ccc',
                    background: panelPosition === pos ? '#e3f2fd' : '#fff',
                    borderRadius: '3px',
                    cursor: 'pointer',
                  }}
                >
                  {pos === 'sidebar' ? t('layout_sidebar') : pos === 'inline' ? t('layout_embedded') : t('layout_floating')}
                </button>
              ))}
            </div>
          </div>

          <div style={{ padding: '8px', display: 'flex', flexDirection: 'column', gap: '8px' }}>
            <RCCAMProgress
              currentStage={state.rccam.currentStage as any}
              stagesCompleted={state.rccam.stagesCompleted}
              totalStages={state.rccam.totalStages}
              onPause={onRCCAMPause}
              onResume={onRCCAMResume}
              onDepthChange={onRCCAMDepthChange}
              onStrategyChange={onRCCAMStrategyChange}
            />

            <MemoryPanel
              engramCount={state.memory.engramCount}
              neuralCount={state.memory.neuralCount}
              synapseCount={state.memory.synapseCount}
              consolidationStatus={state.memory.consolidationStatus}
              onSearch={onMemorySearch}
            />

            <DAGTree
              onExpand={onDAGExpand}
              onCollapse={onDAGCollapse}
              onSummary={onDAGSummary}
            />
          </div>
        </div>
      )}
    </>
  );
};

export default CognitivePanel;