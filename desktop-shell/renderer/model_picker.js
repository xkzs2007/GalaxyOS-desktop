// model_picker.js — model selection menu (ZCode/Codex pattern).
//
// Persists the current model in localStorage so it survives reloads.
// On select, updates the topbar chip + closes the menu.

const MODEL_KEY = 'galaxyos.model.v1';
const DEFAULT_MODEL = 'Qwen-2.5';

let currentModel = DEFAULT_MODEL;

function load() {
  const m = localStorage.getItem(MODEL_KEY);
  if (m) currentModel = m;
}

function save() {
  localStorage.setItem(MODEL_KEY, currentModel);
}

function setModel(name) {
  currentModel = name;
  save();
  // Update topbar chip
  const chip = document.getElementById('model-name');
  if (chip) chip.textContent = name;
  // Update selected state in menu
  document.querySelectorAll('.model-option').forEach((b) => {
    b.classList.toggle('selected', b.dataset.model === name);
  });
  console.log('[model] switched to', name);
}

function toggleMenu() {
  const menu = document.getElementById('model-picker-menu');
  if (!menu) return;
  menu.hidden = !menu.hidden;
}

function closeMenu() {
  const menu = document.getElementById('model-picker-menu');
  if (menu) menu.hidden = true;
}

function init() {
  load();
  // Apply current model to UI
  const chip = document.getElementById('model-name');
  if (chip) chip.textContent = currentModel;
  document.querySelectorAll('.model-option').forEach((b) => {
    const isCurrent = b.dataset.model === currentModel;
    b.classList.toggle('selected', isCurrent);
    b.addEventListener('click', () => {
      setModel(b.dataset.model);
      closeMenu();
    });
  });
  // Topbar button toggles the menu
  const btn = document.getElementById('model-picker');
  if (btn) {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleMenu();
    });
  }
  // Click-outside closes
  document.addEventListener('click', (e) => {
    const menu = document.getElementById('model-picker-menu');
    if (menu && !menu.hidden && !menu.contains(e.target) && e.target.id !== 'model-picker') {
      closeMenu();
    }
  });
  // Esc closes
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') closeMenu();
  });
}

window.ModelPicker = {
  init,
  get: () => currentModel,
  set: setModel,
};
