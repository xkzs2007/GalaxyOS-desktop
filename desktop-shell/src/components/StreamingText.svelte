<script lang="ts">
  interface Props {
    text: string;
    speed?: number;
  }

  let { text, speed = 30 }: Props = $props();

  let displayed = $state('');
  let cursor = $state(0);
  let rafId = 0;
  let lastTime = 0;

  $effect(() => {
    cursor = 0;
    displayed = '';
    lastTime = 0;

    function tick(now: number) {
      if (!lastTime) lastTime = now;
      const elapsed = now - lastTime;

      if (elapsed >= speed) {
        const steps = Math.floor(elapsed / speed);
        const nextCursor = Math.min(cursor + steps, text.length);
        if (nextCursor !== cursor) {
          cursor = nextCursor;
          displayed = Array.from(text).slice(0, cursor).join('');
        }
        lastTime = now - (elapsed % speed);
      }

      if (cursor < text.length) {
        rafId = requestAnimationFrame(tick);
      }
    }

    rafId = requestAnimationFrame(tick);

    return () => {
      if (rafId) cancelAnimationFrame(rafId);
    };
  });

  let isTyping = $derived(cursor < text.length);
</script>

<span class="streaming-text">
  {displayed}{#if isTyping}<span class="cursor"></span>{/if}
</span>

<style>
  .streaming-text {
    display: inline;
  }

  .cursor {
    display: inline-block;
    width: 2px;
    height: 1em;
    background: var(--accent);
    margin-left: 1px;
    vertical-align: text-bottom;
    animation: blink 0.8s step-end infinite;
  }

  @keyframes blink {
    0%, 100% { opacity: 1; }
    50% { opacity: 0; }
  }
</style>