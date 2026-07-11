<script lang="ts">
  import { TokUI, registerHandler } from '@jboltai/tokui';
  import { sessions, currentSessionId, streaming, messages } from './lib/stores';
  import ChatContainer from './components/ChatContainer.svelte';

  let el: HTMLDivElement;
  let ui: TokUI | null = null;
  let dslSource = '';
  let dslDisplay = $state('');
  let displayRaf = 0;

  let statusText = $derived($streaming ? 'SSE 连接中，DSL 增量渲染' : '空闲');
  let hasContent = $derived(dslDisplay.length > 0);

  function scheduleDisplay() {
    if (displayRaf) return;
    displayRaf = requestAnimationFrame(() => {
      displayRaf = 0;
      dslDisplay = dslSource;
    });
  }

  function run() {
    if (!ui || $streaming) return;
    if (el) el.innerHTML = '';
    dslSource = '';
    dslDisplay = '';
    $streaming = true;

    const token = typeof __GALAXYOS_TOKEN__ !== 'undefined' ? __GALAXYOS_TOKEN__ : '';
    ui.connect('/sse/ask', { prompt: 'demo' }, token).catch((err: Error) => {
      console.error('TokUI connect 失败:', err);
      $streaming = false;
    });
  }

  $effect(() => {
    ui = new TokUI({
      container: el,
      theme: 'modern-dark',
      onEvent: (type: string) => {
        if (type === 'streamEnd') {
          $streaming = false;
        }
      },
    });

    const origFeed = ui.feed.bind(ui);
    ui.feed = (chunk: string) => {
      dslSource += chunk;
      scheduleDisplay();
      origFeed(chunk);
    };

    registerHandler('onWelcomePick', (data: unknown) => {
      const value = typeof data === 'string' ? data : (data as Record<string, string>)?.value;
      if (value) console.log('[App] Welcome pick:', value);
    });

    return () => {
      ui?.disconnect();
      ui = null;
      if (displayRaf) cancelAnimationFrame(displayRaf);
    };
  });
</script>

<div class="app-layout">
  <header class="app-header">
    <div class="brand">
      <span class="brand-mark">GalaxyOS</span>
    </div>
    <div class="header-actions">
      <button class="btn-run" disabled={$streaming} onclick={run}>
        {$streaming ? '推送中…' : '开始对话'}
      </button>
      <span class="status" class:live={$streaming}>
        <span class="dot"></span>{statusText}
      </span>
    </div>
  </header>

  <main class="app-body">
    <div class="render-stage">
      <div class="render-area" bind:this={el}></div>
      {#if !hasContent}
        <div class="render-empty">
          <div class="empty-logo">GalaxyOS</div>
          <p class="empty-tip">点击「开始对话」，DSL 将经 SSE 流式渲染于此</p>
        </div>
      {/if}
    </div>
  </main>
</div>

<style>
  .app-layout {
    display: flex;
    flex-direction: column;
    height: 100vh;
    background: var(--bg-primary);
  }

  .app-header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 20px;
    border-bottom: 1px solid var(--border-color);
    background: var(--bg-secondary);
  }

  .brand {
    display: flex;
    align-items: center;
    gap: 8px;
  }

  .brand-mark {
    font-size: 18px;
    font-weight: 700;
    color: var(--accent);
  }

  .header-actions {
    display: flex;
    align-items: center;
    gap: 12px;
  }

  .btn-run {
    display: flex;
    align-items: center;
    gap: 6px;
    padding: 6px 16px;
    border: none;
    border-radius: 6px;
    background: var(--accent);
    color: var(--bg-primary);
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    transition: opacity 0.15s;
  }

  .btn-run:disabled {
    opacity: 0.5;
    cursor: not-allowed;
  }

  .status {
    display: flex;
    align-items: center;
    gap: 6px;
    font-size: 12px;
    color: var(--text-secondary);
  }

  .status.live {
    color: var(--success);
  }

  .dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--text-secondary);
  }

  .status.live .dot {
    background: var(--success);
    animation: pulse 1.5s ease-in-out infinite;
  }

  @keyframes pulse {
    0%, 100% { opacity: 1; }
    50% { opacity: 0.4; }
  }

  .app-body {
    flex: 1;
    overflow: hidden;
  }

  .render-stage {
    position: relative;
    height: 100%;
    overflow-y: auto;
  }

  .render-area {
    min-height: 100%;
    padding: 16px 20px;
  }

  .render-empty {
    position: absolute;
    inset: 0;
    display: flex;
    flex-direction: column;
    align-items: center;
    justify-content: center;
    gap: 12px;
    pointer-events: none;
  }

  .empty-logo {
    font-size: 48px;
    font-weight: 800;
    color: var(--border-color);
    letter-spacing: -1px;
  }

  .empty-tip {
    font-size: 14px;
    color: var(--text-secondary);
  }
</style>