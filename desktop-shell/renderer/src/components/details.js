// renderer/src/components/details.js — right panel (skill detail / R-CCAM trace).
//
// Listens for the 'skill:open' CustomEvent dispatched by sidebar.js
// when the user clicks a skill pill. Fetches the skill body + graph
// neighbors, renders to the right panel.

import { galaxy } from '../ipc/client.js';

const $ = (id) => document.getElementById(id);

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = String(s ?? '');
  return d.innerHTML;
}

async function showSkillDetail(skillId) {
  if (!galaxy.skill) return;
  const detailsBody = $('details-body');
  if (!detailsBody) return;
  try {
    const detail = await galaxy.skill(skillId);
    const body = detail.body || '(no content)';

    let neighborsHtml = '';
    if (galaxy.skillNeighbors) {
      try {
        const nb = await galaxy.skillNeighbors(skillId);
        if (nb.successors && nb.successors.length > 0) {
          neighborsHtml = '<div class="skill-neighbors"><h4>相关技能 (SkillGraph)</h4>';
          for (const s of nb.successors.slice(0, 8)) {
            neighborsHtml += `<div class="neighbor-pill" data-skill="${escapeHtml(s.name)}">${escapeHtml(s.name)} <span class="neighbor-rel">${escapeHtml(s.relation)}</span></div>`;
          }
          neighborsHtml += '</div>';
        }
      } catch (e) { /* ignore */ }
    }

    detailsBody.innerHTML = `
      <div class="skill-detail">
        <h3>${escapeHtml(detail.name || skillId)}</h3>
        <p class="hint">${escapeHtml(detail.description || '')}</p>
        ${detail.version ? `<p class="hint">v${escapeHtml(detail.version)}</p>` : ''}
        ${neighborsHtml}
        <pre class="skill-body">${escapeHtml(body.slice(0, 2000))}</pre>
      </div>`;

    detailsBody.querySelectorAll('.neighbor-pill').forEach((p) => {
      p.addEventListener('click', () => showSkillDetail(p.dataset.skill));
    });
  } catch (e) {
    console.warn('[details] skill detail failed:', e);
  }
}

export function initDetails() {
  window.addEventListener('skill:open', (e) => {
    showSkillDetail(e.detail.id);
  });

  $('collapse-details')?.addEventListener('click', () => {
    document.getElementById('app')?.classList.toggle('details-collapsed');
  });
}
