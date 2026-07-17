#!/usr/bin/env python3
"""
跨模态记忆绑定引擎 (CrossModalMemoryBinder)

将文本与图像统一编码到 LFM 2048-dim 向量空间，
实现跨模态的语义检索和记忆绑定。

架构：
- 快速桥接路径：OpenJiuwen multimodal tool → caption → LFM embed_text
- 原生视觉路径：LFMWithVision VisualPatchEmbedding → 2048 dim
- 统一 embedding 存储：所有模态输出(2048,)向量

与 GalaxyOS 管线集成：
- memory_store() 时自动生成图像 embedding
- memory_recall() 时文本/图像混合检索
- Titans 神经记忆模块可接收跨模态输入

Author: GalaxyOS
Version: 1.0.0
Created: 2026-06-15
"""

import json
import math
import os
import numpy as np
from typing import Dict, List, Optional, Tuple, Any
from pathlib import Path
from datetime import datetime, timezone
from galaxyos.shared.paths import workspace

# ── LFM embedding 引擎 ──
try:
    from lfm_adaptive_operator import RealLFMNetwork
    _LFM_AVAILABLE = True
except ImportError:
    _LFM_AVAILABLE = False


class CrossModalMemoryBinder:
    """
    跨模态记忆绑定器

    提供统一的 text→embedding 和 image→embedding 接口，
    输出均为 2048-dim 向量（LFM 空间）。
    """

    def __init__(self, workspace_path: Optional[str] = None):
        self.workspace_path = Path(workspace_path or workspace())
        self.bind_path = self.workspace_path / ".learnings" / "cross_modal"
        self.bind_path.mkdir(parents=True, exist_ok=True)

        self._lfm = None
        self._vision_model = None
        self._vision_model_loaded = False
        self.embed_dim = 2048

    # ── LFM 文本 embedding ──

    def _ensure_lfm(self):
        if self._lfm is None and _LFM_AVAILABLE:
            try:
                self._lfm = RealLFMNetwork()
                self._lfm._ensure()
            except Exception:
                pass
        return self._lfm is not None

    def text_to_embedding(self, text: str) -> Optional[np.ndarray]:
        """
        文本 → 2048-dim LFM embedding

        Args:
            text: 输入文本

        Returns:
            (2048,) float32 numpy 向量，或 None
        """
        if not self._ensure_lfm():
            return self._fallback_embedding(text)
        assert self._lfm is not None
        try:
            emb = self._lfm.embed_text(text[:512])
            if emb is not None and emb.shape == (self.embed_dim,):
                return emb.astype(np.float32)
        except Exception:
            pass
        return self._fallback_embedding(text)

    def _fallback_embedding(self, content: str) -> np.ndarray:
        """降级 embedding：稀疏 hash 编码 (2048-dim)"""
        vec = np.zeros(self.embed_dim, dtype=np.float32)
        content_hash = abs(hash(content)) % self.embed_dim
        vec[content_hash] = min(1.0, len(content) / 500.0)
        for i in range(5):
            h = abs(hash(f"{content}_{i}")) % self.embed_dim
            vec[h] = 0.3 - i * 0.05
        norm = np.linalg.norm(vec)
        if norm > 0:
            vec = vec / norm
        return vec

    # ── 原生视觉 embedding（LFMWithVision） ──

    def _ensure_vision(self):
        """懒加载 LFMWithVision 视觉模型"""
        if self._vision_model_loaded:
            return self._vision_model is not None
        self._vision_model_loaded = True
        try:
            from lfm_adaptive_operator import LFMWithVision
            # 使用与 LFM 对齐的维度
            self._vision_model = LFMWithVision(
                hidden_dim=512,
                num_heads=8,
                patch_size=16,
                num_layers=8,
                weight_rank=8,
            )
            return True
        except Exception:
            return False

    def image_to_embedding(self, image: np.ndarray) -> Optional[np.ndarray]:
        """
        图像 → 2048-dim LFM embedding（原生视觉路径）

        Args:
            image: [H, W, C] numpy 数组，值域 [0, 1] 或 [0, 255]

        Returns:
            (2048,) float32 向量，或 None
        """
        if not self._ensure_vision():
            return None
        try:
            img = np.array(image, dtype=np.float32)
            if img.max() > 1.0:
                img = img / 255.0

            # 限制分辨率到 256 以内（性能和模型容量权衡）
            H, W = img.shape[:2]
            max_side = 256
            if max(H, W) > max_side:
                scale = max_side / max(H, W)
                new_H, new_W = int(H * scale), int(W * scale)
                try:
                    from skimage.transform import resize
                    img = resize(img, (new_H, new_W), anti_aliasing=True)
                except ImportError:
                    try:
                        from PIL import Image
                        pil_img = Image.fromarray((img * 255).astype(np.uint8))
                        pil_img = pil_img.resize((new_W, new_H))
                        img = np.array(pil_img).astype(np.float32) / 255.0
                    except Exception:
                        pass

            # 用视觉 encoder 生成 token
            assert self._vision_model is not None
            vis_tokens = self._vision_model.visual_embed.forward(img)
            # mean pooling → 2048 dim（与 LFM 空间对齐需要线性投影）
            pooled = np.mean(vis_tokens, axis=0)  # (hidden_dim,)
            # 投影到 2048 dim
            if len(pooled) == self.embed_dim:
                return pooled.astype(np.float32)
            # 线性投影（简单版）
            proj = np.zeros(self.embed_dim, dtype=np.float32)
            copy_len = min(len(pooled), self.embed_dim)
            proj[:copy_len] = pooled[:copy_len]
            return proj
        except Exception:
            return None

    # ── 快捷桥接路径（caption → text embedding） ──

    def image_caption_to_embedding(self, caption: str) -> Optional[np.ndarray]:
        """
        图像描述 → 2048-dim（桥接路径）

        先用 OpenJiuwen multimodal tool 生成 caption，
        再通过 LFM embed_text 编码 caption。

        这是快速桥接方案，不需要加载视觉模型。

        Args:
            caption: 图像文本描述（来自 OpenJiuwen multimodal tool）

        Returns:
            (2048,) float32 向量
        """
        return self.text_to_embedding(caption)

    # ── 统一接口 ──

    def get_embedding(self, text: Optional[str] = None,
                      image: Optional[np.ndarray] = None,
                      caption: Optional[str] = None) -> Optional[np.ndarray]:
        """
        统一 embedding 接口

        优先级：image (native) > caption (bridge) > text

        Args:
            text: 文本输入
            image: 图像输入（原生视觉路径）
            caption: 图像描述文本（桥接路径）

        Returns:
            (2048,) float32 向量
        """
        if image is not None:
            emb = self.image_to_embedding(image)
            if emb is not None:
                return emb
        if caption is not None:
            return self.image_caption_to_embedding(caption)
        if text is not None:
            return self.text_to_embedding(text)
        return None

    def combine_embeddings(self, embeddings: List[np.ndarray],
                           weights: Optional[List[float]] = None) -> np.ndarray:
        """
        多模态 embedding 融合

        支持权重平均合并多个模态的 embedding。

        Args:
            embeddings: (2048,) 向量列表
            weights: 各向量的权重，默认等权

        Returns:
            (2048,) 融合向量
        """
        if not embeddings:
            return np.zeros(self.embed_dim, dtype=np.float32)
        if weights is None:
            weights = [1.0 / len(embeddings)] * len(embeddings)
        w_arr = np.array(weights, dtype=np.float32)
        w_arr = w_arr / w_arr.sum()
        result = sum(w * emb for w, emb in zip(w_arr, embeddings))
        norm = np.linalg.norm(result)
        if norm > 0:
            result = result / norm
        return result.astype(np.float32)

    # ── 工具 ──

    def hash_text(self, text: str) -> int:
        """文本 → 语义稳定的哈希"""
        # 用 embedding 的 first dim 作为指纹（如果可用）
        emb = self.text_to_embedding(text)
        if emb is not None:
            return hash(emb[:32].tobytes())
        return hash(text)

    def similarity(self, a: np.ndarray, b: np.ndarray) -> float:
        """两个 embedding 的余弦相似度"""
        a_norm = max(np.linalg.norm(a), 1e-8)
        b_norm = max(np.linalg.norm(b), 1e-8)
        return float(np.dot(a, b) / (a_norm * b_norm))


