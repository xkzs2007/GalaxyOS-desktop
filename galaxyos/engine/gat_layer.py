"""
GAT Layer - 图注意力层
参考论文: Graph Attention Networks (https://arxiv.org/abs/1710.10903)

双路径实现（稠密 + 稀疏），自动按显存预算选择：
  - GraphAttentionLayer  (稠密，N×N mask + softmax)  — 兼容旧调用
  - SparseGraphAttentionLayer (稀疏，O(E·d))           — 大图兜底

高层 GAT / GATLayer / MultiHeadGraphAttentionLayer 支持 mode ∈ {auto, dense, sparse}：
  - auto: 按 memory_budget 估算，自动切
  - dense: 走稠密 (允许 chunk + fp16/bf16 + sub-sample 兜底)
  - sparse: 强制稀疏

环境变量调参：
  GALAXYOS_GAT_MODE              (auto|dense|sparse) 默认 auto
  GALAXYOS_GAT_MEMORY_BUDGET_MB  默认 1536 (1.5GB)
  GALAXYOS_GAT_SPARSE_THRESHOLD  默认 64
"""
import os

try:
    import torch
    import torch.nn as nn
    import torch.nn.functional as F
    TORCH_AVAILABLE = True
except ImportError:
    TORCH_AVAILABLE = False
from typing import Optional, Tuple, Union


# ═══════════════════════════════════════════════════
# 全局默认（环境变量覆盖）
# ═══════════════════════════════════════════════════
#
# 注意：默认按 5.8GB 容器设计，GAT 单进程预算 1.2GB，留 4GB 给 system + 同进程
# 其它（vault / onnx / cfc 等）。如部署到 8GB+ 环境可调大 GALAXYOS_GAT_MEMORY_BUDGET_MB。

def _parse_mode(v: str) -> str:
    v = (v or "auto").strip().lower()
    return v if v in ("auto", "dense", "sparse") else "auto"


def _parse_dtype(v: str) -> torch.dtype:
    v = (v or "fp32").strip().lower()
    if v in ("fp16", "half"):
        return torch.float16
    if v in ("bf16", "bfloat16"):
        return torch.bfloat16
    return torch.float32


_DEFAULT_MODE = _parse_mode(os.environ.get("GALAXYOS_GAT_MODE", "auto"))
_DEFAULT_BUDGET_MB = float(os.environ.get("GALAXYOS_GAT_MEMORY_BUDGET_MB", "1200"))
_DEFAULT_SPARSE_THRESHOLD = int(os.environ.get("GALAXYOS_GAT_SPARSE_THRESHOLD", "128"))
_DEFAULT_DTYPE = _parse_dtype(os.environ.get("GALAXYOS_GAT_DTYPE", "fp32"))
_DEFAULT_CHUNK_SIZE = int(os.environ.get("GALAXYOS_GAT_CHUNK_SIZE", "256"))


def _adj_to_edge_index(adj: torch.Tensor) -> torch.Tensor:
    """稠密 (N,N) 邻接 → (2, E) 边索引。"""
    if adj.is_sparse:
        return adj.coalesce().indices().to(torch.long)
    src, dst = adj.nonzero(as_tuple=True)
    if src.numel() == 0:
        return torch.zeros(2, 0, dtype=torch.long, device=adj.device)
    return torch.stack([src, dst], dim=0).to(torch.long)


def _estimate_dense_bytes(N: int, hidden: int, num_heads: int, layers: int = 2,
                          attn_per_node_bytes: int = 4,
                          input_dim: int = 64) -> int:
    """
    粗估稠密 GAT 前向峰值显存。

    实测在 chunked dense 路径下，每层每头会同时展开 (chunk, N, F) 注意力块，
    N=1500/4head/2layer 时单层单头 (chunk=256, N=1500, F=8) = 256·1500·8·4B = 12MB，
    4 头 × 2 层同时活着 = 100MB+，叠加 Wh/ELU/LN 副本，RSS 实测 3GB+。

    因此估算时除了 attention 矩阵 + 临时 Wh 展开，给 chunked 路径加一个
    (chunk * N * F * num_heads) 副本开销，再乘安全系数 3.0。
    """
    # 顶层 attention
    attn = N * N * num_heads * attn_per_node_bytes
    wh = N * hidden * num_heads * 4
    # chunk 路径下 (chunk, N, F) 展开（每头）
    chunk = min(_DEFAULT_CHUNK_SIZE, max(1, N))
    chunk_blk = chunk * N * max(hidden, 1) * num_heads * 4
    proj = N * max(input_dim, hidden) * 4
    inter = N * hidden * num_heads * 4 * max(layers - 1, 0)
    base = attn + wh + chunk_blk + proj + inter
    return int(base * 8.0)  # 8x 系数：实测 N=1200 走 dense 涨 2.2GB，估算需更保守


