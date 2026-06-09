"""
GAT Layer - 图注意力层
参考论文: Graph Attention Networks (https://arxiv.org/abs/1710.10903)
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Optional, Tuple, List, Union
import numpy as np


class GraphAttentionLayer(nn.Module):
    """
    单头图注意力层
    
    实现:
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
        """
        Args:
            in_features: 输入特征维度
            out_features: 输出特征维度
            dropout: Dropout 概率
            alpha: LeakyReLU 负斜率
            concat: 是否拼接多头输出
            bias: 是否使用偏置
        """
        super().__init__()
        self.in_features = in_features
        self.out_features = out_features
        self.dropout = dropout
        self.alpha = alpha
        self.concat = concat
        
        # 线性变换
        self.W = nn.Parameter(torch.empty(in_features, out_features))
        
        # 注意力参数
        self.a = nn.Parameter(torch.empty(2 * out_features, 1))
        
        if bias:
            self.bias = nn.Parameter(torch.empty(out_features))
        else:
            self.register_parameter('bias', None)
        
        # LeakyReLU
        self.leaky_relu = nn.LeakyReLU(alpha)
        
        self.reset_parameters()
    
    def reset_parameters(self):
        """初始化参数"""
        nn.init.xavier_uniform_(self.W)
        nn.init.xavier_uniform_(self.a)
        if self.bias is not None:
            nn.init.zeros_(self.bias)
    
    def forward(
        self,
        h: torch.Tensor,
        edge_index: torch.Tensor
    ) -> torch.Tensor:
        """
        前向传播（稀疏 edge_index 版本）
        
        只对实际存在的边计算注意力，O(N*d + E) 内存而非 O(N²)
        
        Args:
            h: 节点特征 (N, in_features)
            edge_index: 边索引 (2, E)，int64 COO 格式
            
        Returns:
            更新后的节点特征 (N, out_features)
        """
        N = h.size(0)
        
        # 线性变换
        Wh = torch.mm(h, self.W)  # (N, out_features)
        
        # 只对实际存在的边计算注意力
        src_idx = edge_index[0]  # (E,)
        dst_idx = edge_index[1]  # (E,)
        
        # 边两端节点的特征
        Wh_src = Wh[src_idx]  # (E, out_features)
        Wh_dst = Wh[dst_idx]  # (E, out_features)
        
        # 拼接 [Wh_i || Wh_j]
        Wh_cat = torch.cat([Wh_src, Wh_dst], dim=-1)  # (E, 2*out_features)
        
        # 计算每条边的注意力分数
        e = self.leaky_relu(torch.matmul(Wh_cat, self.a).squeeze(-1))  # (E,)
        
        # 按目标节点分组 softmax
        # 生成稀疏 COO 注意力矩阵 (E,) → scatter 到 (N, N)
        # 等价于 edge_weight 形式的 softmax
        # 使用 max + exp + sum scatter 做 segment softmax
        ones_for_N = torch.zeros(N, dtype=torch.bool, device=h.device)
        
        # segment softmax: 对每个 dst 节点的入边做 softmax
        # 先找每个 dst 节点的最大值
        max_vals = torch.full((N,), float('-inf'), device=h.device)
        max_vals = max_vals.scatter_reduce_(0, dst_idx, e, reduce='amax')  # (N,)
        
        # exp(score - max)
        e_sub = e - max_vals[dst_idx]  # (E,)
        exp_e = torch.exp(e_sub)  # (E,)
        
        # 求和每个 dst 节点的 exp 总和
        sum_exp = torch.zeros(N, device=h.device)
        sum_exp = sum_exp.scatter_add_(0, dst_idx, exp_e)  # (N,)
        
        # softmax = exp / sum
        alpha = exp_e / (sum_exp[dst_idx] + 1e-16)  # (E,)
        alpha = F.dropout(alpha, self.dropout, training=self.training)
        
        # 聚合：按目标节点加权求和邻居特征
        # h_i' = Σ α_ij * W * h_j
        Wh_weighted = Wh_src * alpha.unsqueeze(-1)  # (E, out_features)
        h_prime = torch.zeros(N, self.out_features, device=h.device)
        h_prime = h_prime.scatter_add_(0, dst_idx.unsqueeze(-1).expand(-1, self.out_features), Wh_weighted)
        
        if self.bias is not None:
            h_prime = h_prime + self.bias
        
        if self.concat:
            return F.elu(h_prime)
        else:
            return h_prime
    
    def __repr__(self):
        return f'{self.__class__.__name__}({self.in_features} -> {self.out_features})'


