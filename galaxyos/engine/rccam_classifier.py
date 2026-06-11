"""
R-CCAM 查询分类器

混合方案:
1. ≤3 字 → 启发式快速判定（零成本兜底）
2. 否则 → TF-IDF + LogisticRegression 模型判定
3. 置信度 < 0.55 → 回退到启发式规则（防漏判）

模型路径: data/rccam_classifier_v1.pkl (～129KB)
"""

import os
import pickle
import logging

logger = logging.getLogger("rccam_classifier")

# 缓存单例
_classifier = None  # { "vectorizer": ..., "model": ... }

_heuristic_simple_kws = [
    '你好', 'hi', 'hello', '嗯', '好的', 'ok', 'okay', '谢谢',
    '哈哈', '是的', '对', 'no', 'yes', '在吗', '不错', '可以', '好',
    '试试', '知道了', '收到', '没事', '拜拜', '再见', '辛苦了',
    '晚安', '早安', '午安', '继续', '然后呢', '还有呢', '知道了',
    '明白了', '搞定了', '厉害了', '太棒了', '真棒', '牛', 'nice',
    'great', 'cool', 'perfect', 'thx', 'thanks', 'lol', 'hehe',
    '好吧', '行吧', '那行', '好使', '确实', '没错', '对呀',
    '好呀', '好哦', '好滴', '好哒', '哦', '噢', '哦哦', '嗯嗯',
    '额', '呃', '话说',
]

HEURISTIC_ONLY_MAX_LEN = 3  # ≤3 字走纯启发式
ML_FALLBACK_THRESHOLD = 0.55  # ML 置信度低于此值 → 回退启发式


def _load():
    """加载模型（懒加载 + 缓存）"""
    global _classifier
    if _classifier is not None:
        return _classifier
    # 修复 BUG-4: 原代码 `dirname(dirname(__file__))` 解析到 /workspace/galaxyos/，
    # 但 pkl 真实在 /workspace/data/ 下，永远找不到，ML 模型从未加载过。
    # 改为多候选路径 + 存在性探测。
    _base = os.path.dirname(os.path.abspath(__file__))
    _candidates = [
        os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(_base))),
                     "data", "rccam_classifier_v1.pkl"),  # repo_root/data/
        os.path.join(os.path.dirname(os.path.dirname(_base)),
                     "data", "rccam_classifier_v1.pkl"),  # galaxyos/data/ (兼容旧布局)
        "/workspace/data/rccam_classifier_v1.pkl",  # 绝对路径兜底
    ]
    model_path = next((p for p in _candidates if os.path.exists(p)), _candidates[0])
    if not os.path.exists(model_path):
        logger.warning(f"分类器模型未找到: {model_path}，使用纯启发式")
        return None
    try:
        with open(model_path, "rb") as f:
            _classifier = pickle.load(f)
        logger.info(f"分类器模型已加载 ({os.path.getsize(model_path)} bytes) from {model_path}")
        return _classifier
    except Exception as e:
        logger.warning(f"分类器模型加载失败: {e}，使用纯启发式")
        return None


def _heuristic(msg: str) -> bool:
    """纯启发式判定：是否 simple（True=simple/直接回答）"""
    m = msg.strip()
    if not m:
        return True
    # 非常短 → simple
    if len(m) <= HEURISTIC_ONLY_MAX_LEN:
        return True
    # 含问号 → complex（可能是真问题）
    if '?' in m or '？' in m:
        return False
    # 含“帮”字 → 大概率需要检索/复杂处理
    if '帮' in m:
        return False
    # 关键词匹配
    if any(kw in m.lower() for kw in _heuristic_simple_kws):
        return True
    return False


def classify(user_input: str) -> dict:
    """
    判定用户输入是否需要走完整 R-CCAM 管线。

    返回:
        {
            "is_simple": bool,   # True=直答, False=走完整管线
            "confidence": float, # 判定置信度 (0~1)
            "method": str,       # "heuristic" | "ml" | "ml+fallback"
        }
    """
    msg = user_input.strip()
    if not msg:
        return {"is_simple": True, "confidence": 1.0, "method": "heuristic"}

    # Stage 1: 纯启发式兜底（≤3 字零成本）
    if len(msg) <= HEURISTIC_ONLY_MAX_LEN:
        is_simple = _heuristic(msg)
        return {
            "is_simple": is_simple,
            "confidence": 0.9 if is_simple else 0.7,
            "method": "heuristic",
        }

    # Stage 2: ML 判定
    clf = _load()
    if clf is not None:
        try:
            X = clf["vectorizer"].transform([msg])
            probs = clf["model"].predict_proba(X)[0]
            # model.classes_ 可能是 [0, 1] 或 [1, 0]
            classes = clf["model"].classes_
            # 找 simple 标签 (1) 的概率
            if len(classes) == 2:
                simple_idx = 1 if classes[1] == 1 else 0
                prob_simple = float(probs[simple_idx])
                prob_complex = 1.0 - prob_simple
            else:
                prob_simple = float(probs[0])
                prob_complex = 1.0 - prob_simple

            confidence = max(prob_simple, prob_complex)
            is_simple = prob_simple >= 0.5

            # Stage 3: 低置信度回退到启发式
            if confidence < ML_FALLBACK_THRESHOLD:
                heu_simple = _heuristic(msg)
                if heu_simple != is_simple:
                    # ML 低置信度且与启发式矛盾 → 倾向用 ML 结果但降低置信度
                    return {
                        "is_simple": is_simple,
                        "confidence": confidence,
                        "method": "ml+fallback",
                    }

            return {
                "is_simple": is_simple,
                "confidence": confidence,
                "method": "ml",
            }
        except Exception as e:
            logger.warning(f"ML 分类器推理失败: {e}")

    # Fallback: 纯启发式
    is_simple = _heuristic(msg)
    return {
        "is_simple": is_simple,
        "confidence": 0.7,
        "method": "heuristic",
    }
