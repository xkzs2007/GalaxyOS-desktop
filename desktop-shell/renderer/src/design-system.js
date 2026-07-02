function escapeHtml(value = '') {
  return String(value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function normalizeSteps(steps = []) {
  return steps.map((step) => {
    if (typeof step === 'string') {
      return { title: step, tone: 'neutral', meta: '' };
    }
    return {
      title: step?.title || step?.label || step?.text || '',
      tone: step?.tone || 'neutral',
      meta: step?.meta || '',
    };
  });
}

export function buildWorkbenchHeader({ title = 'GalaxyOS', subtitle = '本地执行型智能助手', status = '已连接' } = {}) {
  return `
    <section class="workspace-toolbar" role="banner">
      <div class="workspace-toolbar__brand">
        <div class="workspace-toolbar__logo">${escapeHtml(String(title).slice(0, 1))}</div>
        <div class="workspace-toolbar__copy">
          <div class="workspace-toolbar__title">${escapeHtml(title)}</div>
          <div class="workspace-toolbar__subtitle">${escapeHtml(subtitle)}</div>
        </div>
      </div>
      <div class="workspace-toolbar__meta">
        <span class="workspace-toolbar__meta-pill">本地</span>
        <span class="workspace-toolbar__status">
          <span class="workspace-toolbar__dot"></span>
          <span>${escapeHtml(status)}</span>
        </span>
      </div>
    </section>
  `;
}

export function buildWelcomeHero({ title = '你好，我是 GalaxyOS', subtitle = '把复杂任务拆成可执行步骤，在工作台里直接完成。' } = {}) {
  return `
    <section class="welcome-hero">
      <div class="welcome-hero__eyebrow">AI 工作台</div>
      <h2 class="welcome-hero__title">${escapeHtml(title)}</h2>
      <p class="welcome-hero__subtitle">${escapeHtml(subtitle)}</p>
      <div class="welcome-hero__metrics">
        <div class="welcome-hero__metric">
          <div class="welcome-hero__metric-value">3</div>
          <div class="welcome-hero__metric-label">执行阶段</div>
        </div>
        <div class="welcome-hero__metric">
          <div class="welcome-hero__metric-value">∞</div>
          <div class="welcome-hero__metric-label">记忆联想</div>
        </div>
        <div class="welcome-hero__metric">
          <div class="welcome-hero__metric-value">1</div>
          <div class="welcome-hero__metric-label">Agent 工具</div>
        </div>
      </div>
      <div class="welcome-hero__chips">
        <span class="welcome-hero__chip">执行计划</span>
        <span class="welcome-hero__chip">记忆检索</span>
        <span class="welcome-hero__chip">Agent 工具</span>
      </div>
    </section>
  `;
}

export function buildTaskPanel({ title = '执行面板', summary = '当前任务链路清晰可追踪', steps = [] } = {}) {
  const items = normalizeSteps(steps).map(({ title: stepTitle, tone, meta }) => {
    const metaMarkup = meta ? `<span class="task-panel__item-meta">${escapeHtml(meta)}</span>` : '';
    return `<li class="task-panel__item task-panel__item--${tone}"><span class="task-panel__item-title">${escapeHtml(stepTitle)}</span>${metaMarkup}</li>`;
  }).join('');

  return `
    <section class="task-panel">
      <div class="task-panel__header">
        <div class="task-panel__title">${escapeHtml(title)}</div>
        <div class="task-panel__summary">${escapeHtml(summary)}</div>
      </div>
      <ul class="task-panel__list">${items}</ul>
    </section>
  `;
}