class SparseGraphAttentionLayer(nn.Module):
    """
    稀疏图注意力层
    
    用于大规模图的稀疏邻接矩阵
    """
    
    def __init__(
        self,
        in_features: int,
        out_features: int,
        dropout: float = 0.6,
        alpha: float = 0.2,
        concat: bool = True
    ):
        super().__init__()
        self.in_features = in_features
        self.out_features = out_features
        self.dropout = dropout
        self.alpha = alpha
        self.concat = concat
        
        self.W = nn.Parameter(torch.empty(in_features, out_features))
        self.a_src = nn.Parameter(torch.empty(out_features, 1))
        self.a_dst = nn.Parameter(torch.empty(out_features, 1))
        
        self.leaky_relu = nn.LeakyReLU(alpha)
        
        self.reset_parameters()
    
    def reset_parameters(self):
        nn.init.xavier_uniform_(self.W)
        nn.init.xavier_uniform_(self.a_src)
        nn.init.xavier_uniform_(self.a_dst)
    
    def forward(
        self,
        h: torch.Tensor,
        edge_index: torch.Tensor
    ) -> torch.Tensor:
        """
        前向传播（稀疏版本）
        
        Args:
            h: 节点特征 (N, in_features)
            edge_index: 边索引 (2, E)，edge_index[0] 是源节点，edge_index[1] 是目标节点
            
        Returns:
            更新后的节点特征 (N, out_features)
        """
        N = h.size(0)
        
        # 线性变换
        Wh = torch.mm(h, self.W)  # (N, out_features)
        
        # 计算边的注意力
        src_idx = edge_index[0]  # (E,)
        dst_idx = edge_index[1]  # (E,)
        
        # 注意力分数
        e_src = torch.mm(Wh, self.a_src).squeeze(-1)  # (N,)
        e_dst = torch.mm(Wh, self.a_dst).squeeze(-1)  # (N,)
        
        # 边的注意力
        edge_attention = self.leaky_relu(e_src[src_idx] + e_dst[dst_idx])  # (E,)
        
        # Softmax（按目标节点分组）
        # 创建稀疏注意力矩阵
        attention_sparse = torch.sparse_coo_tensor(
            edge_index,
            edge_attention,
            size=(N, N)
        )
        attention_sparse = torch.sparse.softmax(attention_sparse, dim=1)
        
        # 聚合
        # 使用稀疏矩阵乘法
        h_prime = torch.sparse.mm(attention_sparse, Wh)
        
        if self.concat:
            return F.elu(h_prime)
        else:
            return h_prime


class MultiHeadGraphAttentionLayer(nn.Module):
    """
    多头图注意力层
    
    实现:
    h_i' = ||_{k=1}^K σ(Σ_{j∈N(i)} α_ij^k W^k h_j)
    或平均版本:
    h_i' = σ(1/K Σ_{k=1}^K Σ_{j∈N(i)} α_ij^k W^k h_j)
    """
    
    def __init__(
        self,
        in_features: int,
        out_features: int,
        num_heads: int = 8,
        dropout: float = 0.6,
        alpha: float = 0.2,
        concat: bool = True,
        average: bool = False
    ):
        """
        Args:
            in_features: 输入特征维度
            out_features: 每个头的输出特征维度
            num_heads: 注意力头数量
            dropout: Dropout 概率
            alpha: LeakyReLU 负斜率
            concat: 是否拼接多头输出
            average: 是否平均多头输出（concat=False 时有效）
        """
        super().__init__()
        self.in_features = in_features
        self.out_features = out_features
        self.num_heads = num_heads
        self.concat = concat
        self.average = average
        
        # 多头注意力
        self.attentions = nn.ModuleList([
            GraphAttentionLayer(
                in_features, 
                out_features, 
                dropout=dropout, 
                alpha=alpha,
                concat=True
            )
            for _ in range(num_heads)
        ])
        
        # 输出投影（用于平均模式）
        if not concat and average:
            self.out_proj = nn.Linear(out_features, out_features)
        else:
            self.out_proj = None
    
    def forward(
        self,
        h: torch.Tensor,
        edge_index: torch.Tensor
    ) -> torch.Tensor:
        """
        前向传播（稀疏 edge_index 版本）
        
        Args:
            h: 节点特征 (N, in_features)
            edge_index: 边索引 (2, E)，int64 COO 格式
            
        Returns:
            更新后的节点特征
        """
        if self.concat:
            return torch.cat([att(h, edge_index) for att in self.attentions], dim=1)
        else:
            heads_output = torch.stack([att(h, edge_index) for att in self.attentions], dim=0)
            avg_output = heads_output.mean(dim=0)
            if self.average and self.out_proj is not None:
                return self.out_proj(avg_output)
            return avg_output


