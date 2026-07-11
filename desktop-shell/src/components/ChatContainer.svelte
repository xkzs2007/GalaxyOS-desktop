<script lang="ts">
  import type { Snippet } from 'svelte';
  import { streaming, messages } from '../lib/stores';

  interface Props {
    children?: Snippet;
  }

  let { children }: Props = $props();

  let containerEl: HTMLDivElement;

  $effect(() => {
    if (containerEl && $streaming) {
      containerEl.scrollTop = containerEl.scrollHeight;
    }
  });
</script>

<div class="chat-container" bind:this={containerEl}>
  <div class="chat-messages">
    {#if children}
      {@render children()}
    {/if}
  </div>

  {#if $streaming}
    <div class="streaming-indicator">
      <span class="dot"></span>
      <span class="dot"></span>
      <span class="dot"></span>
    </div>
  {/if}
</div>

<style>
  .chat-container {
    display: flex;
    flex-direction: column;
    height: 100%;
    overflow-y: auto;
    padding: 16px;
  }

  .chat-messages {
    display: flex;
    flex-direction: column;
    gap: 4px;
    flex: 1;
  }

  .streaming-indicator {
    display: flex;
    align-items: center;
    gap: 4px;
    padding: 8px 16px;
    opacity: 0.7;
  }

  .streaming-indicator .dot {
    width: 6px;
    height: 6px;
    border-radius: 50%;
    background: var(--accent);
    animation: bounce 1.4s ease-in-out infinite;
  }

  .streaming-indicator .dot:nth-child(2) {
    animation-delay: 0.16s;
  }

  .streaming-indicator .dot:nth-child(3) {
    animation-delay: 0.32s;
  }

  @keyframes bounce {
    0%, 80%, 100% { transform: scale(0.6); opacity: 0.4; }
    40% { transform: scale(1); opacity: 1; }
  }
</style>