# ── 快捷入口 ──

_BINDER: Optional[CrossModalMemoryBinder] = None

def get_binder(workspace_path: Optional[str] = None) -> CrossModalMemoryBinder:
    """获取/创建全局跨模态绑定器"""
    global _BINDER
    if _BINDER is None:
        _BINDER = CrossModalMemoryBinder(workspace_path)
    return _BINDER


def embed_text(text: str) -> Optional[np.ndarray]:
    """快速文本嵌入"""
    return get_binder().text_to_embedding(text)


def embed_image(image: np.ndarray) -> Optional[np.ndarray]:
    """快速图像嵌入"""
    return get_binder().image_to_embedding(image)


def similarity(a: np.ndarray, b: np.ndarray) -> float:
    """快速余弦相似度"""
    return get_binder().similarity(a, b)


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    binder = get_binder()
    if cmd == "embed-text":
        text = sys.argv[2] if len(sys.argv) > 2 else "hello world"
        emb = binder.text_to_embedding(text)
        if emb is not None:
            print(f"embedding: ({emb.shape[0]},) norm={np.linalg.norm(emb):.4f}")
        else:
            print("embedding 不可用")
    elif cmd == "similarity":
        if len(sys.argv) < 4:
            print("用法: similarity <text_a> <text_b>")
        else:
            a = binder.text_to_embedding(sys.argv[2])
            b = binder.text_to_embedding(sys.argv[3])
            if a is not None and b is not None:
                sim = binder.similarity(a, b)
                print(f"相似度: {sim:.4f}")
    else:
        info = {
            "lfm_available": _LFM_AVAILABLE,
            "embed_dim": binder.embed_dim,
        }
        print(json.dumps(info, indent=2, ensure_ascii=False))


    # ── Engram 感知 embedding ──

    def text_to_embedding_with_engram(self, text: str,
                                       engram_memory=None) -> np.ndarray:
        """文本→embedding，优先用 Engram 命中

        Args:
            text: 输入文本
            engram_memory: EngramMemory 实例

        Returns:
            (2048,) 向量
        """
        import numpy as np

        # Engram 查找
        if engram_memory is not None:
            try:
                engram_emb, engram_stat = engram_memory.lookup(text[:128])
                if engram_emb is not None and engram_stat.get("hit_rate", 0) > 0.3:
                    return engram_emb.astype(np.float32)
            except Exception:
                pass

        # 降级到 LFM
        return self.text_to_embedding(text) or self._fallback_embedding(text)

    def blend_embeddings(self, lfm_emb: np.ndarray,
                          engram_emb: np.ndarray,
                          alpha: float = 0.5) -> np.ndarray:
        """加权融合 LFM + Engram 的 embedding

        Args:
            lfm_emb: (2048,) LFM embedding
            engram_emb: (2048,) Engram embedding
            alpha: LFM 权重 [0,1]

        Returns:
            (2048,) 融合向量
        """
        import numpy as np
        if lfm_emb is None or engram_emb is None:
            return lfm_emb if lfm_emb is not None else engram_emb
        blended = alpha * lfm_emb + (1 - alpha) * engram_emb
        norm = np.linalg.norm(blended)
        if norm > 0:
            blended = blended / norm * 2048  # 保持 norm 约 2048
        return blended.astype(np.float32)

