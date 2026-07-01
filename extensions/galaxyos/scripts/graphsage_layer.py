"""
GraphSAGE Layer - 图采样聚合层
参考论文: Inductive Representation Learning on Large Graphs (https://arxiv.org/abs/1706.02216)
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import Optional, Tuple, List, Union
import numpy as np


class MeanAggregator(nn.Module):
    """均值聚合器"""
    
    def __init__(self, input_dim: int, output_dim: int, bias: bool = True):
        super().__init__()
        self.input_dim = input_dim
        self.output_dim = output_dim
        
        self.linear = nn.Linear(input_dim, output_dim, bias=bias)
    
    def forward(
        self, 
        features: torch.Tensor,
        neighbors: torch.Tensor,
        num_neighbors: torch.Tensor
    ) -> torch.Tensor:
        """
        Args:
            features: 节点特征 (num_nodes, input_dim)
            neighbors: 邻居索引 (num_nodes, max_neighbors)
            num_neighbors: 每个节点的邻居数量 (num_nodes,)
            
        Returns:
            聚合后的特征 (num_nodes, output_dim)
        """
        num_nodes = features.size(0)
        max_neighbors = neighbors.size(1)
        
        # 获取邻居特征
        # 扩展 features 以便索引
        neighbor_features = features[neighbors]  # (num_nodes, max_neighbors, input_dim)
        
        # 创建掩码处理填充的邻居
        mask = torch.arange(max_neighbors, device=features.device).unsqueeze(0) < num_neighbors.unsqueeze(1)
        mask = mask.unsqueeze(-1).float()  # (num_nodes, max_neighbors, 1)
        
        # 计算均值
        masked_features = neighbor_features * mask
        sum_features = masked_features.sum(dim=1)  # (num_nodes, input_dim)
        mean_features = sum_features / (num_neighbors.unsqueeze(-1).float() + 1e-8)
        
        # 线性变换
        output = self.linear(mean_features)
        
        return output


class LSTMAggregator(nn.Module):
    """LSTM 聚合器"""
    
    def __init__(self, input_dim: int, output_dim: int, hidden_dim: int = 128):
        super().__init__()
        self.input_dim = input_dim
        self.output_dim = output_dim
        self.hidden_dim = hidden_dim
        
        self.lstm = nn.LSTM(input_dim, hidden_dim, batch_first=True)
        self.linear = nn.Linear(hidden_dim, output_dim)
    
    def forward(
        self,
        features: torch.Tensor,
        neighbors: torch.Tensor,
        num_neighbors: torch.Tensor
    ) -> torch.Tensor:
        """
        Args:
            features: 节点特征 (num_nodes, input_dim)
            neighbors: 邻居索引 (num_nodes, max_neighbors)
            num_neighbors: 每个节点的邻居数量 (num_nodes,)
            
        Returns:
            聚合后的特征 (num_nodes, output_dim)
        """
        num_nodes = features.size(0)
        max_neighbors = neighbors.size(1)
        
        # 获取邻居特征
        neighbor_features = features[neighbors]  # (num_nodes, max_neighbors, input_dim)
        
        # LSTM 编码
        lstm_out, (h_n, c_n) = self.lstm(neighbor_features)
        
        # 使用最后一个隐藏状态
        aggregated = h_n.squeeze(0)  # (num_nodes, hidden_dim)
        
        # 线性变换
        output = self.linear(aggregated)
        
        return output


class PoolingAggregator(nn.Module):
    """池化聚合器"""
    
    def __init__(
        self, 
        input_dim: int, 
        output_dim: int,
        hidden_dim: int = 128,
        pool_type: str = 'max'
    ):
        super().__init__()
        self.input_dim = input_dim
        self.output_dim = output_dim
        self.hidden_dim = hidden_dim
        self.pool_type = pool_type
        
        self.fc1 = nn.Linear(input_dim, hidden_dim)
        self.fc2 = nn.Linear(hidden_dim, output_dim)
    
    def forward(
        self,
        features: torch.Tensor,
        neighbors: torch.Tensor,
        num_neighbors: torch.Tensor
    ) -> torch.Tensor:
        """
        Args:
            features: 节点特征 (num_nodes, input_dim)
            neighbors: 邻居索引 (num_nodes, max_neighbors)
            num_neighbors: 每个节点的邻居数量 (num_nodes,)
            
        Returns:
            聚合后的特征 (num_nodes, output_dim)
        """
        num_nodes = features.size(0)
        max_neighbors = neighbors.size(1)
        
        # 获取邻居特征
        neighbor_features = features[neighbors]  # (num_nodes, max_neighbors, input_dim)
        
        # 通过 MLP
        hidden = F.relu(self.fc1(neighbor_features))  # (num_nodes, max_neighbors, hidden_dim)
        
        # 创建掩码
        mask = torch.arange(max_neighbors, device=features.device).unsqueeze(0) < num_neighbors.unsqueeze(1)
        mask = mask.unsqueeze(-1).float()
        hidden = hidden * mask + (-1e9) * (1 - mask)
        
        # 池化
        if self.pool_type == 'max':
            pooled = hidden.max(dim=1)[0]  # (num_nodes, hidden_dim)
        else:  # mean
            pooled = (hidden * mask).sum(dim=1) / (mask.sum(dim=1) + 1e-8)
        
        # 输出层
        output = self.fc2(pooled)
        
        return output


class GraphSAGELayer(nn.Module):
    """
    GraphSAGE 单层
    
    实现:
    h_v^k = σ(W^k · CONCAT(h_v^{k-1}, AGGREGATE({h_u^{k-1}, ∀u ∈ N(v)})))
    """
    
    def __init__(
        self,
        input_dim: int,
        output_dim: int,
        aggregator_type: str = 'mean',
        dropout: float = 0.5,
        bias: bool = True,
        normalize: bool = True
    ):
        """
        Args:
            input_dim: 输入特征维度
            output_dim: 输出特征维度
            aggregator_type: 聚合器类型 ('mean', 'lstm', 'pool')
            dropout: Dropout 概率
            bias: 是否使用偏置
            normalize: 是否对输出进行 L2 归一化
        """
        super().__init__()
        self.input_dim = input_dim
        self.output_dim = output_dim
        self.aggregator_type = aggregator_type
        self.dropout = dropout
        self.normalize = normalize
        
        # 选择聚合器
        if aggregator_type == 'mean':
            self.aggregator = MeanAggregator(input_dim, output_dim, bias=bias)
        elif aggregator_type == 'lstm':
            self.aggregator = LSTMAggregator(input_dim, output_dim)
        elif aggregator_type == 'pool':
            self.aggregator = PoolingAggregator(input_dim, output_dim)
        else:
            raise ValueError(f"Unknown aggregator type: {aggregator_type}")
        
        # 自身特征的线性变换
        self.self_linear = nn.Linear(input_dim, output_dim, bias=bias)
        
        self.dropout_layer = nn.Dropout(dropout)
    
    def forward(
        self,
        features: torch.Tensor,
        adj_list: List[List[int]],
        num_samples: int = 10
    ) -> torch.Tensor:
        """
        前向传播
        
        Args:
            features: 节点特征 (num_nodes, input_dim)
            adj_list: 邻接表，adj_list[i] 是节点 i 的邻居列表
            num_samples: 采样的邻居数量
            
        Returns:
            更新后的节点特征 (num_nodes, output_dim)
        """
        num_nodes = features.size(0)
        device = features.device
        
        # 采样邻居
        sampled_neighbors, num_neighbors = self._sample_neighbors(
            adj_list, num_nodes, num_samples, device
        )
        
        # 聚合邻居特征
        aggregated = self.aggregator(features, sampled_neighbors, num_neighbors)
        
        # 自身特征变换
        self_features = self.self_linear(features)
        
        # 拼接并激活
        combined = torch.cat([self_features, aggregated], dim=-1)
        
        # 如果维度不匹配，添加一个投影层
        if combined.size(-1) != self.output_dim:
            if not hasattr(self, 'projection'):
                self.projection = nn.Linear(combined.size(-1), self.output_dim).to(device)
            output = self.projection(combined)
        else:
            output = combined
        
        output = F.relu(output)
        output = self.dropout_layer(output)
        
        # L2 归一化
        if self.normalize:
            output = F.normalize(output, p=2, dim=-1)
        
        return output
    
    def _sample_neighbors(
        self,
        adj_list: List[List[int]],
        num_nodes: int,
        num_samples: int,
        device: torch.device
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        """采样邻居"""
        max_neighbors = min(
            max(len(neighbors) for neighbors in adj_list) if adj_list else 1,
            num_samples
        )
        
        sampled = np.zeros((num_nodes, max_neighbors), dtype=np.int64)
        num_neighbors = np.zeros(num_nodes, dtype=np.int64)
        
        for i in range(num_nodes):
            neighbors = adj_list[i] if i < len(adj_list) else []
            if len(neighbors) > 0:
                # 随机采样
                if len(neighbors) > num_samples:
                    sampled_neighbors = np.random.choice(neighbors, num_samples, replace=False)
                else:
                    sampled_neighbors = neighbors
                
                num_neighbor = len(sampled_neighbors)
                sampled[i, :num_neighbor] = sampled_neighbors
                num_neighbors[i] = num_neighbor
        
        return (
            torch.tensor(sampled, dtype=torch.long, device=device),
            torch.tensor(num_neighbors, dtype=torch.long, device=device)
        )


class GraphSAGE(nn.Module):
    """
    完整的 GraphSAGE 模型
    
    多层堆叠的 GraphSAGE
    """
    
    def __init__(
        self,
        input_dim: int,
        hidden_dims: List[int],
        output_dim: int,
        aggregator_type: str = 'mean',
        dropout: float = 0.5,
        num_samples_list: Optional[List[int]] = None
    ):
        """
        Args:
            input_dim: 输入特征维度
            hidden_dims: 隐藏层维度列表
            output_dim: 输出维度
            aggregator_type: 聚合器类型
            dropout: Dropout 概率
            num_samples_list: 每层采样的邻居数量
        """
        super().__init__()
        self.input_dim = input_dim
        self.hidden_dims = hidden_dims
        self.output_dim = output_dim
        self.num_layers = len(hidden_dims) + 1
        
        if num_samples_list is None:
            num_samples_list = [10] * self.num_layers
        self.num_samples_list = num_samples_list
        
        # 构建层
        self.layers = nn.ModuleList()
        
        dims = [input_dim] + hidden_dims + [output_dim]
        for i in range(len(dims) - 1):
            layer = GraphSAGELayer(
                input_dim=dims[i],
                output_dim=dims[i + 1],
                aggregator_type=aggregator_type,
                dropout=dropout if i < len(dims) - 2 else 0.0
            )
            self.layers.append(layer)
    
    def forward(
        self,
        features: torch.Tensor,
        adj_list: List[List[int]]
    ) -> torch.Tensor:
        """
        前向传播
        
        Args:
            features: 节点特征 (num_nodes, input_dim)
            adj_list: 邻接表
            
        Returns:
            节点嵌入 (num_nodes, output_dim)
        """
        h = features
        
        for i, layer in enumerate(self.layers):
            h = layer(h, adj_list, self.num_samples_list[i])
        
        return h
    
    def get_embeddings(
        self,
        features: torch.Tensor,
        adj_list: List[List[int]]
    ) -> torch.Tensor:
        """获取节点嵌入（不计算梯度）"""
        with torch.no_grad():
            return self.forward(features, adj_list)


if __name__ == '__main__':
    # 测试
    torch.manual_seed(42)
    
    # 创建测试数据
    num_nodes = 100
    input_dim = 64
    hidden_dim = 128
    output_dim = 64
    
    features = torch.randn(num_nodes, input_dim)
    adj_list = [list(np.random.choice(num_nodes, size=np.random.randint(1, 10), replace=False)) 
                for _ in range(num_nodes)]
    
    # 测试单层
    layer = GraphSAGELayer(input_dim, hidden_dim)
    output = layer(features, adj_list)
    print(f"单层输出形状: {output.shape}")
    
    # 测试完整模型
    model = GraphSAGE(
        input_dim=input_dim,
        hidden_dims=[128, 64],
        output_dim=output_dim,
        aggregator_type='mean'
    )
    
    embeddings = model(features, adj_list)
    print(f"模型输出形状: {embeddings.shape}")
    print(f"模型参数量: {sum(p.numel() for p in model.parameters())}")
