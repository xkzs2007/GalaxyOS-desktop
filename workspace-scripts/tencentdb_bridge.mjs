#!/usr/bin/env node
/**
 * 腾讯云记忆 → UnifiedVectorStore 数据桥接脚本
 *
 * 用 Node.js + sqlite-vec 读取腾讯云 vectors.db 的向量数据，
 * 通过 Python 统一 API 写入 UnifiedVectorStore。
 *
 * 用途：打通十亿云四层记忆与 UnifiedVectorStore 之间的数据通路
 *
 * 用法：
 *   node scripts/tencentdb_bridge.mjs          # 同步全部 L1 数据
 *   node scripts/tencentdb_bridge.mjs --dry-run # 预览模式
 *   node scripts/tencentdb_bridge.mjs --stats   # 只看统计
 */

import { createRequire } from 'module';
import { execSync } from 'child_process';
import path from 'path';
import fs from 'fs';

const require = createRequire(import.meta.url);

// 路径
const TENCENTDB_DIR = path.join(process.env.HOME, '.openclaw/memory-tdai');
const VECTORS_DB = path.join(TENCENTDB_DIR, 'vectors.db');
const UNIFIED_VECTORS_DB = path.join(TENCENTDB_DIR, 'unified_vectors.db');
const WORKSPACE = path.join(process.env.HOME, '.openclaw/workspace');
const UNIFIED_API = path.join(WORKSPACE, 'skills/xiaoyi-claw-omega-final/skills/llm-memory-integration/core/xiaoyi_claw_api.py');

// 加载 sqlite-vec
let Database;
try {
  Database = require('better-sqlite3');
} catch {
  // 尝试用默认 sqlite3
  console.error('❌ 需要 better-sqlite3: npm install better-sqlite3');
  process.exit(1);
}

async function main() {
  const args = process.argv.slice(2);
  const dryRun = args.includes('--dry-run');
  const justStats = args.includes('--stats');

  if (!fs.existsSync(VECTORS_DB)) {
    console.error('❌ vectors.db 不存在:', VECTORS_DB);
    process.exit(1);
  }

  // 加载 sqlite-vec 扩展
  const vecExt = path.join(
    process.env.HOME,
    '.openclaw/extensions/memory-tencentdb/node_modules/sqlite-vec-linux-x64/vec0.so'
  );

  const db = new Database(VECTORS_DB);
  db.loadExtension(vecExt);

  // ======== 统计 ========
  const l1Count = db.prepare('SELECT COUNT(*) FROM l1_records').pluck().get();
  const l0Count = db.prepare('SELECT COUNT(*) FROM l0_conversations').pluck().get();
  const vecCount = db.prepare('SELECT COUNT(*) FROM l1_vec').pluck().get();
  const vecDimensions = db.prepare('SELECT dims FROM l1_vec_info').pluck().get() || 4096;

  console.log(`📊 腾讯云记忆插件统计:
  L1 记忆: ${l1Count} 条
  L0 对话: ${l0Count} 条
  向量记录: ${vecCount} 条
  向量维度: ${vecDimensions}`);

  // 检查 unified_vectors.db 已有数据
  let existingCount = 0;
  if (fs.existsSync(UNIFIED_VECTORS_DB)) {
    const udb = new Database(UNIFIED_VECTORS_DB);
    const tables = udb.prepare("SELECT name FROM sqlite_master WHERE type='table'").all();
    if (tables.find(t => t.name === 'vectors')) {
      existingCount = udb.prepare('SELECT COUNT(*) FROM vectors').pluck().get();
    }
    udb.close();
  }
  console.log(`  UnifiedVectorStore 已有: ${existingCount} 条`);

  if (justStats) {
    db.close();
    return;
  }

  // ======== 读取 L1 向量数据 ========
  // l1_vec_rowids 表关联 l1_vec 和 l1_records
  const rows = db.prepare(`
    SELECT
      r.record_id,
      r.content,
      r.type,
      r.priority,
      r.scene_name,
      r.timestamp_str,
      r.metadata_json,
      v.rowid as vec_rowid
    FROM l1_records r
    JOIN l1_vec_rowids vr ON r.rowid = vr.rowid
    JOIN l1_vec v ON vr.rowid = v.rowid
    ORDER BY r.priority DESC
  `).all();

  console.log(`\n📝 待同步数据: ${rows.length} 条`);

  if (rows.length === 0) {
    db.close();
    return;
  }

  // 预览前 5 条
  console.log('\n预览（前 5 条）:');
  for (let i = 0; i < Math.min(5, rows.length); i++) {
    const r = rows[i];
    console.log(`  [${r.priority}] [${r.type}] ${r.record_id.slice(0, 12)}... | ${r.content.slice(0, 60)}...`);
  }

  if (dryRun) {
    console.log('\n✅ 预览模式，未执行写入');
    db.close();
    return;
  }

  // ======== 通过 Python API 写入 UnifiedVectorStore ========
  // 分批写入，避免单次数据量过大
  const BATCH_SIZE = 10;
  let successCount = 0;
  let errorCount = 0;

  // 构建 Python 脚本调用
  for (let i = 0; i < rows.length; i += BATCH_SIZE) {
    const batch = rows.slice(i, i + BATCH_SIZE微观);

    // 构建 JSON 数据传递给 Python
    const batchData = batch.map(r => ({
      id: r.record_id,
      content: r.content,
      metadata: {
        type: r.type,
        priority: r.priority,
        scene_name: r.scene_name,
        timestamp_str: r.timestamp_str,
        metadata_json: r.metadata_json
      },
      source: 'memory-tdai'
    }));

    // 调用 unified_entry.py 的 store 方法
    const jsonInput = JSON.stringify(batchData);
    const script = path.join(WORKSPACE, 'skills/xiaoyi-claw-omega-final/scripts/unified_entry.py');

    try {
      const result = execSync(
        `python3 "${script}" store --content '${jsonInput.replace(/'/g, "'\\''")}' --json`,
        {
          cwd: WORKSPACE,
          encoding: 'utf-8',
          timeout: 30000,
          maxBuffer: 10 * 1024 * 1024
        }
      );
      successCount += batch.length;
    } catch (err) {
      errorCount += batch.length;
      console.error(`❌ 批处理失败 (${i}-${i + batch.length}): ${err.message}`);
    }

    // 进度
    const progress = Math.min((i + BATCH_SIZE) / rows.length * 100, 100);
    process.stdout.write(`\r  进度: ${progress.toFixed(0)}% (${successCount + errorCount}/${rows.length})`);
  }

  console.log('\n');

  // ======== 验证 ========
  const udb = new Database(UNIFIED_VECTORS_DB);
  const finalCount = udb.prepare('SELECT COUNT(*) FROM vectors').pluck().get();
  udb.close();

  console.log(`\n✅ 同步完成:
  成功: ${successCount} 条
  失败: ${errorCount} 条
  UnifiedVectorStore 总数: ${finalCount}`);

  db.close();
}

main().catch(err => {
  console.error('❌ 执行失败:', err);
  process.exit(1);
});
