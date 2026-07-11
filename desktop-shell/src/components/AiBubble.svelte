<script lang="ts">
  import type { Snippet } from 'svelte';

  interface Props {
    role: 'ai' | 'user';
    model?: string;
    time?: string;
    children: Snippet;
  }

  let { role, model = '', time = '', children }: Props = $props();

  let isAi = $derived(role === 'ai');
  let displayTime = $derived(time || new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }));
</script>

<div class="bubble" class:ai={isAi} class:user={!isAi}>
  <div class="bubble-header">
    {#if isAi}
      <span class="bubble-role">AI</span>
      {#if model}
        <span class="bubble-model">{model}</span>
      {/if}
    {:else}
      <span class="bubble-role">You</span>
    {/if}
    <span class="bubble-time">{displayTime}</span>
  </div>
  <div class="bubble-body">
    {@render children()}
  </div>
</div>

<style>
  .bubble {
    padding: 12px 16px;
    border-radius: 12px;
    margin-bottom: 8px;
    max-width: 85%;
  }

  .bubble.ai {
    background: var(--bg-secondary);
    border: 1px solid var(--border-color);
    align-self: flex-start;
  }

  .bubble.user {
    background: var(--accent-dim);
    color: var(--bg-primary);
    align-self: flex-end;
  }

  .bubble-header {
    display: flex;
    align-items: center;
    gap: 8px;
    margin-bottom: 6px;
    font-size: 12px;
  }

  .bubble-role {
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.5px;
  }

  .bubble.ai .bubble-role {
    color: var(--accent);
  }

  .bubble.user .bubble-role {
    color: var(--bg-primary);
  }

  .bubble-model {
    color: var(--text-secondary);
    font-size: 11px;
  }

  .bubble-time {
    margin-left: auto;
    font-size: 11px;
    opacity: 0.6;
  }

  .bubble-body {
    font-size: 14px;
    line-height: 1.6;
  }
</style>