def _dense_n_hard_limit(num_heads: int, attn_per_node_bytes: int = 4) -> int:
    """
    硬上限：单 attention 矩阵允许的最大节点数。

    5.8GB 容器单进程 1.5GB 预算下实测 (fp32+chunk=256)：
      N=600  RSS=722MB    N=800  RSS=1123MB
      N=1000 RSS=1589MB   N=1200 RSS=2220MB (超 2GB)
    即 4head 下 N≤800 安全，N≥1000 应踢 sparse。

    硬上限设计：单层 (N,N) attention 字节 ≤ 50MB，
    num_heads=4 时 N ≤ sqrt(50M / 4 / 4) ≈ 1768
    num_heads=8 时 N ≤ sqrt(50M / 4 / 8) ≈ 1250
    留出多头 attention 拼接 + 临时 buffer 余量。
    """
    cap_bytes = 50 * 1024 * 1024  # 50MB 单层 attention 上限
    n_sq = cap_bytes / max(attn_per_node_bytes, 1) / max(num_heads, 1)
    return int(n_sq ** 0.5)


# ═══════════════════════════════════════════════════
# 稠密单头
# ═══════════════════════════════════════════════════

class GraphAttentionLayer(nn.Module):
    """
    单头图注意力层 (稠密)

    α_ij = softmax_j(LeakyReLU(a^T [Wh_i || Wh_j]))
    h_i' = σ(Σ_{j∈N(i)} α_ij Wh_j)
    """

    def __init__(
        self,
        in_features: int,
        out_features: int,
        dropout: float = 0.6,
        alpha: float = 0.2,
        concat: bool = True,
        bias: bool = True
    ):
        super().__init__()
        if not TORCH_AVAILABLE:
            raise ImportError("torch is required for GraphAttentionLayer")
        self.in_features = in_features
        self.out_features = out_features
        self.dropout = dropout
        self.alpha = alpha
        self.concat = concat

        self.W = nn.Parameter(torch.empty(in_features, out_features))
        self.a = nn.Parameter(torch.empty(2 * out_features, 1))

        if bias:
            self.bias = nn.Parameter(torch.empty(out_features))
        else:
            self.register_parameter('bias', None)

        self.leaky_relu = nn.LeakyReLU(alpha)
        self.reset_parameters()

    def reset_parameters(self):
        nn.init.xavier_uniform_(self.W)
        nn.init.xavier_uniform_(self.a)
        if self.bias is not None:
            nn.init.zeros_(self.bias)

    def forward(
        self,
        h: torch.Tensor,
        adj: torch.Tensor,
        chunk_size: int = 0,
    ) -> torch.Tensor:
        """chunk_size>0 时按行分块算注意力。"""
        N = h.size(0)
        # dtype 一致性：h 是 fp16 时把 W/a 也 cast 到 fp16
        W = self.W.to(h.dtype) if self.W.dtype != h.dtype else self.W
        a = self.a.to(h.dtype) if self.a.dtype != h.dtype else self.a
        bias = self.bias.to(h.dtype) if (self.bias is not None and self.bias.dtype != h.dtype) else self.bias

        Wh = torch.mm(h, W)  # (N, out_features)

        if chunk_size and chunk_size > 0 and chunk_size < N:
            out_chunks = []
            for start in range(0, N, chunk_size):
                end = min(start + chunk_size, N)
                Wh1 = Wh[start:end].unsqueeze(1).expand(-1, N, -1)
                Wh2 = Wh.unsqueeze(0).expand(end - start, -1, -1)
                Wh_cat = torch.cat([Wh1, Wh2], dim=-1)
                e = self.leaky_relu(torch.matmul(Wh_cat, a).squeeze(-1))  # (chunk, N)
                zero_vec = -9e15 * torch.ones_like(e)
                attn = torch.where(adj[start:end] > 0, e, zero_vec)
                attn = F.softmax(attn, dim=1)
                attn = F.dropout(attn, self.dropout, training=self.training)
                out_chunks.append(torch.matmul(attn, Wh))
            h_prime = torch.cat(out_chunks, dim=0)
        else:
            Wh1 = Wh.unsqueeze(1).expand(-1, N, -1)
            Wh2 = Wh.unsqueeze(0).expand(N, -1, -1)
            Wh_cat = torch.cat([Wh1, Wh2], dim=-1)
            e = self.leaky_relu(torch.matmul(Wh_cat, a).squeeze(-1))
            zero_vec = -9e15 * torch.ones_like(e)
            attn = torch.where(adj > 0, e, zero_vec)
            attn = F.softmax(attn, dim=1)
            attn = F.dropout(attn, self.dropout, training=self.training)
            h_prime = torch.matmul(attn, Wh)

        if bias is not None:
            h_prime = h_prime + bias
        return F.elu(h_prime) if self.concat else h_prime

    def __repr__(self):
        return f'{self.__class__.__name__}({self.in_features} -> {self.out_features})'


