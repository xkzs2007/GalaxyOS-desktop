// renderer/src/components/details.js — right panel (skill detail / R-CCAM trace).
//
// C 阶段：用 [card][md][source] 替换手写 innerHTML 拼接。
// TokUI 组件自带：
//   - 卡片容器（[card tt:... ]）
//   - Markdown 渲染（[md]...[/md]）
//   - 引用来源（[source n: tt: sn: u:]）
//   - 技能图邻居（[tag] 列表）

import { galaxy } from '../ipc/client.js';
import { bootTokUI } from '../tokui/runtime.js';
import { escapeDsl } from '../utils.js';

function renderSkillDetail(skillId) {
  if (!galaxy.skill) return;
  const host = document.getElementById('details-host');
  if (!host) return;
  bootTokUI().then(async (ui) => {
    if (!ui) return;
    let detail;
    try {
      detail = await galaxy.skill(skillId);
    } catch (e) {
      host.innerHTML = '';
      ui.startStream(host);
      ui.feed(`[card tt:"错误"][callout t:danger tt:"加载失败"]${e.message ?? e}[/callout][/card]`);
      ui.endStream();
      return;
    }
    const body = detail.body || '(no content)';
    const md = body.slice(0, 2000);

    // Fetch graph neighbors
    let neighbors = [];
    if (galaxy.skillNeighbors) {
      try {
        const nb = await galaxy.skillNeighbors(skillId);
        neighbors = (nb.successors ?? []).slice(0, 8);
      } catch (e) { /* ignore */ }
    }

    host.innerHTML = '';
    ui.startStream(host);
    ui.feed(`[card tt:"${escapeDsl(detail.name || skillId)}" v:highlight]`);
    if (detail.description) {
      ui.feed(`[p v:muted]${escapeDsl(detail.description)}[/p]`);
    }
    if (detail.version) {
      ui.feed(`[tag]v${escapeDsl(detail.version)}[/tag]`);
    }
    ui.feed(`[md]\n${md}\n[/md]`);
    if (neighbors.length) {
      ui.feed(`[p v:muted]相关技能：[/p]`);
      ui.feed(`[row]`);
      for (const n of neighbors) {
        ui.feed(`[tag clk:onSkillOpen act:${escapeDsl(n.name)}]${escapeDsl(n.name)}[/tag]`);
      }
      ui.feed(`[/row]`);
    }
    ui.feed(`[/card]`);
    ui.endStream();
  });
}

export function initDetails() {
  window.addEventListener('skill:open', (e) => {
    renderSkillDetail(e.detail.id);
  });
}
