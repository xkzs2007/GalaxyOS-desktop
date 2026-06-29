#!/usr/bin/env python3
"""
本地 Embedding 推理服务 — bge-small-zh (ONNX Runtime)

模型：BAAI/bge-small-zh-v1.5 (4层 BERT, 512维, 92MB, 中文优化)
推理：ONNX Runtime CPU (0.2MB 计算图 + 93MB 权重)
速度：单条 ~10.7ms (比 PyTorch 15ms 快 30%)

架构：
  ┌────────────┐    ┌──────────────┐    ┌───────────────┐
  │ query text  │ →  │ Tokenizer    │ →  │ ONNX Runtime  │
  │ neuron text │    │ (tokenizers) │    │ (CPUExecution)│
  └────────────┘    └──────────────┘    └───────────────┘
                                            │
                                            ↓
                                       [512维 embedding]
                                            │
                                       ┌────┴────┐
                                       │ Cosine  │ → top-K seeds
                                       │ Similar  │
                                       └─────────┘

缓存策略：
  - 神经元 embedding 持久化到 .npy + .json 双文件
  - 增量计算：只对新的或内容变更的神经元跑 ONNX
  - query embedding 实时计算，不做缓存
"""

import os, json, logging, time, gc, numpy as np, threading

logger = logging.getLogger("onnx_embedding")

_EMBEDDING_DIM = 512           # bge-small-zh 输出维度
_BATCH_SIZE = 50               # 推理批次

# 模型目录：三重自发现（repo → cwd-repo → user）
_repo_root = os.environ.get("GALAXYOS_REPO", "")
_candidates = []
if _repo_root:
    _candidates.append(os.path.join(_repo_root, "models", "embeddings"))
# 从 cwd 推断 repo（常见于在 repo root 运行）
_cwd = os.getcwd()
_candidates.append(os.path.join(_cwd, "models", "embeddings"))
_candidates.append(os.path.join(_cwd, "..", "models", "embeddings"))
# user 目录兜底
_candidates.append(os.path.expanduser("~/.openclaw/workspace/GalaxyOS/models/embeddings"))
# GalaxyOS 运行时安装目录
_candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "models", "embeddings"))

_MODEL_DIR = ""
for _c in _candidates:
    _c = os.path.abspath(_c)
    if os.path.isdir(_c) and os.path.exists(os.path.join(_c, "bge-small-zh.onnx")):
        _MODEL_DIR = _c
        break
if not _MODEL_DIR:
    _MODEL_DIR = _candidates[-1]  # fallback to user dir for error message
_CACHE_DIR = os.path.expanduser(
    "~/.openclaw/workspace/.neural_cache"
)

_INSTANCE = None
_INSTANCE_LOCK = threading.Lock()


# ── ONNX Runtime 引擎 ──