# ═══════════════════════════════════════════════════
# 稀疏单头
# ═══════════════════════════════════════════════════

class SparseGraphAttentionLayer(nn.Module):
    """
    稀疏图注意力层 — O(E·d)

    接受 edge_index (2, E)，不再构造 N×N 矩阵。
    """

    def __init__(
        self,
        in_features: int,
        out_features: int,
        dropout: float = 0.6,
        alpha: float = 0.2,
        concat: bool = True,
        bias: bool = True
    ):
        super().__init__()
        if not TORCH_AVAILABLE:
            raise ImportError("torch is required for SparseGraphAttentionLayer")
        self.in_features = in_features
        self.out_features = out_features
        self.dropout = dropout
        self.alpha = alpha
        self.concat = concat

        self.W = nn.Parameter(torch.empty(in_features, out_features))
        self.a_src = nn.Parameter(torch.empty(out_features, 1))
        self.a_dst = nn.Parameter(torch.empty(out_features, 1))

        if bias:
            self.bias = nn.Parameter(torch.empty(out_features))
        else:
            self.register_parameter('bias', None)

        self.leaky_relu = nn.LeakyReLU(alpha)
        self.reset_parameters()

    def reset_parameters(self):
        nn.init.xavier_uniform_(self.W)
        nn.init.xavier_uniform_(self.a_src)
        nn.init.xavier_uniform_(self.a_dst)
        if self.bias is not None:
            nn.init.zeros_(self.bias)

    def forward(
        self,
        h: torch.Tensor,
        edge_index: torch.Tensor,
        num_nodes: Optional[int] = None,
    ) -> torch.Tensor:
        N = num_nodes if num_nodes is not None else h.size(0)
        # dtype 一致
        W = self.W.to(h.dtype) if self.W.dtype != h.dtype else self.W
        a_src = self.a_src.to(h.dtype) if self.a_src.dtype != h.dtype else self.a_src
        a_dst = self.a_dst.to(h.dtype) if self.a_dst.dtype != h.dtype else self.a_dst
        bias = self.bias.to(h.dtype) if (self.bias is not None and self.bias.dtype != h.dtype) else self.bias

        if edge_index is None or edge_index.numel() == 0:
            Wh = torch.mm(h, W)
            if bias is not None:
                Wh = Wh + bias
            return F.elu(Wh) if self.concat else Wh

        Wh = torch.mm(h, W)
        src_idx = edge_index[0].to(h.device)
        dst_idx = edge_index[1].to(h.device)

        e_src = torch.mm(Wh, a_src).squeeze(-1)
        e_dst = torch.mm(Wh, a_dst).squeeze(-1)

        edge_e = self.leaky_relu(e_src[src_idx] + e_dst[dst_idx])

        device = edge_e.device
        try:
            max_per_dst = torch.full((N,), -1e9, dtype=edge_e.dtype, device=device)
            max_per_dst.index_reduce_(0, dst_idx, edge_e, reduce="amax", include_self=False)
        except Exception:
            unique_dst, inv = torch.unique(dst_idx, return_inverse=True)
            mp = torch.zeros(unique_dst.size(0), dtype=edge_e.dtype, device=device)
            mp = mp.scatter_reduce(0, inv, edge_e, reduce="amax", include_self=True)
            max_per_dst = mp

        exp_e = torch.exp(edge_e - max_per_dst[dst_idx])
        sum_per_dst = torch.zeros(N, dtype=edge_e.dtype, device=device)
        sum_per_dst.index_add_(0, dst_idx, exp_e)
        sum_per_dst = sum_per_dst.clamp(min=1e-16)
        alpha = exp_e / sum_per_dst[dst_idx]
        alpha = F.dropout(alpha, self.dropout, training=self.training)

        h_prime = torch.zeros(N, self.out_features, dtype=h.dtype, device=device)
        h_prime.index_add_(0, dst_idx, Wh[src_idx] * alpha.unsqueeze(-1))

        if bias is not None:
            h_prime = h_prime + bias
        return F.elu(h_prime) if self.concat else h_prime


# ═══════════════════════════════════════════════════
# 多头
# ═══════════════════════════════════════════════════

