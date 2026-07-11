#!/usr/bin/env python3
"""
Adaptive-RAG query 分类器

基于 scikit-learn (LogisticRegression) 或规则兜底，对 query 做
simple / medium / complex 三级分类。支持在线 feedback 自我更新。

集成: 供 retrieval_hub 替换原有的 classify_query_complexity 规则分类器。
"""

import os
import re
import json
import logging
import pickle
import time
from typing import Dict, List, Optional, Any
from pathlib import Path
from galaxyos.shared.paths import workspace

logger = logging.getLogger(__name__)

# 持久化路径
CLASSIFIER_PATH = os.path.join(
    os.environ.get("WORKSPACE", workspace()),
    ".learnings", "adaptive_classifier.pkl"
)
VECTORIZER_PATH = os.path.join(
    os.environ.get("WORKSPACE", workspace()),
    ".learnings", "adaptive_vectorizer.pkl"
)
FEEDBACK_PATH = os.path.join(
    os.environ.get("WORKSPACE", workspace()),
    ".learnings", "adaptive_feedback.jsonl"
)

# ========== 特征工程 ==========

# 中文技术词
_TECH_WORDS = {
    "代码", "bug", "api", "python", "架构", "配置", "部署",
    "算法", "协议", "接口", "依赖", "线程", "进程", "缓存",
    "神经网络", "训练", "优化", "损失函数", "梯度", "模型",
    "数据库", "sql", "docker", "容器", "k8s", "kubernetes",
    "前端", "后端", "微服务", "api", "sdk", "cli", "rest",
    "grpc", "protobuf", "dockerfile", "yml", "yaml", "json",
    "调试", "编译", "重构", "测试", "部署", "监控", "日志",
    "性能", "并发", "异步", "同步", "回调", "死锁", "竞态",
    "git", "commit", "pr", "merge", "branch", "diff", "patch",
    "linux", "shell", "bash", "zsh", "环境变量", "path",
    "内存", "cpu", "gpu", "磁盘", "io", "网络", "带宽",
    "加密", "认证", "授权", "oauth", "jwt", "ssl", "tls",
    "vector", "embedding", "rag", "llm", "gpt", "transformer",
    "token", "prompt", "fine-tune", "lora", "qlora", "rlhf",
    "faiss", "hnsw", "ann", "检索", "召回", "rerank", "排序",
    # 用户环境特定
    "openclaw", "claw-core", "dag", "rccam", "memory_editor",
    "retrieval_hub", "xiaoyi", "claw", "pytorch", "onnx",
}

# 复杂查询标记词
_COMPLEX_MARKERS = {
    "为什么", "如何", "怎样", "怎么", "对比", "比较", "区别",
    "原理", "机制", "流程", "步骤", "方案", "设计", "架构",
    "分析", "评估", "权衡", "优缺点", "适用场景", "最佳实践",
    "关系", "关联", "影响", "依赖链", "演进", "路线图",
    "底层", "源码", "实现细节", "性能对比", "benchmark",
}


def _extract_features(query: str) -> Dict[str, float]:
    """
    从 query 中提取数值特征（用于规则分类器和 ML 特征补充）

    Returns:
        特征字典
    """
    q_lower = query.lower()
    q_words = set(re.findall(r'[\w\u4e00-\u9fff]+', q_lower))
    q_len = len(query)
    q_word_count = len(q_words)

    tech_overlap = len(q_words & _TECH_WORDS)
    complex_overlap = len(q_words & _COMPLEX_MARKERS)

    # 句法特征
    has_question = bool(re.search(r'[?？]', query))
    has_conjunction = bool(re.search(r'和|与|及|、|vs|对比|比较', query))
    has_comparison = bool(re.search(r'vs|对比|比较|区别|versus', q_lower))
    has_code = bool(re.search(r'`[^`]+`|def |class |import |from |async |await ', query))
    has_number = bool(re.search(r'\d+', query))

    # 长度相关特征
    is_short = q_len < 10
    is_long = q_len > 50

    return {
        "q_len": float(q_len),
        "q_word_count": float(q_word_count),
        "tech_overlap": float(tech_overlap),
        "complex_overlap": float(complex_overlap),
        "has_question": 1.0 if has_question else 0.0,
        "has_conjunction": 1.0 if has_conjunction else 0.0,
        "has_comparison": 1.0 if has_comparison else 0.0,
        "has_code": 1.0 if has_code else 0.0,
        "has_number": 1.0 if has_number else 0.0,
        "is_short": 1.0 if is_short else 0.0,
        "is_long": 1.0 if is_long else 0.0,
        "tech_ratio": tech_overlap / max(q_word_count, 1),
        "complex_ratio": complex_overlap / max(q_word_count, 1),
    }


