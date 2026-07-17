import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { TokUIView } from '@jboltai/tokui-react';

function isTokUIDSL(content: string): boolean {
  if (!content || typeof content !== 'string') return false;
  const trimmed = content.trim();
  return trimmed.startsWith('[') && trimmed.endsWith(']') && trimmed.length > 2;
}

interface TokUIChatRendererProps {
  workspaceId: string;
  sseEndpoint?: string;
  content: string;
  onDSLRendered?: (dsl: string, componentType: string) => void;
  onDSLFailed?: (dsl: string, error: Error) => void;
  theme?: string;
}

const SafeTokUIView: React.FC<{ dsl: string; theme?: string }> = ({ dsl, theme }) => {
  const [error, setError] = useState<Error | null>(null);

  if (error) {
    return (
      <div
        className="tokui-unknown"
        style={{
          padding: '8px 12px',
          background: '#f5f5f5',
          borderRadius: '4px',
          fontFamily: 'monospace',
          fontSize: '13px',
          whiteSpace: 'pre-wrap',
          wordBreak: 'break-all',
          color: '#666',
          border: '1px dashed #ccc',
        }}
      >
        <div style={{ color: '#e53e3e', marginBottom: '4px', fontSize: '12px' }}>
          TokUI 渲染异常，显示原始 DSL：
        </div>
        {dsl}
      </div>
    );
  }

  try {
    return <TokUIView dsl={dsl} theme={theme} />;
  } catch (e: any) {
    setError(e);
    return null;
  }
};

export const TokUIChatRenderer: React.FC<TokUIChatRendererProps> = ({
  workspaceId,
  sseEndpoint,
  content,
  onDSLRendered,
  onDSLFailed,
  theme = 'default',
}) => {
  const [streamDsl, setStreamDsl] = useState<string>('');
  const [isStreaming, setIsStreaming] = useState(false);
  const eventSourceRef = useRef<EventSource | null>(null);
  const reconnectRef = useRef(0);

  const isDSL = useMemo(() => isTokUIDSL(content), [content]);

  const connectSSE = useCallback(() => {
    if (!sseEndpoint || !workspaceId) return;

    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }

    const url = `${sseEndpoint}?workspaceId=${encodeURIComponent(workspaceId)}`;
    const es = new EventSource(url);
    eventSourceRef.current = es;
    setIsStreaming(true);

    es.addEventListener('tokui_chunk', (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data);
        if (data.workspaceId && data.workspaceId !== workspaceId) return;

        setStreamDsl((prev) => prev + (data.dsl || ''));

        if (data.isFinal) {
          es.close();
          eventSourceRef.current = null;
          setIsStreaming(false);
          reconnectRef.current = 0;
        }

        onDSLRendered?.(data.dsl || '', data.componentType || '');
      } catch {
        // ignore parse errors for individual chunks
      }
    });

    es.addEventListener('tokui_error', (event: MessageEvent) => {
      try {
        const data = JSON.parse(event.data);
        onDSLFailed?.(data.error || 'Unknown SSE error', new Error(data.error));
      } catch {
        // ignore
      }
    });

    es.onerror = () => {
      es.close();
      eventSourceRef.current = null;
      setIsStreaming(false);

      if (reconnectRef.current < 3) {
        reconnectRef.current += 1;
        const delay = Math.pow(2, reconnectRef.current) * 1000;
        setTimeout(connectSSE, delay);
      }
    };
  }, [sseEndpoint, workspaceId, onDSLRendered, onDSLFailed]);

  useEffect(() => {
    return () => {
      if (eventSourceRef.current) {
        eventSourceRef.current.close();
        eventSourceRef.current = null;
      }
    };
  }, []);

  if (isStreaming && streamDsl) {
    return (
      <div className="tokui-chat-stream">
        <SafeTokUIView dsl={streamDsl} theme={theme} />
      </div>
    );
  }

  if (isDSL) {
    return (
      <div className="tokui-chat-message">
        <SafeTokUIView dsl={content} theme={theme} />
      </div>
    );
  }

  return null;
};

export { isTokUIDSL, SafeTokUIView };
export default TokUIChatRenderer;