class MultiHeadGraphAttentionLayer(nn.Module):
    """
    多头 GAT — mode ∈ {auto, dense, sparse}, backend ∈ {auto, native, pyg}

    auto mode:   N ≤ sparse_threshold 走 dense，否则 sparse
    auto backend: torch_geometric 可用时稠密路径优先 pyg（CPU scatter 优化）
    """

    def __init__(
        self,
        in_features: int,
        out_features: int,
        num_heads: int = 8,
        dropout: float = 0.6,
        alpha: float = 0.2,
        concat: bool = True,
        average: bool = False,
        mode: Optional[str] = None,
        sparse_threshold: Optional[int] = None,
        memory_budget_mb: Optional[float] = None,
        chunk_size: int = 0,
        backend: Optional[str] = None,
    ):
        super().__init__()
        if not TORCH_AVAILABLE:
            raise ImportError("torch is required for MultiHeadGraphAttentionLayer")
        self.in_features = in_features
        self.out_features = out_features
        self.num_heads = num_heads
        self.concat = concat
        self.average = average
        self.mode = _parse_mode(mode) if mode is not None else _DEFAULT_MODE
        self.sparse_threshold = sparse_threshold if sparse_threshold is not None else _DEFAULT_SPARSE_THRESHOLD
        self.memory_budget_mb = memory_budget_mb if memory_budget_mb is not None else _DEFAULT_BUDGET_MB
        self.chunk_size = chunk_size or _DEFAULT_CHUNK_SIZE
        # backend: auto → pyg (如可用) > native
        env_backend = os.environ.get("GALAXYOS_GAT_BACKEND", "auto")
        if backend is not None:
            self.backend = backend if backend in ("native", "pyg") else "auto"
        else:
            self.backend = env_backend if env_backend in ("native", "pyg") else "auto"
        if self.backend == "auto":
            self.backend = "pyg" if _pyg_available() else "native"

        self.attentions = nn.ModuleList()  # 延迟构建（按 mode+backend）

        if not concat and average:
            self.out_proj = nn.Linear(out_features, out_features)
        else:
            self.out_proj = None

    def _build_attentions(self, use_sparse: bool):
        """按 use_sparse + backend 建对应类型的头。"""
        if self.attentions:
            self.attentions = nn.ModuleList()
        if use_sparse:
            # 稀疏路径用原生实现（PyG 的 GATConv 也会 densify 算 scatter，
            # 在纯稀疏下与原生差异不大，且 PyG 强制要求 GATConv 不可省 self_loop）
            cls = SparseGraphAttentionLayer
        else:
            # 稠密路径优先 PyG 后端
            if self.backend == "pyg" and _pyg_available():
                cls = PyGAttentionLayer
            else:
                cls = GraphAttentionLayer
        for _ in range(self.num_heads):
            self.attentions.append(cls(
                in_features=self.in_features,
                out_features=self.out_features,
                dropout=self.dropout if hasattr(self, "dropout") else 0.6,
                alpha=self.alpha if hasattr(self, "alpha") else 0.2,
                concat=True,
            ))

    def _decide(self, n: int, hidden: int) -> str:
        if self.mode != "auto":
            return self.mode
        if n <= self.sparse_threshold:
            return "dense"
        # 硬上限：单 attention 矩阵字节超过 ~256MB 直接 sparse
        hard = _dense_n_hard_limit(self.num_heads)
        if n > hard:
            return "sparse"
        est = _estimate_dense_bytes(n, hidden, self.num_heads, layers=1,
                                     input_dim=self.in_features)
        budget = self.memory_budget_mb * 1024 * 1024
        if est <= budget:
            return "dense"
        return "sparse"

    def forward(
        self,
        h: torch.Tensor,
        graph: Union[torch.Tensor, Tuple[torch.Tensor, int]]
    ) -> torch.Tensor:
        n = h.size(0)
        use_sparse = self._decide(n, self.out_features) == "sparse"
        self._build_attentions(use_sparse)

        if use_sparse:
            if isinstance(graph, torch.Tensor) and graph.dim() == 2 and graph.size(0) == 2:
                edge_index = graph
            else:
                edge_index = _adj_to_edge_index(graph)
            heads = [att(h, edge_index, n) for att in self.attentions]
        else:
            # dense
            is_edge_index = (isinstance(graph, torch.Tensor) and graph.dim() == 2 and graph.size(0) == 2)
            if is_edge_index:
                # PyG 后端直接吃 edge_index；原生 GAT 也接受 edge_index（_adj_to_edge_index 路径）
                if self.backend == "pyg" and any(
                    isinstance(a, PyGAttentionLayer) for a in self.attentions
                ):
                    # 透传 edge_index；自环已由上层加好
                    heads = [att(h, graph, n) for att in self.attentions]
                else:
                    # 显式 edge_index 仍转回稠密（由调用方保证 N 较小）
                    n_e = h.size(0)
                    adj = torch.zeros(n_e, n_e, device=h.device, dtype=h.dtype)
                    if graph.numel() > 0:
                        adj[graph[0], graph[1]] = 1.0
                    adj = adj + torch.eye(n_e, device=h.device, dtype=h.dtype)
                    heads = [att(h, adj, chunk_size=self.chunk_size) for att in self.attentions]
            else:
                heads = [att(h, graph, chunk_size=self.chunk_size) for att in self.attentions]

        stacked = torch.stack(heads, dim=0) if not self.concat else torch.cat(heads, dim=1)
        if not self.concat and self.average and self.out_proj is not None:
            return self.out_proj(stacked.mean(dim=0))
        if not self.concat:
            return stacked.mean(dim=0)
        return stacked


