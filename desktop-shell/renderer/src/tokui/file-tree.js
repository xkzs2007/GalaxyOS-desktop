// renderer/src/tokui/file-tree.js — Agent sandbox file browser (P1, v9.6).
//
// Uses TokUI [file-tree] / [ft-folder] / [ft-file] components to
// render a sandbox directory tree for the Agent mode. Allows users
// to browse the sandbox filesystem, click files to preview content,
// and navigate folder structures.
//
// Usage:
//   import { renderFileTree, fetchAndShowFileTree } from './file-tree.js';
//   fetchAndShowFileTree('details-host', '/workspace');

import { getInstance, registerHandler } from './runtime.js';
import { galaxy } from '../ipc/client.js';
import notify from './notify.js';
import { escapeDsl } from '../utils.js';
import { buildSkeleton, buildEmpty } from './polish.js';

// ── Mock data (placeholder until sidecar provides real list_dir) ─

const MOCK_TREE = [
  { name: 'src', type: 'dir', children: [
    { name: 'main.js', type: 'file', badge: '12KB' },
    { name: 'composer.js', type: 'file', badge: '8KB' },
    { name: 'utils.js', type: 'file', badge: '2KB' },
    { name: 'components', type: 'dir', children: [
      { name: 'sidebar.js', type: 'file', badge: '10KB' },
      { name: 'welcome.js', type: 'file', badge: '3KB' },
    ]},
  ]},
  { name: 'tests', type: 'dir', children: [
    { name: 'smoke.test.js', type: 'file', badge: '1KB' },
  ]},
  { name: 'package.json', type: 'file', badge: '2KB' },
  { name: 'README.md', type: 'file', badge: '1KB' },
];

// ── DSL builders ───────────────────────────────────────────────

function renderNode(node, depth = 0) {
  const indent = '  '.repeat(depth);
  if (node.type === 'dir') {
    const badgeAttr = node.badge ? ` badge:${node.badge}` : '';
    return `${indent}[ft-folder name:"${escapeDsl(node.name)}" open${badgeAttr}]\n` +
      (node.children || []).map(c => renderNode(c, depth + 1)).join('') +
      `${indent}[/ft-folder]`;
  }
  const badgeAttr = node.badge ? ` badge:"${escapeDsl(node.badge)}"` : '';
  return `${indent}[ft-file name:"${escapeDsl(node.name)}"${badgeAttr} clk:onFileTreeClick act:${escapeDsl(node.name)}]\n`;
}

function buildFileTreeDSL(tree, rootPath = '/workspace') {
  if (!tree || !tree.length) {
    return buildEmpty('目录为空', `路径: ${rootPath}`, 'folder');
  }

  return `[card tt:"📂 Sandbox · ${escapeDsl(rootPath)}" v:highlight]\n` +
    `  [breadcrumb items:"🏠,${escapeDsl(rootPath)}" clk:onFileTreeBreadcrumb]\n` +
    `  [file-tree]\n` +
    tree.map(n => renderNode(n)).join('') +
    `  [/file-tree]\n` +
    `[/card]`;
}

// ── Public API ────────────────────────────────────────────────

export function renderFileTree(container, tree, rootPath = '/workspace') {
  const host = typeof container === 'string'
    ? document.getElementById(container)
    : container;
  if (!host) return;

  const ui = getInstance();
  if (!ui) return;

  host.innerHTML = '';
  ui.startStream(host);
  ui.feed(buildFileTreeDSL(tree, rootPath));
  ui.endStream();
}

export async function fetchAndShowFileTree(container, rootPath = '/workspace') {
  const host = typeof container === 'string'
    ? document.getElementById(container)
    : container;
  if (!host) return;

  const ui = getInstance();
  if (!ui) return;

  // Show loading
  host.innerHTML = '';
  ui.startStream(host);
  ui.feed(`[card tt:"加载目录…"]`);
  ui.feed(buildSkeleton('chat'));
  ui.feed(`[/card]`);
  ui.endStream();

  try {
    let tree;
    if (galaxy.listDir) {
      const res = await galaxy.listDir({ path: rootPath });
      tree = res?.tree || res?.entries || MOCK_TREE;
    } else {
      // Simulate delay for mock data
      await new Promise(r => setTimeout(r, 200));
      tree = MOCK_TREE;
    }
    renderFileTree(host, tree, rootPath);
  } catch (e) {
    host.innerHTML = '';
    ui.startStream(host);
    ui.feed(`[card tt:"错误"][callout t:danger tt:"加载失败"]${escapeDsl(e.message || String(e))}[/callout][/card]`);
    ui.endStream();
  }
}

// ── Handlers ──────────────────────────────────────────────────

registerHandler('onFileTreeClick', (data) => {
  const name = typeof data === 'string' ? data : data?.act || data?.value || '';
  if (!name) return;
  notify.info(`点击文件: ${name}`, { duration: 2000 });
  // P2: trigger file read + show content in details panel
});

registerHandler('onFileTreeBreadcrumb', (data) => {
  const path = typeof data === 'string' ? data : data?.items || '';
  notify.info(`导航: ${path}`, { duration: 1500 });
});
