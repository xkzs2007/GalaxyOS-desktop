(function() {
  if (window.__galaxyos_injected) return;
  window.__galaxyos_injected = true;

  const COGNITIVE_PANEL_ID = 'galaxyos-cognitive-panel';
  const PANEL_WIDTH = 320;

  function getLocale() {
    return localStorage.getItem('galaxyos-locale') || 'zh';
  }

  const i18n = {
    zh: {
      title: '认知面板',
      close: '关闭',
      memory: '液态神经记忆',
      rccam: 'R-CCAM 循环',
      dag: 'DAG 上下文树',
      search: '记忆检索',
      searchPlaceholder: '搜索记忆...',
      searchButton: '搜索',
      noResults: '未找到匹配的记忆',
      idle: '空闲',
      consolidating: '巩固中',
      consolidated: '已巩固',
      all: '全部',
      pause: '暂停',
      resume: '继续',
      running: '运行中',
      stopped: '已停止',
      summary: '摘要',
      expand: '展开',
      collapse: '折叠',
      noData: '暂无数据',
    },
    en: {
      title: 'Cognitive Panel',
      close: 'Close',
      memory: 'Liquid Neural Memory',
      rccam: 'R-CCAM Loop',
      dag: 'DAG Context Tree',
      search: 'Memory Search',
      searchPlaceholder: 'Search memories...',
      searchButton: 'Search',
      noResults: 'No matching memories',
      idle: 'Idle',
      consolidating: 'Consolidating',
      consolidated: 'Consolidated',
      all: 'All',
      pause: 'Pause',
      resume: 'Resume',
      running: 'Running',
      stopped: 'Stopped',
      summary: 'Summary',
      expand: 'Expand',
      collapse: 'Collapse',
      noData: 'No data',
    },
  };

  function t(key) {
    const locale = getLocale();
    return (i18n[locale] && i18n[locale][key]) || key;
  }

  function createStyles() {
    const style = document.createElement('style');
    style.textContent = `
      #${COGNITIVE_PANEL_ID} {
        position: fixed; top: 0; right: 0; width: ${PANEL_WIDTH}px; height: 100vh;
        background: #fff; border-left: 1px solid #e0e0e0; z-index: 10000;
        display: flex; flex-direction: column; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        font-size: 13px; color: #333; transform: translateX(100%);
        transition: transform 0.25s ease; box-shadow: -2px 0 12px rgba(0,0,0,0.08);
      }
      #${COGNITIVE_PANEL_ID}.open { transform: translateX(0); }
      #${COGNITIVE_PANEL_ID} .gp-header {
        display: flex; justify-content: space-between; align-items: center;
        padding: 12px 16px; border-bottom: 1px solid #e0e0e0; background: #fafafa;
      }
      #${COGNITIVE_PANEL_ID} .gp-header h3 { margin: 0; font-size: 15px; font-weight: 600; }
      #${COGNITIVE_PANEL_ID} .gp-close {
        background: none; border: none; font-size: 18px; cursor: pointer; color: #666; padding: 0 4px;
      }
      #${COGNITIVE_PANEL_ID} .gp-tabs {
        display: flex; border-bottom: 1px solid #e0e0e0; background: #fafafa;
      }
      #${COGNITIVE_PANEL_ID} .gp-tab {
        flex: 1; padding: 8px 4px; text-align: center; font-size: 12px; cursor: pointer;
        border: none; background: none; color: #666; border-bottom: 2px solid transparent;
      }
      #${COGNITIVE_PANEL_ID} .gp-tab.active { color: #1976d2; border-bottom-color: #1976d2; font-weight: 600; }
      #${COGNITIVE_PANEL_ID} .gp-content { flex: 1; overflow-y: auto; padding: 12px 16px; }
      #galaxyos-cog-toggle {
        position: fixed; top: 12px; right: 12px; z-index: 10001;
        width: 36px; height: 36px; border-radius: 50%; border: 1px solid #e0e0e0;
        background: #fff; cursor: pointer; display: flex; align-items: center; justify-content: center;
        font-size: 16px; box-shadow: 0 2px 8px rgba(0,0,0,0.1); transition: all 0.2s;
      }
      #galaxyos-cog-toggle:hover { background: #f5f5f5; box-shadow: 0 2px 12px rgba(0,0,0,0.15); }
    `;
    document.head.appendChild(style);
  }

  function createPanel() {
    const panel = document.createElement('div');
    panel.id = COGNITIVE_PANEL_ID;

    panel.innerHTML = `
      <div class="gp-header">
        <h3>${t('title')}</h3>
        <button class="gp-close" onclick="document.getElementById('${COGNITIVE_PANEL_ID}').classList.remove('open')">&times;</button>
      </div>
      <div class="gp-tabs">
        <button class="gp-tab active" data-tab="memory">${t('memory')}</button>
        <button class="gp-tab" data-tab="rccam">${t('rccam')}</button>
        <button class="gp-tab" data-tab="dag">${t('dag')}</button>
        <button class="gp-tab" data-tab="search">${t('search')}</button>
      </div>
      <div class="gp-content" id="gp-tab-content">
        <div style="text-align:center;color:#999;padding:24px;">${t('noData')}</div>
      </div>
    `;

    panel.querySelectorAll('.gp-tab').forEach(tab => {
      tab.addEventListener('click', () => {
        panel.querySelectorAll('.gp-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        requestTabData(tab.dataset.tab);
      });
    });

    return panel;
  }

  function requestTabData(tabName) {
    const content = document.getElementById('gp-tab-content');
    if (!content) return;

    content.innerHTML = `<div style="text-align:center;color:#999;padding:24px;">${t('noData')}</div>`;

    if (window.__TAURI__) {
      window.__TAURI__.core.invoke('request_cognitive_data', { tab: tabName })
        .then(data => {
          if (data && data.html) {
            content.innerHTML = data.html;
          }
        })
        .catch(() => {});
    }
  }

  function createToggle() {
    const btn = document.createElement('button');
    btn.id = 'galaxyos-cog-toggle';
    btn.textContent = '\u2699';
    btn.title = t('title');
    btn.addEventListener('click', () => {
      const panel = document.getElementById(COGNITIVE_PANEL_ID);
      if (panel) panel.classList.toggle('open');
    });
    return btn;
  }

  function inject() {
    if (document.getElementById(COGNITIVE_PANEL_ID)) return;
    createStyles();
    document.body.appendChild(createPanel());
    document.body.appendChild(createToggle());
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', inject);
  } else {
    inject();
  }

  window.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'galaxyos-cognitive-update') {
      const panel = document.getElementById(COGNITIVE_PANEL_ID);
      if (!panel || !panel.classList.contains('open')) return;
      const activeTab = panel.querySelector('.gp-tab.active');
      if (activeTab) requestTabData(activeTab.dataset.tab);
    }
  });
})();