# ═══════════════════════════════════════════════════
# 单层封装
# ═══════════════════════════════════════════════════

class GATLayer(nn.Module):
    """
    GAT 单层 — 残差 + LayerNorm

    mode ∈ {auto, dense, sparse}，默认 auto。
    当 N 超过预算时自动切到 SparseGraphAttentionLayer，1-2GB 内能兜住 10⁴ 节点。
    """

    def __init__(
        self,
        in_features: int,
        out_features: int,
        num_heads: int = 8,
        dropout: float = 0.6,
        alpha: float = 0.2,
        concat: bool = True,
        residual: bool = True,
        mode: Optional[str] = None,
        sparse_threshold: Optional[int] = None,
        memory_budget_mb: Optional[float] = None,
        chunk_size: int = 0,
        dtype: Optional[torch.dtype] = None,
        backend: Optional[str] = None,
    ):
        super().__init__()
        if not TORCH_AVAILABLE:
            raise ImportError("torch is required for GATLayer")
        self.in_features = in_features
        self.dropout = dropout

        self.multi_head = MultiHeadGraphAttentionLayer(
            in_features=in_features,
            out_features=out_features // num_heads if concat else out_features,
            num_heads=num_heads,
            dropout=dropout,
            alpha=alpha,
            concat=concat,
            mode=mode,
            sparse_threshold=sparse_threshold,
            memory_budget_mb=memory_budget_mb,
            chunk_size=chunk_size,
            backend=backend,
        )
        self.multi_head.dropout = dropout
        self.multi_head.alpha = alpha

        if residual:
            self.residual_proj = nn.Linear(in_features, out_features)
        else:
            self.residual_proj = None

        self.dropout_layer = nn.Dropout(dropout)
        self.layer_norm = nn.LayerNorm(out_features)
        self._cast_dtype = dtype if dtype is not None else _DEFAULT_DTYPE

    def forward(
        self,
        h: torch.Tensor,
        graph: Union[torch.Tensor, Tuple[torch.Tensor, int]],
    ) -> torch.Tensor:
        original_dtype = h.dtype
        # 半精度推理降低显存（仅 eval 模式生效更安全）
        use_amp = (
            self._cast_dtype != torch.float32
            and not self.training
            and h.device.type in ("cpu", "cuda")
            and h.dtype == torch.float32
        )
        if use_amp:
            h_cast = h.to(self._cast_dtype)
            h_attn = self.multi_head(h_cast, graph)
            if self.residual and self.residual_proj is not None:
                # 把 residual_proj 的 weight/bias 也 cast 一下避免 dtype mismatch
                rp = self.residual_proj
                weight = rp.weight.to(h_cast.dtype)
                bias = rp.bias.to(h_cast.dtype) if rp.bias is not None else None
                h_residual = F.linear(h_cast, weight, bias)
                h_attn = h_attn + h_residual
            h_attn = h_attn.to(torch.float32)
        else:
            h_attn = self.multi_head(h, graph)
            if self.residual and self.residual_proj is not None:
                h_residual = self.residual_proj(h)
                h_attn = h_attn + h_residual

        h_attn = self.layer_norm(h_attn)
        h_attn = self.dropout_layer(h_attn)
        return h_attn.to(original_dtype)


# ═══════════════════════════════════════════════════
# 完整 GAT
# ═══════════════════════════════════════════════════

