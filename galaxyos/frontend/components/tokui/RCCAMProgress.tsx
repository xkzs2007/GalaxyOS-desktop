import React from 'react';
import { useTranslation } from 'react-i18next';

const RCCAM_STAGES = ['Retrieval', 'Cognition', 'Control', 'Action', 'Memory'] as const;
type RCCAMStage = typeof RCCAM_STAGES[number];

interface RCCAMProgressProps {
  currentStage: RCCAMStage | 'idle';
  stagesCompleted: number;
  totalStages: number;
  stageDetails?: string;
  onPause?: () => void;
  onResume?: () => void;
  onDepthChange?: (depth: number) => void;
  onStrategyChange?: (strategy: string) => void;
}

const stageIndex: Record<string, number> = {
  idle: -1,
  retrieval: 0,
  cognition: 1,
  control: 2,
  action: 3,
  memory: 4,
};

const stageColors = ['#1976d2', '#388e3c', '#f57c00', '#7b1fa2', '#c62828'];

const RCCAMProgress: React.FC<RCCAMProgressProps> = ({
  currentStage,
  stagesCompleted,
  totalStages,
  stageDetails,
  onPause,
  onResume,
  onDepthChange,
  onStrategyChange,
}) => {
  const { t } = useTranslation('cognitive-panel');
  const currentIndex = stageIndex[currentStage.toLowerCase()] ?? -1;
  const isRunning = currentIndex >= 0;

  return (
    <div className="tokui-rccam-progress" style={{ padding: '12px', background: '#fafafa', borderRadius: '8px', border: '1px solid #e0e0e0' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
        <span style={{ fontWeight: 600, fontSize: '14px' }}>{t('rccam_progress')}</span>
        <div style={{ display: 'flex', gap: '4px' }}>
          {isRunning ? (
            <button onClick={onPause} style={{ padding: '2px 8px', fontSize: '11px', background: '#fff3e0', border: '1px solid #f57c00', borderRadius: '4px', cursor: 'pointer', color: '#f57c00' }}>
              {t('pause', { defaultValue: '暂停' })}
            </button>
          ) : (
            <button onClick={onResume} style={{ padding: '2px 8px', fontSize: '11px', background: '#e8f5e9', border: '1px solid #388e3c', borderRadius: '4px', cursor: 'pointer', color: '#388e3c' }}>
              {t('resume', { defaultValue: '继续' })}
            </button>
          )}
        </div>
      </div>

      <div style={{ display: 'flex', gap: '2px', marginBottom: '8px' }}>
        {RCCAM_STAGES.map((stage, i) => {
          const isActive = i === currentIndex;
          const isCompleted = i < currentIndex;
          return (
            <div key={stage} style={{ flex: 1, textAlign: 'center' }}>
              <div
                style={{
                  height: '6px',
                  borderRadius: '3px',
                  background: isCompleted ? stageColors[i] : isActive ? stageColors[i] : '#e0e0e0',
                  opacity: isCompleted ? 1 : isActive ? 0.7 : 0.3,
                  transition: 'all 0.3s',
                }}
              />
              <div style={{ fontSize: '10px', color: isActive ? stageColors[i] : '#888', marginTop: '2px', fontWeight: isActive ? 600 : 400 }}>
                {stage.slice(0, 3)}
              </div>
            </div>
          );
        })}
      </div>

      <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
        <div style={{ flex: 1 }}>
          <div style={{ fontSize: '11px', color: '#888', marginBottom: '2px' }}>{t('retrieval_depth', { defaultValue: '检索深度' })}</div>
          <input
            type="range"
            min={1}
            max={5}
            defaultValue={3}
            onChange={(e) => onDepthChange?.(Number(e.target.value))}
            style={{ width: '100%', height: '4px' }}
          />
        </div>
        <div>
          <div style={{ fontSize: '11px', color: '#888', marginBottom: '2px' }}>{t('strategy', { defaultValue: '策略' })}</div>
          <select
            onChange={(e) => onStrategyChange?.(e.target.value)}
            style={{ fontSize: '11px', padding: '2px 4px', border: '1px solid #ccc', borderRadius: '4px' }}
          >
            <option value="direct_reply">{t('strategy_direct', { defaultValue: '直接回复' })}</option>
            <option value="deep_analysis">{t('strategy_deep', { defaultValue: '深度分析' })}</option>
            <option value="creative">{t('strategy_creative', { defaultValue: '创意模式' })}</option>
          </select>
        </div>
      </div>

      {stageDetails && (
        <div style={{ fontSize: '11px', color: '#666', marginTop: '6px', padding: '4px 6px', background: '#f0f0f0', borderRadius: '4px' }}>
          {stageDetails}
        </div>
      )}

      <div style={{ fontSize: '11px', color: '#888', marginTop: '4px' }}>
        {t('progress', { defaultValue: '进度' })}: {stagesCompleted}/{totalStages}
      </div>
    </div>
  );
};

export default RCCAMProgress;