class GATLayer(nn.Module):
    """
    GAT 单层（封装多头注意力）
    """
    
    def __init__(
        self,
        in_features: int,
        out_features: int,
        num_heads: int = 8,
        dropout: float = 0.6,
        alpha: float = 0.2,
        concat: bool = True,
        residual: bool = True
    ):
        super().__init__()
        self.in_features = in_features
        self.out_features = out_features
        self.num_heads = num_heads
        self.concat = concat
        self.residual = residual
        
        # 多头注意力
        self.multi_head = MultiHeadGraphAttentionLayer(
            in_features=in_features,
            out_features=out_features // num_heads if concat else out_features,
            num_heads=num_heads,
            dropout=dropout,
            alpha=alpha,
            concat=concat
        )
        
        # 残差连接
        if residual:
            if concat:
                self.residual_proj = nn.Linear(in_features, out_features)
            else:
                self.residual_proj = nn.Linear(in_features, out_features)
        else:
            self.residual_proj = None
        
        self.dropout = nn.Dropout(dropout)
        self.layer_norm = nn.LayerNorm(out_features)
    
    def forward(
        self,
        h: torch.Tensor,
        edge_index: torch.Tensor
    ) -> torch.Tensor:
        """
        前向传播（稀疏 edge_index 版本）
        
        Args:
            h: 节点特征 (N, in_features)
            edge_index: 边索引 (2, E)，int64 COO 格式
            
        Returns:
            更新后的节点特征 (N, out_features)
        """
        h_attn = self.multi_head(h, edge_index)
        
        # 残差连接
        if self.residual and self.residual_proj is not None:
            h_residual = self.residual_proj(h)
            h_attn = h_attn + h_residual
        
        # Layer Norm
        h_attn = self.layer_norm(h_attn)
        
        # Dropout
        h_attn = self.dropout(h_attn)
        
        return h_attn