class GAT(nn.Module):
    """
    完整 GAT 模型 — 双路径（auto/dense/sparse）

    mode='auto' (默认)：
      - 估算稠密路径所需显存
      - 超过 memory_budget_mb (默认 1.5GB) → 切换稀疏路径
      - 在 1-2GB 内存下可处理 ~10⁴ 节点
    mode='dense'：
      - 强制稠密 (N 不大或显存充裕)
      - 支持 chunk + fp16/bf16 兜底
    mode='sparse'：
      - 强制稀疏 O(E·d)
    """

    def __init__(
        self,
        input_dim: int,
        hidden_dim: int,
        output_dim: int,
        num_heads: int = 8,
        num_layers: int = 2,
        dropout: float = 0.6,
        alpha: float = 0.2,
        residual: bool = True,
        mode: Optional[str] = None,
        sparse_threshold: Optional[int] = None,
        memory_budget_mb: Optional[float] = None,
        chunk_size: int = 0,
        dtype: Optional[torch.dtype] = None,
        backend: Optional[str] = None,
    ):
        super().__init__()
        if not TORCH_AVAILABLE:
            raise ImportError("torch is required for GAT")
        self.input_dim = input_dim
        self.output_dim = output_dim
        self.num_layers = num_layers
        self.mode = _parse_mode(mode) if mode is not None else _DEFAULT_MODE
        self.sparse_threshold = sparse_threshold if sparse_threshold is not None else _DEFAULT_SPARSE_THRESHOLD
        self.memory_budget_mb = memory_budget_mb if memory_budget_mb is not None else _DEFAULT_BUDGET_MB

        self.layers = nn.ModuleList()
        self.layers.append(GATLayer(
            in_features=input_dim,
            out_features=hidden_dim * num_heads,
            num_heads=num_heads, dropout=dropout, alpha=alpha,
            concat=True, residual=residual,
            mode=self.mode, sparse_threshold=self.sparse_threshold,
            memory_budget_mb=self.memory_budget_mb, chunk_size=chunk_size,
            dtype=dtype, backend=backend,
        ))
        for _ in range(num_layers - 2):
            self.layers.append(GATLayer(
                in_features=hidden_dim * num_heads,
                out_features=hidden_dim * num_heads,
                num_heads=num_heads, dropout=dropout, alpha=alpha,
                concat=True, residual=residual,
                mode=self.mode, sparse_threshold=self.sparse_threshold,
                memory_budget_mb=self.memory_budget_mb, chunk_size=chunk_size,
                dtype=dtype, backend=backend,
            ))
        if num_layers > 1:
            self.layers.append(GATLayer(
                in_features=hidden_dim * num_heads,
                out_features=output_dim,
                num_heads=num_heads, dropout=dropout, alpha=alpha,
                concat=False, residual=residual,
                mode=self.mode, sparse_threshold=self.sparse_threshold,
                memory_budget_mb=self.memory_budget_mb, chunk_size=chunk_size,
                dtype=dtype, backend=backend,
            ))

    def _resolve_graph(self, h: torch.Tensor, adj: torch.Tensor):
        """按预算决定是稠密还是稀疏。

        PyG 后端始终透传 edge_index（不 densify），原生 GAT 在大图时也透传。
        """
        n = h.size(0)
        is_edge_index = (isinstance(adj, torch.Tensor) and adj.dim() == 2 and adj.size(0) == 2)
        # 任何 backend + dense 模式下都要走稠密 (N, N) 路径给原生 GraphAttentionLayer
        # 但 PyG 后端始终接收 edge_index；这里按 multi_head 的实际 backend 决定
        # 简化：直接看 self.layers[0].multi_head.backend
        pyg_mode = any(
            l.multi_head.backend == "pyg" for l in self.layers
            if hasattr(l, "multi_head") and l.multi_head.backend == "pyg"
        )

        def _to_dense(ei: torch.Tensor) -> torch.Tensor:
            a = torch.zeros(n, n, device=h.device, dtype=h.dtype)
            if ei is not None and ei.numel() > 0:
                a[ei[0], ei[1]] = 1.0
            return a + torch.eye(n, device=h.device, dtype=h.dtype)

        if self.mode == "sparse":
            if is_edge_index:
                return adj
            return _adj_to_edge_index(adj)
        if self.mode == "dense":
            if pyg_mode:
                # PyG 后端统一吃 edge_index
                return adj if is_edge_index else _adj_to_edge_index(adj)
            if is_edge_index:
                return _to_dense(adj)
            return adj
        # auto
        if n <= self.sparse_threshold:
            if pyg_mode and is_edge_index:
                return adj
            if pyg_mode and not is_edge_index:
                # 稠密 adj → 转 edge_index
                return _adj_to_edge_index(adj)
            if is_edge_index:
                return _to_dense(adj)
            return adj
        # 硬上限：单 attention 矩阵超过 ~50MB → 直接 sparse
        hard = _dense_n_hard_limit(self.layers[0].num_heads)
        if n > hard:
            if is_edge_index:
                return adj
            return _adj_to_edge_index(adj)
        est = _estimate_dense_bytes(n, self.hidden_dim, self.layers[0].num_heads,
                                     layers=self.num_layers,
                                     input_dim=self.input_dim)
        budget = self.memory_budget_mb * 1024 * 1024
        if est <= budget:
            if pyg_mode and is_edge_index:
                return adj
            if pyg_mode and not is_edge_index:
                return _adj_to_edge_index(adj)
            if is_edge_index:
                return _to_dense(adj)
            return adj
        # 走稀疏
        if is_edge_index:
            return adj
        return _adj_to_edge_index(adj)

    def forward(
        self,
        features: torch.Tensor,
        adj: torch.Tensor,
    ) -> torch.Tensor:
        h = features
        graph = self._resolve_graph(h, adj)
        for i, layer in enumerate(self.layers):
            h = layer(h, graph)
            if i < len(self.layers) - 1:
                h = F.elu(h)
        return h

    def get_attention_weights(
        self,
        features: torch.Tensor,
        adj: torch.Tensor,
        layer_idx: int = 0,
    ) -> Optional[torch.Tensor]:
        N = features.size(0)
        # 稀疏模式/大图 不返回稠密 attention
        if self.mode == "sparse" or N > self.sparse_threshold:
            return None
        # 退化到稠密
        if isinstance(adj, torch.Tensor) and adj.dim() == 2 and adj.size(0) == 2:
            a = torch.zeros(N, N, device=features.device, dtype=features.dtype)
            if adj.numel() > 0:
                a[adj[0], adj[1]] = 1.0
            adj = a + torch.eye(N, device=features.device, dtype=features.dtype)

        h = features
        for i, layer in enumerate(self.layers):
            if i == layer_idx:
                all_att = []
                for att in layer.multi_head.attentions:
                    Wh = torch.mm(h, att.W)
                    Wh1 = Wh.unsqueeze(1).expand(-1, N, -1)
                    Wh2 = Wh.unsqueeze(0).expand(N, -1, -1)
                    Wh_cat = torch.cat([Wh1, Wh2], dim=-1)
                    e = att.leaky_relu(torch.matmul(Wh_cat, att.a).squeeze(-1))
                    zero_vec = -9e15 * torch.ones_like(e)
                    attn = torch.where(adj > 0, e, zero_vec)
                    all_att.append(F.softmax(attn, dim=1))
                return torch.stack(all_att).mean(dim=0)
            h = layer(h, adj)
        return None