class LocalEmbeddingService:
    """
    本地 Embedding 服务（单例，bge-small-zh）

    用法：
        svc = get_onnx_embedding()
        svc.initialize()
        q_emb = svc.embed_query("上海旅游攻略")
        seeds = svc.find_seeds(query, candidates=[...])
    """

    def __init__(self):
        self._tok = None
        self._sess = None
        self._initialized = False
        self._cache = {}
        self._cache_dirty = False
        self._cache_path = os.path.join(_CACHE_DIR, "neural_emb_cache.npy")
        self._index_path = os.path.join(_CACHE_DIR, "neural_emb_cache.json")
        os.makedirs(_CACHE_DIR, exist_ok=True)

    # ── 初始化 ──

    def initialize(self):
        if self._initialized:
            return
        t0 = time.time()

        # 1. Tokenizer
        onnx_path = os.path.join(_MODEL_DIR, "bge-small-zh.onnx")
        if not os.path.exists(onnx_path):
            raise FileNotFoundError(
                f"ONNX 模型不存在: {onnx_path}\n"
                "请从 Modelscope 下载 BAAI/bge-small-zh-v1.5 后导出 ONNX"
            )
        import tokenizers
        tok_path = os.path.join(_MODEL_DIR, "tokenizer.json")
        if not os.path.exists(tok_path):
            raise FileNotFoundError(
                f"Tokenizer 不存在: {tok_path}"
            )
        self._tok = tokenizers.Tokenizer.from_file(tok_path)
        self._tok.enable_truncation(max_length=128)
        self._tok.enable_padding(length=128)

        # 2. ONNX Runtime session
        import onnxruntime as ort
        so = ort.SessionOptions()
        so.enable_cpu_mem_arena = False
        so.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._sess = ort.InferenceSession(
            onnx_path, so,
            providers=['CPUExecutionProvider'],
        )

        # 3. 缓存
        self._load_cache()
        logger.info(
            f"本地 Embedding 服务初始化完成: "
            f"{len(self._cache)} 缓存项, {time.time()-t0:.1f}s "
            f"(ONNX Runtime)"
        )
        self._initialized = True

    # ── ONNX 推理 ──

    def embed(self, texts: list) -> np.ndarray:
        """批量文本 → 归一化 embedding 矩阵"""
        if not texts:
            return np.empty((0, _EMBEDDING_DIM), dtype=np.float32)
        if not self._initialized:
            self.initialize()

        all_embs = []
        for i in range(0, len(texts), _BATCH_SIZE):
            batch = texts[i:i + _BATCH_SIZE]
            encs = self._tok.encode_batch(batch)
            ids = np.array([e.ids for e in encs], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
            emb, = self._sess.run(
                ['bge_embedding'],
                {'input_ids': ids, 'attention_mask': mask}
            )
            # 归一化
            norms = np.linalg.norm(emb, axis=1, keepdims=True)
            norms = np.where(norms == 0, 1.0, norms)
            emb = emb / norms
            all_embs.append(emb)

        return np.vstack(all_embs).astype(np.float32)

    def embed_query(self, text: str) -> np.ndarray:
        """单条文本 → 归一化 embedding"""
        return self.embed([text])[0]

    @staticmethod
    def compute_content_hash(content: str) -> str:
        import hashlib
        return hashlib.md5(content.encode("utf-8")).hexdigest()

    # ── 神经元缓存管理 ──

    def precompute_neurons(self, neurons: list, force: bool = False):
        """批量预计算神经元的 embedding 并缓存"""
        if not self._initialized:
            self.initialize()
        if not neurons:
            return
        to_compute = []
        for n in neurons:
            nid = n.get("id", "")
            content = n.get("content", "") or ""
            if not content:
                continue
            ch = self.compute_content_hash(content[:1000])
            if force or nid not in self._cache or self._cache[nid].get("hash") != ch:
                to_compute.append((nid, content[:1000]))
                self._cache[nid] = {"hash": ch, "content": content[:1000]}

        if not to_compute:
            logger.debug(f"  无新神经元需计算 (已有 {len(self._cache)})")
            return

        t0 = time.time()
        embs = self.embed([t[1] for t in to_compute])
        for (nid, _), emb in zip(to_compute, embs):
            self._cache[nid]["embedding"] = emb.tolist()
        self._cache_dirty = True
        logger.info(
            f"  预计算 {len(to_compute)} 个神经元 ({time.time()-t0:.2f}s)"
        )

    def find_seeds(
        self,
        query: str,
        top_k: int = 10,
        min_score: float = 0.2,
        candidates: list = None,
    ) -> list:
        """
        Embedding 余弦相似度找种子（两阶段：jieba 粗筛 + ONNX 精排）

        Args:
            query: 查询文本
            top_k: 返回 top K
            min_score: 最低相似度阈值
            candidates: 预筛选 [(neuron_id, content), ...]

        Returns:
            [(neuron_id, score, content), ...]
        """
        if not self._initialized:
            self.initialize()

        q_emb = self.embed_query(query).reshape(1, -1)

        # 收集候选 embedding
        if candidates is not None:
            # 从缓存取 + 新计算
            nids_cache, nids_miss, texts_miss = [], [], []
            for nid, content in candidates:
                c = self._cache.get(nid, {})
                if c.get("embedding") is not None:
                    nids_cache.append(nid)
                else:
                    nids_miss.append(nid)
                    texts_miss.append(content[:1000])
            all_nids = list(nids_cache)
            embs_parts = []
            if nids_cache:
                embs_parts.append(
                    np.array([self._cache[n]["embedding"] for n in nids_cache],
                             dtype=np.float32)
                )
            if texts_miss:
                embs_parts.append(self.embed(texts_miss))
                all_nids.extend(nids_miss)
            if not all_nids:
                return []
            embs = np.vstack(embs_parts)
        else:
            # 全缓存扫描
            nids, embs_list = [], []
            for nid, data in self._cache.items():
                e = data.get("embedding")
                if e is None:
                    continue
                nids.append(nid)
                embs_list.append(e)
            if not nids:
                return []
            embs = np.array(embs_list, dtype=np.float32)

        # 余弦相似度 + 排序
        sims = np.dot(q_emb, embs.T).flatten()
        idx = np.argsort(-sims)

        results = []
        for i in idx:
            if sims[i] < min_score:
                break
            if len(results) >= top_k:
                break
            nid = nids[i] if candidates is None else all_nids[i]
            c = self._cache.get(nid, {}).get("content", "")
            results.append((nid, float(sims[i]), c))
        return results

    def get_cache_stats(self) -> dict:
        return {
            "cached_neurons": len(self._cache),
            "model": "bge-small-zh-v1.5",
            "dim": _EMBEDDING_DIM,
        }

    # ── 缓存持久化 ──

    def save_cache(self):
        if not self._cache_dirty:
            return
        t0 = time.time()
        nids, embs, meta = [], [], {}
        for nid, data in self._cache.items():
            e = data.get("embedding")
            if e is None:
                continue
            nids.append(nid)
            embs.append(e)
            meta[nid] = {"hash": data["hash"], "content": data["content"]}
        if not embs:
            return
        np.save(self._cache_path, np.array(embs, dtype=np.float32))
        with open(self._index_path, "w") as f:
            json.dump({"nids": nids, "meta": meta}, f)
        self._cache_dirty = False
        logger.info(f"缓存已保存: {len(nids)} 条 ({time.time()-t0:.2f}s)")

    def _load_cache(self):
        if not os.path.exists(self._cache_path) or not os.path.exists(self._index_path):
            return
        try:
            embs = np.load(self._cache_path)
            with open(self._index_path) as f:
                idx = json.load(f)
            for i, nid in enumerate(idx.get("nids", [])):
                m = idx["meta"].get(nid, {})
                self._cache[nid] = {
                    "embedding": embs[i].tolist(),
                    "hash": m.get("hash", ""),
                    "content": m.get("content", ""),
                }
            logger.info(
                f"加载缓存: {len(idx.get('nids',[]))} 神经元 "
                f"({embs.nbytes/1024/1024:.1f} MB)"
            )
        except Exception as e:
            logger.warning(f"缓存加载失败: {e}")

    def clear_cache(self):
        self._cache.clear()
        self._cache_dirty = False
        for p in [self._cache_path, self._index_path]:
            if os.path.exists(p):
                os.remove(p)


# ── 全局单例 ──

def get_onnx_embedding():
    global _INSTANCE
    if _INSTANCE is None:
        with _INSTANCE_LOCK:
            if _INSTANCE is None:
                _INSTANCE = LocalEmbeddingService()
    return _INSTANCE


# ── 测试 ──

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    svc = get_onnx_embedding()
    svc.initialize()

    q = "上海旅游攻略"
    emb = svc.embed_query(q)
    print(f"query [{q}] dim={len(emb)}  norm={np.linalg.norm(emb):.4f}")

    neurons = [
        {"id": "n1", "content": "上海迪士尼乐园攻略"},
        {"id": "n2", "content": "Python 编程入门教程"},
        {"id": "n3", "content": "北京故宫游玩推荐"},
        {"id": "n4", "content": "机器学习的数学基础"},
    ]
    svc.precompute_neurons(neurons)
    seeds = svc.find_seeds(
        q, top_k=3, min_score=0.1,
        candidates=[(n["id"], n["content"]) for n in neurons]
    )
    print("\nbge-small-zh 种子匹配结果 (ONNX Runtime):")
    for nid, score, content in seeds:
        print(f"  [{nid}] {score:.3f} → {content}")

    stats = svc.get_cache_stats()
    print(f"\n缓存统计: {stats}")
    svc.save_cache()

    # 性能测试
    import time
    t0 = time.time()
    for _ in range(200):
        svc.embed_query("测试性能")
    t1 = time.time()
    print(f"\n性能: 200 次 embed_query = {(t1-t0)*1000:.0f}ms ({((t1-t0)/200)*1000:.2f}ms/run)")