class GAT(nn.Module):
    """
    完整的 GAT 模型
    
    多层堆叠的图注意力网络
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
        residual: bool = True
    ):
        """
        Args:
            input_dim: 输入特征维度
            hidden_dim: 隐藏层维度
            output_dim: 输出维度
            num_heads: 注意力头数量
            num_layers: 层数
            dropout: Dropout 概率
            alpha: LeakyReLU 负斜率
            residual: 是否使用残差连接
        """
        super().__init__()
        self.input_dim = input_dim
        self.hidden_dim = hidden_dim
        self.output_dim = output_dim
        self.num_layers = num_layers
        
        # 构建层
        self.layers = nn.ModuleList()
        
        # 第一层
        self.layers.append(GATLayer(
            in_features=input_dim,
            out_features=hidden_dim * num_heads,
            num_heads=num_heads,
            dropout=dropout,
            alpha=alpha,
            concat=True,
            residual=residual
        ))
        
        # 中间层
        for _ in range(num_layers - 2):
            self.layers.append(GATLayer(
                in_features=hidden_dim * num_heads,
                out_features=hidden_dim * num_heads,
                num_heads=num_heads,
                dropout=dropout,
                alpha=alpha,
                concat=True,
                residual=residual
            ))
        
        # 输出层（平均多头）
        if num_layers > 1:
            self.layers.append(GATLayer(
                in_features=hidden_dim * num_heads,
                out_features=output_dim,
                num_heads=num_heads,
                dropout=dropout,
                alpha=alpha,
                concat=False,
                residual=residual
            ))
    
    def forward(
        self,
        features: torch.Tensor,
        edge_index: torch.Tensor
    ) -> torch.Tensor:
        """
        前向传播（稀疏 edge_index 版本）
        
        Args:
            features: 节点特征 (N, input_dim)
            edge_index: 边索引 (2, E)，int64 COO 格式
            
        Returns:
            节点嵌入 (N, output_dim)
        """
        h = features
        for i, layer in enumerate(self.layers):
            h = layer(h, edge_index)
            
            # 除最后一层外，使用 ELU 激活
            if i < len(self.layers) - 1:
                h = F.elu(h)
        
        return h
    
    def get_attention_weights(
        self,
        features: torch.Tensor,
        edge_index: torch.Tensor,
        layer_idx: int = 0
    ) -> torch.Tensor:
        """
        获取注意力权重（稀疏 edge_index 版本）
        
        Args:
            features: 节点特征 (N, input_dim)
            edge_index: 边索引 (2, E)
            layer_idx: 层索引
            
        Returns:
            all_head_avg: (E,) 所有头平均后的边注意力权重
            edge_index: 对应的边 (2, E)，保持不变
        """
        h = features
        for i, layer in enumerate(self.layers):
            if i == layer_idx:
                src_idx = edge_index[0]
                dst_idx = edge_index[1]
                head_weights = []
                for att in layer.multi_head.attentions:
                    Wh = torch.mm(h, att.W)
                    Wh_src = Wh[src_idx]
                    Wh_dst = Wh[dst_idx]
                    Wh_cat = torch.cat([Wh_src, Wh_dst], dim=-1)
                    e = att.leaky_relu(torch.matmul(Wh_cat, att.a).squeeze(-1))
                    # segment softmax
                    N = h.size(0)
                    max_vals = torch.full((N,), float('-inf'), device=h.device)
                    max_vals = max_vals.scatter_reduce_(0, dst_idx, e, reduce='amax')
                    exp_e = torch.exp(e - max_vals[dst_idx])
                    sum_exp = torch.zeros(N, device=h.device).scatter_add_(0, dst_idx, exp_e)
                    alpha = exp_e / (sum_exp[dst_idx] + 1e-16)
                    head_weights.append(alpha)
                return torch.stack(head_weights).mean(dim=0)
            h = layer(h, adj)
        
        return None


if __name__ == '__main__':
    # 测试
    torch.manual_seed(42)
    
    # 创建测试数据
    num_nodes = 100
    input_dim = 64
    hidden_dim = 8
    output_dim = 64
    num_heads = 8
    
    features = torch.randn(num_nodes, input_dim)
    
    # 创建随机稀疏边索引
    src = torch.randint(0, num_nodes, (300,))
    dst = torch.randint(0, num_nodes, (300,))
    edge_index = torch.stack([src, dst], dim=0)  # (2, E)
    
    # 测试单头注意力层
    print("测试单头注意力层...")
    single_head = GraphAttentionLayer(input_dim, hidden_dim)
    out_single = single_head(features, edge_index)
    print(f"单头输出形状: {out_single.shape}")
    
    # 测试多头注意力层
    print("\n测试多头注意力层...")
    multi_head = MultiHeadGraphAttentionLayer(
        input_dim, hidden_dim, num_heads=num_heads, concat=True
    )
    out_multi = multi_head(features, edge_index)
    print(f"多头输出形状: {out_multi.shape}")
    
    # 测试完整 GAT 模型
    print("\n测试完整 GAT 模型...")
    model = GAT(
        input_dim=input_dim,
        hidden_dim=hidden_dim,
        output_dim=output_dim,
        num_heads=num_heads,
        num_layers=2
    )
    
    embeddings = model(features, edge_index)
    print(f"GAT 输出形状: {embeddings.shape}")
    print(f"GAT 参数量: {sum(p.numel() for p in model.parameters())}")
    
    # 测试注意力可视化
    attn_weights = model.get_attention_weights(features, edge_index, layer_idx=0)
    if attn_weights is not None:
        print(f"注意力权重形状: {attn_weights.shape}")
