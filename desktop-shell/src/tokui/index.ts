import { TokUI, registerHandler } from '@jboltai/tokui';

export { TokUI, registerHandler };

export interface ConnectOptions {
  prompt?: string;
  sessionId?: string;
  streamId?: string;
}

export interface SSEEvent {
  type: 'token' | 'error' | 'done';
  data: string;
  index: number;
}

const PROTOCOL_VERSION = '1';

function detectProtocol(eventSource: MessageEvent): string {
  const raw = typeof eventSource.data === 'string' ? eventSource.data : '';
  if (raw === '[DONE]') return PROTOCOL_VERSION;
  try {
    const parsed = JSON.parse(raw);
    if (parsed.tokui !== undefined) return PROTOCOL_VERSION;
    if (parsed.event) return 'legacy';
  } catch {
    return 'raw';
  }
  return 'unknown';
}

export async function connect(
  endpoint: string,
  options: ConnectOptions = {},
  token?: string,
): Promise<void> {
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
  };
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }

  const response = await fetch(endpoint, {
    method: 'POST',
    headers,
    body: JSON.stringify({
      prompt: options.prompt ?? '',
      session_id: options.sessionId ?? '',
      stream_id: options.streamId ?? '',
    }),
  });

  if (!response.ok) {
    throw new Error(`SSE connect failed: ${response.status} ${response.statusText}`);
  }

  if (!response.body) {
    throw new Error('SSE connect failed: no readable stream');
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let index = 0;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';

    for (const line of lines) {
      if (line.startsWith('data: ')) {
        const payload = line.slice(6).trim();
        if (payload === '[DONE]') {
          return;
        }
        try {
          const parsed = JSON.parse(payload);
          if (parsed.tokui !== undefined) {
            index++;
          }
        } catch {
          // raw text chunk
          index++;
        }
      }
    }
  }
}

export function createSSEConnection(
  endpoint: string,
  token: string,
  onToken: (chunk: string, index: number) => void,
  onError: (message: string) => void,
  onDone: () => void,
): EventSource {
  const url = new URL(endpoint, window.location.origin);
  const es = new EventSource(url.toString());

  es.addEventListener('token', (e: MessageEvent) => {
    const chunk = typeof e.data === 'string' ? e.data : '';
    const idx = Number(e.lastEventId) || 0;
    onToken(chunk, idx);
  });

  es.addEventListener('error', (e: MessageEvent) => {
    const msg = typeof e.data === 'string' ? e.data : 'SSE error';
    onError(msg);
  });

  es.addEventListener('done', () => {
    onDone();
    es.close();
  });

  es.onerror = () => {
    onError('SSE connection lost');
    es.close();
  };

  return es;
}