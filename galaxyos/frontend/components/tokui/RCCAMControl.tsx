import React from 'react';
import { useTranslation } from 'react-i18next';

interface RCCAMControlProps {
  isRunning: boolean;
  currentStrategy?: string;
  retrievalDepth?: number;
  onPause?: () => void;
  onResume?: () => void;
  onDepthChange?: (depth: number) => void;
  onStrategyChange?: (strategy: string) => void;
}

const RCCAMControl: React.FC<RCCAMControlProps> = ({
  isRunning,
  currentStrategy = 'direct_reply',
  retrievalDepth = 3,
  onPause,
  onResume,
  onDepthChange,
  onStrategyChange,
}) => {
  const { t } = useTranslation('cognitive-panel');
  return (
    <div className="tokui-rccam-control" style={{ padding: '12px', background: '#fafafa', borderRadius: '8px', border: '1px solid #e0e0e0' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '8px' }}>
        <span style={{ fontWeight: 600, fontSize: '14px' }}>{t('rccam_control', { defaultValue: 'R-CCAM 控制' })}</span>
        <span style={{ display: 'flex', alignItems: 'center', gap: '4px', fontSize: '12px', color: isRunning ? '#388e3c' : '#9e9e9e' }}>
          <span style={{ width: '8px', height: '8px', borderRadius: '50%', background: isRunning ? '#4caf50' : '#9e9e9e', display: 'inline-block' }} />
          {isRunning ? t('running', { defaultValue: '运行中' }) : t('stopped', { defaultValue: '已停止' })}
        </span>
      </div>

      <div style={{ display: 'flex', gap: '6px', marginBottom: '8px' }}>
        {isRunning ? (
          <button onClick={onPause} style={{ flex: 1, padding: '4px 8px', fontSize: '12px', background: '#fff3e0', border: '1px solid #f57c00', borderRadius: '4px', cursor: 'pointer', color: '#f57c00' }}>
            {t('pause', { defaultValue: '暂停' })}
          </button>
        ) : (
          <button onClick={onResume} style={{ flex: 1, padding: '4px 8px', fontSize: '12px', background: '#e8f5e9', border: '1px solid #388e3c', borderRadius: '4px', cursor: 'pointer', color: '#388e3c' }}>
            {t('resume', { defaultValue: '继续' })}
          </button>
        )}
      </div>

      <div style={{ marginBottom: '8px' }}>
        <div style={{ fontSize: '11px', color: '#888', marginBottom: '2px' }}>{t('retrieval_depth', { defaultValue: '检索深度' })}: {retrievalDepth}</div>
        <input
          type="range"
          min={1}
          max={5}
          value={retrievalDepth}
          onChange={(e) => onDepthChange?.(Number(e.target.value))}
          style={{ width: '100%', height: '4px' }}
        />
      </div>

      <div>
        <div style={{ fontSize: '11px', color: '#888', marginBottom: '2px' }}>{t('cognitive_strategy', { defaultValue: '认知策略' })}</div>
        <select
          value={currentStrategy}
          onChange={(e) => onStrategyChange?.(e.target.value)}
          style={{ width: '100%', fontSize: '12px', padding: '4px 6px', border: '1px solid #ccc', borderRadius: '4px' }}
        >
          <option value="direct_reply">{t('strategy_direct', { defaultValue: '直接回复' })}</option>
          <option value="deep_analysis">{t('strategy_deep', { defaultValue: '深度分析' })}</option>
          <option value="creative">{t('strategy_creative', { defaultValue: '创意模式' })}</option>
        </select>
      </div>
    </div>
  );
};

export default RCCAMControl;