class AdaptiveClassifier:
    """
    Adaptive-RAG query 复杂度分类器

    支持两级分类：
    1. ML 模式（scikit-learn LogisticRegression，若可用）
    2. 规则兜底（始终可用）

    支持在线 feedback 收集和自我更新。
    """

    def __init__(self):
        self._ml = None  # LogisticRegression 实例
        self._vectorizer = None  # TfidfVectorizer 实例
        self._ml_available = False
        self._feedback_buffer: List[Dict] = []
        self._min_feedback_for_train = 20

        self._load_ml()

    # ── 持久化 ──

    def _load_ml(self):
        """尝试从磁盘加载已训练的模型"""
        try:
            if os.path.exists(CLASSIFIER_PATH) and os.path.exists(VECTORIZER_PATH):
                with open(CLASSIFIER_PATH, "rb") as f:
                    self._ml = pickle.load(f)
                with open(VECTORIZER_PATH, "rb") as f:
                    self._vectorizer = pickle.load(f)
                self._ml_available = True
                logger.info("AdaptiveClassifier: 加载已训练的 ML 模型")
                # 加载历史 feedback
                self._load_feedback()
        except Exception as e:
            logger.warning(f"AdaptiveClassifier: 加载 ML 模型失败: {e}")
            self._ml_available = False

    def _save_ml(self):
        """保存 ML 模型到磁盘"""
        if not self._ml_available or not self._ml or not self._vectorizer:
            return
        try:
            os.makedirs(os.path.dirname(CLASSIFIER_PATH), exist_ok=True)
            with open(CLASSIFIER_PATH, "wb") as f:
                pickle.dump(self._ml, f)
            with open(VECTORIZER_PATH, "wb") as f:
                pickle.dump(self._vectorizer, f)
            logger.info("AdaptiveClassifier: ML 模型已保存")
        except Exception as e:
            logger.warning(f"AdaptiveClassifier: 保存 ML 模型失败: {e}")

    def _load_feedback(self):
        """加载历史 feedback 数据"""
        if not os.path.exists(FEEDBACK_PATH):
            return
        try:
            with open(FEEDBACK_PATH, "r") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self._feedback_buffer.append(json.loads(line))
            logger.info(f"AdaptiveClassifier: 加载了 {len(self._feedback_buffer)} 条 feedback")
        except Exception as e:
            logger.warning(f"AdaptiveClassifier: 加载 feedback 失败: {e}")

    def _save_feedback(self, entry: Dict):
        """追加一条 feedback 记录"""
        try:
            os.makedirs(os.path.dirname(FEEDBACK_PATH), exist_ok=True)
            with open(FEEDBACK_PATH, "a") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning(f"AdaptiveClassifier: 保存 feedback 失败: {e}")

    # ── 规则分类器（始终可用） ──

    def _rule_classify(self, features: Dict[str, float]) -> Dict[str, Any]:
        """
        基于规则的 query 复杂度分类

        Returns:
            {"level": "simple"|"medium"|"complex", "strategy": str, "confidence": float}
        """
        has_code = features["has_code"] > 0
        is_short = features["is_short"] > 0
        is_long = features["is_long"] > 0
        has_conjunction = features["has_conjunction"] > 0
        has_comparison = features["has_comparison"] > 0
        has_question = features["has_question"] > 0
        tech_overlap = features["tech_overlap"]
        complex_overlap = features["complex_overlap"]
        q_len = features["q_len"]

        # 简单查询: 短、无技术词、无复杂标记
        if is_short and tech_overlap == 0 and complex_overlap == 0 and has_conjunction == 0:
            return {"level": "simple", "strategy": "quick_recall", "confidence": 0.9}

        # 复杂查询: 长 + 技术词多 + 复杂标记多 + 对比句
        complexity_score = (
            (is_long * 0.25) +
            (tech_overlap * 0.15) +
            (complex_overlap * 0.25) +
            (has_comparison * 0.2) +
            (has_conjunction * 0.1) +
            (has_code * 0.15) +
            (has_question * 0.1)
        )

        if complexity_score >= 0.6 or (complex_overlap >= 2 and tech_overlap >= 1):
            return {"level": "complex", "strategy": "deep_search", "confidence": min(1.0, complexity_score + 0.2)}

        if complexity_score >= 0.25 or tech_overlap >= 1:
            return {"level": "medium", "strategy": "standard_recall", "confidence": min(1.0, complexity_score + 0.3)}

        return {"level": "simple", "strategy": "quick_recall", "confidence": 0.8}

    # ── ML 分类器 ──

    def _ml_classify(self, query: str, features: Dict[str, float]) -> Optional[Dict[str, Any]]:
        """用 scikit-learn LogisticRegression 分类"""
        if not self._ml_available or not self._ml or not self._vectorizer:
            return None

        try:
            # 用 TF-IDF 特征
            tfidf = self._vectorizer.transform([query])
            pred = self._ml.predict(tfidf)[0]
            proba = self._ml.predict_proba(tfidf)[0]
            confidence = float(max(proba))
            level = str(pred)

            strategies = {
                "simple": "quick_recall",
                "medium": "standard_recall",
                "complex": "deep_search",
            }
            return {
                "level": level,
                "strategy": strategies.get(level, "standard_recall"),
                "confidence": round(confidence, 3),
                "ml_based": True,
            }
        except Exception as e:
            logger.warning(f"AdaptiveClassifier: ML 分类失败: {e}")
            return None

    def _train_ml(self):
        """用 feedback 数据训练/更新 ML 模型"""
        if len(self._feedback_buffer) < self._min_feedback_for_train:
            logger.info(
                f"AdaptiveClassifier: feedback 不足，需要 "
                f"{self._min_feedback_for_train} 条（当前 {len(self._feedback_buffer)}）"
            )
            return False

        try:
            from sklearn.linear_model import LogisticRegression
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.model_selection import train_test_split

            # 准备数据
            queries = []
            labels = []
            for fb in self._feedback_buffer:
                queries.append(fb.get("query", ""))
                labels.append(fb.get("actual_level", "simple"))

            if len(set(labels)) < 2:
                logger.info("AdaptiveClassifier: feedback 标签种类不足，跳过训练")
                return False

            # 训练
            vectorizer = TfidfVectorizer(
                max_features=500,
                ngram_range=(1, 2),
                token_pattern=r'(?u)\b\w+\b',
            )
            X = vectorizer.fit_transform(queries)
            y = labels

            clf = LogisticRegression(
                max_iter=200,
                multi_class='multinomial',
                solver='lbfgs',
                class_weight='balanced',
                random_state=42,
            )
            clf.fit(X, y)

            # 更新模型
            self._ml = clf
            self._vectorizer = vectorizer
            self._ml_available = True

            # 持久化
            self._save_ml()

            logger.info(
                f"AdaptiveClassifier: ML 模型训练完成 "
                f"(样本数={len(queries)}, 类别={set(labels)})"
            )
            return True
        except Exception as e:
            logger.warning(f"AdaptiveClassifier: ML 训练失败: {e}")
            self._ml_available = False
            return False

    # ── 公开接口 ──

    def classify(self, query: str) -> Dict[str, Any]:
        """
        对 query 做复杂度分类

        优先级: ML 分类 → 规则分类

        Args:
            query: 查询文本

        Returns:
            {"level": "simple"|"medium"|"complex",
             "strategy": str,
             "confidence": float}
        """
        features = _extract_features(query)

        # 优先 ML
        ml_result = self._ml_classify(query, features)
        if ml_result:
            return ml_result

        # 兜底规则
        return self._rule_classify(features)

    def feedback(self, query: str, actual_level: str, success: bool):
        """
        收集分类 feedback，用于自我更新

        Args:
            query: 原始查询
            actual_level: 实际应属的复杂度层级
            success: 本次检索是否成功（辅助信号）
        """
        entry = {
            "query": query,
            "actual_level": actual_level,
            "success": success,
            "timestamp": time.time(),
        }
        self._feedback_buffer.append(entry)
        self._save_feedback(entry)

        # 不定期触发重训练
        if len(self._feedback_buffer) >= self._min_feedback_for_train:
            trained = self._train_ml()
            if trained:
                logger.info("AdaptiveClassifier: 触发 online 训练完成")
            else:
                logger.debug("AdaptiveClassifier: training conditions not met yet")

    def get_stats(self) -> Dict[str, Any]:
        """获取分类器状态"""
        return {
            "ml_available": self._ml_available,
            "feedback_count": len(self._feedback_buffer),
            "min_feedback_for_train": self._min_feedback_for_train,
        }


# ── 全局实例 ──
_instance = None


def get_adaptive_classifier() -> AdaptiveClassifier:
    """获取全局 AdaptiveClassifier 实例"""
    global _instance
    if _instance is None:
        _instance = AdaptiveClassifier()
    return _instance


if __name__ == "__main__":
    ac = AdaptiveClassifier()
    tests = [
        "你好",
        "今天天气怎么样",
        "Python 代码中如何实现多线程",
        "对比 PyTorch 和 TensorFlow 在分布式训练上的架构差异",
        "贵州兴义 PyTorch 开发",
        "为什么 RAG 系统的检索质量会下降",
        "帮我写个 Hello World",
    ]
    for t in tests:
        r = ac.classify(t)
        print(f"  [{r['level']:7s}] (conf={r['confidence']:.2f}) {t}")
    print(f"\nStats: {ac.get_stats()}")