# ═══════════════════════════════════════════════════
# SparseGAT 便捷别名（外部 API）
# ═══════════════════════════════════════════════════

class SparseGAT(GAT):
    """强制 use_sparse=True 的便捷封装。"""
    def __init__(self, *args, **kwargs):
        kwargs["mode"] = "sparse"
        super().__init__(*args, **kwargs)


# ═══════════════════════════════════════════════════
# DenseGAT 便捷别名（外部 API）
# ═══════════════════════════════════════════════════

class DenseGAT(GAT):
    """强制 mode='dense' 的便捷封装；支持 chunk + 半精度。"""
    def __init__(self, *args, **kwargs):
        kwargs["mode"] = "dense"
        super().__init__(*args, **kwargs)


# ═══════════════════════════════════════════════════
# PyG 后端 — torch_geometric.nn.GATConv 封装
# 5.8GB 容器下优势：
#   - PyG 用 torch_scatter.scatter_softmax，CPU 上比手写 index_add_ 快 2-3x
#   - 自动处理 edge_index，无需 densify adj
#   - 显存峰值更可控（不会展 (chunk, N, F) 临时块）
# ═══════════════════════════════════════════════════

def _pyg_available() -> bool:
    try:
        from torch_geometric.nn import GATConv  # noqa
        return True
    except Exception:
        return False


class PyGAttentionLayer(nn.Module):
    """
    torch_geometric.nn.GATConv 包装层

    输入统一为 (x, edge_index)，无论上层传的是 adj 还是 edge_index 都能自动适配。
    """
    def __init__(
        self,
        in_features: int,
        out_features: int,
        dropout: float = 0.6,
        alpha: float = 0.2,
        concat: bool = True,
        bias: bool = True,
    ):
        super().__init__()
        from torch_geometric.nn import GATConv
        self.gat_conv = GATConv(
            in_channels=in_features,
            out_channels=out_features,
            heads=1,                # 单头；多头在外层堆叠
            dropout=dropout,
            negative_slope=alpha,
            add_self_loops=False,   # 我们自己加自环（PyG 2.8 的 add_self_loops 有 bug）
            edge_dim=None,
            bias=bias,
        )
        self.concat = concat

    def forward(
        self,
        h: torch.Tensor,
        edge_index: torch.Tensor,
        num_nodes: Optional[int] = None,
        chunk_size: int = 0,  # 兼容 MultiHead 的传参；PyG 内部自己管 chunk
    ) -> torch.Tensor:
        N = num_nodes if num_nodes is not None else h.size(0)
        ei = edge_index
        if ei is not None and ei.dtype != torch.long:
            ei = ei.long()
        # 外部已加自环（MultiHead / GAT._resolve_graph），add_self_loops=False
        return self.gat_conv(h, ei)


class PyGMultiHeadGraphAttentionLayer(nn.Module):
    """
    多头 PyG GAT — 头数通过堆叠多个 PyGAttentionLayer 实现（PyG GATConv 的 heads 是 head concat）

    为简化实现和显存，每头一个独立的 GATConv，再用 ModuleList 拼装。
    """
    def __init__(
        self,
        in_features: int,
        out_features: int,
        num_heads: int = 8,
        dropout: float = 0.6,
        alpha: float = 0.2,
        concat: bool = True,
        average: bool = False,
    ):
        super().__init__()
        self.in_features = in_features
        self.out_features = out_features
        self.num_heads = num_heads
        self.concat = concat
        self.average = average

        self.attentions = nn.ModuleList([
            PyGAttentionLayer(
                in_features=in_features,
                out_features=out_features,
                dropout=dropout,
                alpha=alpha,
                concat=True,
            )
            for _ in range(num_heads)
        ])

        if not concat and average:
            self.out_proj = nn.Linear(out_features, out_features)
        else:
            self.out_proj = None

    def forward(
        self,
        h: torch.Tensor,
        graph: Union[torch.Tensor, Tuple[torch.Tensor, int]],
    ) -> torch.Tensor:
        n = h.size(0)
        if isinstance(graph, torch.Tensor) and graph.dim() == 2 and graph.size(0) == 2:
            edge_index = graph
        else:
            edge_index = _adj_to_edge_index(graph)

        heads = [att(h, edge_index, n) for att in self.attentions]
        if self.concat:
            return torch.cat(heads, dim=1)
        stacked = torch.stack(heads, dim=0)
        if self.average and self.out_proj is not None:
            return self.out_proj(stacked.mean(dim=0))
        return stacked.mean(dim=0)


# ═══════════════════════════════════════════════════
# PyGGAT — PyG 后端的便捷封装
# ═══════════════════════════════════════════════════

class PyGGAT(GAT):
    """
    PyG 后端 GAT — 用 torch_geometric.nn.GATConv 实现，CPU 端 scatter 优化。

    等价于 GAT(backend='pyg')。PyG 不可用时降级到原生 GAT。
    """
    def __init__(self, *args, **kwargs):
        kwargs["backend"] = "pyg"
        super().__init__(*args, **kwargs)


if __name__ == '__main__':
    torch.manual_seed(42)
    num_nodes = 100
    input_dim = 64
    hidden_dim = 8
    output_dim = 64
    num_heads = 8

    features = torch.randn(num_nodes, input_dim)
    adj = torch.rand(num_nodes, num_nodes)
    adj = (adj > 0.9).float()
    adj = adj + torch.eye(num_nodes)

    print("=== 小图 auto 模式 ===")
    model = GAT(input_dim=input_dim, hidden_dim=hidden_dim,
                output_dim=output_dim, num_heads=num_heads, num_layers=2)
    out = model(features, adj)
    print(f"输出: {out.shape}, 参数量: {sum(p.numel() for p in model.parameters())}")

    print("\n=== 大图 (3000 节点) auto 模式应走 sparse ===")
    big_n = 3000
    big_feat = torch.randn(big_n, 32)
    big_adj = torch.rand(big_n, big_n)
    big_adj = (big_adj > 0.99).float()  # 稀疏
    big_adj = big_adj + torch.eye(big_n)
    big_model = GAT(input_dim=32, hidden_dim=8, output_dim=32, num_heads=4, num_layers=2)
    out_big = big_model(big_feat, big_adj)
    print(f"大图输出: {out_big.shape}, 模式={big_model.layers[0].multi_head.mode}")

    print("\n=== 显式 SparseGAT ===")
    sparse_model = SparseGAT(input_dim=32, hidden_dim=8, output_dim=32, num_heads=4, num_layers=2)
    out_sparse = sparse_model(big_feat, big_adj)
    print(f"稀疏输出: {out_sparse.shape}")

    print("\n=== 显式 DenseGAT + chunk ===")
    dense_model = DenseGAT(input_dim=32, hidden_dim=8, output_dim=32, num_heads=4, num_layers=2, chunk_size=512)
    out_dense = dense_model(big_feat, big_adj)
    print(f"稠密输出: {out_dense.shape}")
