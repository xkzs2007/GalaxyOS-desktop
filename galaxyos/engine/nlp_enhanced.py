#!/usr/bin/env python3
"""
增强 NLP 模块 (NLP Enhanced)

基于论文方向扩展基础 NLP 能力：
1. 依存句法分析 — 基于词性模板的轻量依赖解析
2. 实体链接 — 将命名实体映射到系统知识库
3. 指代消解 — 基于就近原则的代词解析
4. 对比句检测 — 比较关系抽取与图谱构建

Author: GalaxyOS
Version: 1.0.0
Created: 2026-05-14
"""

import re
import json
import os
import math
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any, Set
from dataclasses import dataclass, field
from collections import OrderedDict
from galaxyos.shared.paths import workspace

try:
    import jieba
    import jieba.posseg as pseg
    JIEBA_AVAILABLE = True
except ImportError:
    JIEBA_AVAILABLE = False


# ==================== 1. 依存句法分析 ====================

@dataclass
class DependencyRelation:
    """依存关系"""
    head_word: str        # 核心词
    head_pos: str         # 核心词词性
    dep_word: str         # 依存词
    dep_pos: str          # 依存词词性
    relation: str         # 依存关系类型
    head_idx: int = -1    # 核心词位置
    dep_idx: int = -1     # 依存词位置


@dataclass
class DependencyParseResult:
    """依存分析结果"""
    tokens: List[Tuple[str, str]]  # (词, 词性)
    relations: List[DependencyRelation]
    root_word: Optional[str] = None
    root_idx: int = -1


class LightweightDependencyParser:
    """
    轻量依存句法分析器

    基于词性模板匹配，不做神经网络。
    覆盖中文核心依存关系：
    - 主谓关系 (SBV): 名词+动词
    - 动宾关系 (VOB): 动词+名词
    - 定中关系 (ATT): 形容词/名词+名词
    - 状中关系 (ADV): 副词+动词/形容词
    - 动补关系 (CMP): 动词+趋向动词/形容词
    - 并列关系 (COO): 连词连接
    - 介宾关系 (POB): 介词+名词
    """

    # 词性模板 → 依存关系映射
    POS_PATTERNS = [
        # 主谓：名词(主语) + 动词(谓语)
        (("v", "n"), "SBV", 1, 0),     # 动词在前名词在后 → 名词是主语
        (("n", "v"), "SBV", 0, 1),     # 名词在前动词在后 → 名词是主语
        (("r", "v"), "SBV", 0, 1),     # 代词+动词 → 代词是主语
        # 动宾：动词 + 名词
        (("v", "n"), "VOB", 0, 1),     # 动词+名词 → 名词是宾语
        (("v", "ns"), "VOB", 0, 1),    # 动词+地名 → 地名是宾语
        (("v", "vn"), "VOB", 0, 1),    # 动词+名动词
        # 定中：形容词/名词 + 名词
        (("a", "n"), "ATT", 0, 1),     # 形容词+名词 → 形容词修饰名词
        (("n", "n"), "ATT", 0, 1),     # 名词+名词 → 前修饰后
        (("m", "n"), "ATT", 0, 1),     # 数词+名词 → 数量修饰
        (("q", "n"), "ATT", 0, 1),     # 量词+名词
        # 状中：副词 + 动词/形容词
        (("d", "v"), "ADV", 0, 1),     # 副词+动词
        (("d", "a"), "ADV", 0, 1),     # 副词+形容词
        # 动补：动词 + 趋向/结果
        (("v", "v"), "CMP", 0, 1),     # 动词+动词 → 后为补语
        (("v", "a"), "CMP", 0, 1),     # 动词+形容词 → 补语
        # 介宾：介词 + 名词
        (("p", "n"), "POB", 0, 1),     # 介词+名词
        (("p", "ns"), "POB", 0, 1),    # 介词+地名
        (("p", "r"), "POB", 0, 1),     # 介词+代词
    ]

    # 标点词性
    PUNCT_POS = {"w", "x"}
    # 不做核心词的词性
    NON_HEAD_POS = {"u", "uj", "ul", "ud", "c", "p", "m", "q"}

    def parse(self, text: str) -> DependencyParseResult:
        """
        对句子做依存分析

        Args:
            text: 输入文本

        Returns:
            依存分析结果
        """
        if not JIEBA_AVAILABLE:
            return DependencyParseResult(tokens=[], relations=[])

        words = list(pseg.cut(text))
        tokens = [(w.word, w.flag) for w in words]

        relations = []

        # 滑动窗口匹配词性模板
        for i in range(len(words)):
            for j in range(i + 1, min(i + 3, len(words))):
                w1, p1 = words[i].word, words[i].flag
                w2, p2 = words[j].word, words[j].flag

                if p1 in self.PUNCT_POS or p2 in self.PUNCT_POS:
                    continue

                # 匹配模板
                for (pos1, pos2), rel, head_idx, dep_idx in self.POS_PATTERNS:
                    if self._pos_match(p1, pos1) and self._pos_match(p2, pos2):
                        head = w1 if head_idx == 0 else w2
                        head_p = p1 if head_idx == 0 else p2
                        dep = w1 if dep_idx == 0 else w2
                        dep_p = p1 if dep_idx == 0 else p2

                        # 过滤过短的词
                        if len(head) < 1 or len(dep) < 1:
                            continue

                        relations.append(DependencyRelation(
                            head_word=head, head_pos=head_p,
                            dep_word=dep, dep_pos=dep_p,
                            relation=rel,
                            head_idx=i if head_idx == 0 else j,
                            dep_idx=i if dep_idx == 0 else j
                        ))

        # 找出核心词（未被任何关系做依赖词的词）
        dep_indices = {r.dep_idx for r in relations}
        root_idx = -1
        for i, (word, pos) in enumerate(tokens):
            if pos not in self.PUNCT_POS and pos not in self.NON_HEAD_POS:
                if i not in dep_indices:
                    root_idx = i
                    break

        root_word = tokens[root_idx][0] if root_idx >= 0 else (tokens[0][0] if tokens else None)

        return DependencyParseResult(
            tokens=tokens,
            relations=relations,
            root_word=root_word,
            root_idx=root_idx
        )

    def _pos_match(self, actual: str, pattern: str) -> bool:
        """词性匹配（支持前缀匹配）"""
        return actual == pattern or actual.startswith(pattern)

    def extract_triple(self, text: str) -> Optional[Tuple[str, str, str]]:
        """
        从句子中提取主谓宾三元组

        适用：简单的 "主语 + 谓语 + 宾语" 结构

        Returns:
            (主语, 谓语, 宾语) 或 None
        """
        result = self.parse(text)

        subject = None
        verb = None
        obj = None

        for rel in result.relations:
            if rel.relation == "SBV":
                if rel.head_pos.startswith("v"):
                    verb = rel.head_word
                subject = rel.dep_word
            elif rel.relation == "VOB":
                obj = rel.dep_word
                if rel.head_word and not verb:
                    verb = rel.head_word

        if subject and verb:
            return (subject, verb, obj or "")

        return None


# ==================== 2. 实体链接 ====================

@dataclass
class KnowledgeEntity:
    """知识库实体"""
    name: str
    aliases: List[str] = field(default_factory=list)
    type: str = "concept"
    description: str = ""
    source: str = ""
    keywords: List[str] = field(default_factory=list)


class EntityLinker:
    """
    实体链接器

    将 NLP 抽取出的命名实体映射到系统知识库，
    为检索提供精确的实体→文档/记忆跳转。
    """

    def __init__(self, workspace_path: str = None):
        workspace = workspace_path or os.environ.get(
            "OPENCLAW_WORKSPACE",
            workspace())

        # 内置知识库
        self._builtin_entities = self._build_system_kb(workspace)

        # 持久化路径
        self.custom_path = Path(workspace) / ".learnings" / "entity_linker.jsonl"
        self.custom_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.custom_path.exists():
            self.custom_path.touch()

        # 缓存
        self._custom_entities: Dict[str, KnowledgeEntity] = {}
        self._load_custom()

    def _build_system_kb(self, workspace: str) -> Dict[str, KnowledgeEntity]:
        """构建系统内置知识库"""
        kb = {}
        entities = [
            KnowledgeEntity("GalaxyOS", aliases=["GalaxyOS", "claw系统"], type="system", description="基于OpenClaw框架的AI助手系统"),
            KnowledgeEntity("OpenClaw", aliases=["openclaw", "Claw框架"], type="framework", description="开源AI助手框架"),
            KnowledgeEntity("DeepSeek", aliases=["deepseek", "DeepSeek V4"], type="model", description="AI模型提供商"),
            KnowledgeEntity("腾讯云记忆插件", aliases=["memory-tencentdb", "腾讯云插件", "tdai-memory"], type="plugin", description="腾讯云记忆存储插件"),
            KnowledgeEntity("Yaoyao Memory", aliases=["yaoyao", "yaoyao-memory", "yaoyao插件"], type="plugin", description="本地记忆管理插件"),
            KnowledgeEntity("DAG上下文管理器", aliases=["DAG", "dag_context", "场景图"], type="module", description="DAG上下文管理模块"),
            KnowledgeEntity("R-CCAM", aliases=["rccam", "认知循环"], type="module", description="结构化认知循环：五阶段处理"),
            KnowledgeEntity("突触网络", aliases=["synapse", "synapse_network", "记忆突触"], type="module", description="神经网络模拟的记忆联结系统"),
            KnowledgeEntity("BGE-M3", aliases=["bge-m3", "bge_m3"], type="model", description="嵌入向量模型（1024维）"),
            KnowledgeEntity("BGE-Reranker", aliases=["bge-reranker", "bge-reranker-v2-m3"], type="model", description="重排序模型"),
            KnowledgeEntity("华为云盘", aliases=["huawei-drive", "华为云"], type="service", description="云存储备份服务"),
            KnowledgeEntity("IMA知识库", aliases=["ima", "IMA"], type="service", description="腾讯知识库平台"),
            KnowledgeEntity("无问芯穹", aliases=["cloud.infini-ai.com", "infini-ai"], type="service", description="AI模型API服务商"),
            KnowledgeEntity("HNSWLib", aliases=["hnswlib", "hnsw"], type="library", description="高效近邻搜索C++库"),
            KnowledgeEntity("艾宾浩斯遗忘曲线", aliases=["ebbinghaus", "遗忘曲线"], type="theory", description="记忆遗忘的指数衰减模型"),
            KnowledgeEntity("Merge Gate", aliases=["merge-gate", "合入门禁"], type="module", description="代码合并质量门禁系统"),
            KnowledgeEntity("Rails护栏", aliases=["rails", "护栏系统"], type="module", description="系统权限与安全护栏"),
            KnowledgeEntity("Matt Pocock", aliases=["matt pocock", "matt-pocock"], type="skill", description="工程技能集（10个）"),
            KnowledgeEntity("教员思想", aliases=["qiushi", "求是", "方法论"], type="skill", description="11个方法论思维技能"),
        ]
        for e in entities:
            kb[e.name] = e
            for alias in e.aliases:
                kb[alias] = e
        return kb

    def _load_custom(self):
        """加载自定义实体"""
        try:
            with open(self.custom_path, "r") as f:
                for line in f:
                    if line.strip():
                        data = json.loads(line)
                        e = KnowledgeEntity(**data)
                        self._custom_entities[e.name] = e
                        for alias in e.aliases:
                            self._custom_entities[alias] = e
        except Exception:
            pass

    def add_entity(self, entity: KnowledgeEntity):
        """添加自定义实体"""
        self._custom_entities[entity.name] = entity
        for alias in entity.aliases:
            self._custom_entities[alias] = entity
        try:
            with open(self.custom_path, "a") as f:
                f.write(json.dumps({
                    "name": entity.name,
                    "aliases": entity.aliases,
                    "type": entity.type,
                    "description": entity.description,
                    "source": entity.source,
                    "keywords": entity.keywords,
                }, ensure_ascii=False) + "\n")
        except Exception:
            pass

    def link(self, text: str) -> List[Tuple[str, KnowledgeEntity, float]]:
        """
        将文本中的实体链接到知识库

        Args:
            text: 输入文本

        Returns:
            [(匹配文本, 实体, 置信度), ...]
        """
        results = []
        matched = set()

        # 精确匹配（优先长匹配）
        candidates = sorted(
            list(self._builtin_entities.keys()) + list(self._custom_entities.keys()),
            key=len, reverse=True
        )

        for name in candidates:
            if name in matched:
                continue
            if name.lower() in text.lower():
                entity = self._builtin_entities.get(name) or self._custom_entities.get(name)
                if entity:
                    # 避免短名被长名覆盖
                    if any(m in name for m in matched):
                        continue
                    results.append((name, entity, 0.9))
                    matched.add(name)

        return results


# ==================== 3. 指代消解 ====================

class CoreferenceResolver:
    """
    指代消解器

    基于就近原则 + 语法角色的代词解析。
    支持：人称代词（它/他/她/它们）、指示代词（这/那/这个/那个）
    """

    # 代词映射
    PRONOUNS = {
        # 人称代词 → 可能的实体类型倾向
        "它": "thing",
        "他": "person",
        "她": "person",
        "它们": "thing",
        "他们": "person",
        "她们": "person",
        "其": "thing",
        # 指示代词
        "这": "thing",
        "那": "thing",
        "这个": "thing",
        "那个": "thing",
        "这些": "thing",
        "那些": "thing",
        # 其他
        "对方": "person",
        "前者": "thing",
        "后者": "thing",
    }

    def __init__(self):
        self._history: List[Dict] = []  # 对话历史实体
        self._max_history = 20

    def update_context(self, entities: List[str]):
        """更新上下文实体列表"""
        for e in reversed(entities):
            if e not in [h["name"] for h in self._history]:
                self._history.insert(0, {
                    "name": e,
                    "time": len(self._history)
                })
        # 裁剪
        self._history = self._history[:self._max_history]

    def resolve(self, text: str, context_text: str = "") -> Dict[str, str]:
        """
        解析文本中的代词

        Args:
            text: 当前文本
            context_text: 上下文文本（用于提取候选实体）

        Returns:
            {代词: 指代对象}
        """
        if not JIEBA_AVAILABLE:
            return {}

        # 从上下文提取候选实体
        if context_text and not self._history:
            words = jieba.lcut(context_text)
            # 名词和专有名词作为候选
            candidates = [w for w in words if len(w) > 1]
            self.update_context(candidates)

        # 查找代词
        resolutions = {}
        words = list(pseg.cut(text))

        for word, flag in words:
            if word in self.PRONOUNS and flag.startswith("r"):
                # 找最近的匹配实体
                if self._history:
                    # 根据类型倾向选最近的
                    preferred_type = self.PRONOUNS[word]
                    for h in self._history:
                        if h["name"] not in text:  # 避免自指
                            resolutions[word] = h["name"]
                            break

        return resolutions


# ==================== 4. 对比句检测 ====================

@dataclass
class ComparisonResult:
    """对比检测结果"""
    has_comparison: bool
    subject_a: Optional[str] = None        # 比较主体A
    subject_b: Optional[str] = None        # 比较主体B
    dimension: Optional[str] = None        # 比较维度（如"性能"、"质量"）
    relation: str = ""                     # 关系类型：> / < / =
    original_sentence: str = ""
    confidence: float = 0.0


class ComparisonDetector:
    """
    对比句检测器

    检测比较关系并抽取：
    - 显式比较：A比B更X, A不如B, A和B一样
    - 隐式比较：A更X（省略B）, A最好（最高级）
    """

    COMPARISON_MARKERS = [
        # (marker_word, relation, require_second_word)
        ("比", ">", False),     # A比B更X / A比BX得多
        ("不如", "<", False),
        ("比不上", "<", False),
        ("没有", "<", True),    # A没有B那么X
        ("和", "=", True),     # A和B一样X
        ("跟", "≈", True),    # A跟B差不多
        ("超过", ">", False),
        ("优于", ">", False),
        ("劣于", "<", False),
    ]

    # 常见比较维度关键词
    DIMENSION_WORDS = [
        "好", "坏", "快", "慢", "大", "小", "多", "少",
        "高", "低", "强", "弱", "优", "劣", "新", "旧",
        "方便", "好用", "稳定", "安全", "便宜", "贵",
        "效率", "性能", "质量", "速度", "成本", "效果",
        "简单", "复杂", "重要", "关键", "核心", "多",
    ]

    def detect(self, text: str) -> Optional[ComparisonResult]:
        """检测对比句"""
        if not text or len(text) < 5 or not JIEBA_AVAILABLE:
            return None

        for marker, relation, require_second in self.COMPARISON_MARKERS:
            pos = text.find(marker)
            if pos < 0:
                continue

            before = text[:pos].strip()
            after = text[pos + len(marker):].strip()
            if not before or not after:
                continue

            if require_second:
                # A和B一样X / A没有B那么X → 找第二个标记词
                second_markers = {"和": "一样", "没有": "那么"}.get(marker)
                if marker == "跟":
                    second_markers = "差不多"
                snd_pos = after.find(second_markers) if second_markers else -1
                if snd_pos < 0:
                    continue
                mid_part = after[:snd_pos].strip()
                last_part = after[snd_pos + len(second_markers):].strip()
            else:
                mid_part = after
                last_part = ""

            if not mid_part:
                continue

            # 抽实体：从左(before)取最后一个名词+英文，从右(mid_part)取第一个名词+英文
            def _extract_entity(seg: str, from_left: bool) -> str:
                """从分词中组装配对的相邻名词+英文"""
                seg_words = list(pseg.cut(seg))
                # 找名词/英文/专名的连续组合
                parts = []
                for w, f in seg_words:
                    if f.startswith(("n", "ns", "nr", "nz", "j", "vn", "eng")):
                        parts.append(w)
                if not parts:
                    return seg[:12]
                if from_left:
                    return "".join(parts[-3:])  # 取最后3个连续词
                return "".join(parts[:3])       # 取前3个连续词

            left_entity = _extract_entity(before, True)
            right_entity = _extract_entity(mid_part, False)

            if not left_entity or not right_entity or left_entity == right_entity:
                continue

            # 维度词：从最后一个比较段找
            dim = ""
            if last_part:
                for dw in self.DIMENSION_WORDS:
                    if dw in last_part or last_part.endswith(dw):
                        dim = dw
                        break
            elif "比" in before + marker + after:
                for dw in self.DIMENSION_WORDS + ["多"]:
                    if ("得" + dw) in after or dw in after:
                        dim = dw
                        break

            return ComparisonResult(
                has_comparison=True,
                subject_a=left_entity, subject_b=right_entity,
                dimension=dim, relation=relation,
                original_sentence=text, confidence=0.8
            )

        # 最高级：A 最 X
        max_match = re.search(r"(.+?)\s*最\s*([\u4e00-\u9fff]{1,4})", text)
        if max_match:
            a = max_match.group(1).strip()
            d = max_match.group(2).strip()
            return ComparisonResult(
                has_comparison=True, subject_a=a,
                dimension=d, relation="max",
                original_sentence=text, confidence=0.75
            )

        return None

    def extract_comparison_graph(self, texts: List[str]) -> List[ComparisonResult]:
        """
        从多条文本中抽取比较关系图谱

        Args:
            texts: 文本列表

        Returns:
            比较关系列表
        """
        results = []
        for text in texts:
            result = self.detect(text)
            if result:
                results.append(result)
        return results


# ==================== 集成入口 ====================

class EnhancedNLP:
    """
    增强 NLP 集成入口

    整合依存分析、实体链接、指代消解、对比检测四项能力。
    """

    def __init__(self, workspace_path: str = None):
        self.dep_parser = LightweightDependencyParser()
        self.entity_linker = EntityLinker(workspace_path)
        self.coref_resolver = CoreferenceResolver()
        self.comparison_detector = ComparisonDetector()

    def analyze(self, text: str, context: str = "") -> Dict[str, Any]:
        """
        对文本做全量增强分析

        Args:
            text: 输入文本
            context: 上下文文本（用于指代消解）

        Returns:
            分析结果字典
        """
        result = {"text": text[:200]}

        # 1. 依存句法
        try:
            dep = self.dep_parser.parse(text)
            triple = self.dep_parser.extract_triple(text)
            result["dependencies"] = {
                "relations": [(r.relation, r.head_word, r.dep_word) for r in dep.relations],
                "root": dep.root_word,
                "triple": triple,
            }
        except Exception:
            pass

        # 2. 实体链接
        try:
            links = self.entity_linker.link(text)
            linked_entities = []
            for match_text, entity, conf in links[:5]:
                linked_entities.append({
                    "mention": match_text,
                    "name": entity.name,
                    "type": entity.type,
                    "description": entity.description,
                    "confidence": conf,
                })
            result["entities"] = linked_entities
        except Exception:
            pass

        # 3. 指代消解
        try:
            resolutions = self.coref_resolver.resolve(text, context)
            if resolutions:
                result["coreferences"] = resolutions
        except Exception:
            pass

        # 4. 对比检测
        try:
            comparison = self.comparison_detector.detect(text)
            if comparison:
                result["comparison"] = {
                    "has_comparison": True,
                    "subject_a": comparison.subject_a,
                    "subject_b": comparison.subject_b,
                    "dimension": comparison.dimension,
                    "relation": comparison.relation,
                    "confidence": comparison.confidence,
                }
        except Exception:
            pass

        return result


# ==================== 便捷函数 ====================

_enhanced_nlp = None


def get_enhanced_nlp(workspace: str = None) -> EnhancedNLP:
    """获取全局增强 NLP 实例"""
    global _enhanced_nlp
    if _enhanced_nlp is None:
        _enhanced_nlp = EnhancedNLP(workspace)
    return _enhanced_nlp


def extract_triple(text: str) -> Optional[Tuple[str, str, str]]:
    """快捷提取主谓宾三元组"""
    return LightweightDependencyParser().extract_triple(text)


def link_entities(text: str, workspace: str = None) -> List[Tuple[str, str, float]]:
    """快捷实体链接"""
    linker = EntityLinker(workspace)
    return [(m, e.name, c) for m, e, c in linker.link(text)]


def resolve_coref(text: str, context: str = "") -> Dict[str, str]:
    """快捷指代消解"""
    return CoreferenceResolver().resolve(text, context)


def detect_comparison(text: str) -> Optional[ComparisonResult]:
    """快捷对比检测"""
    return ComparisonDetector().detect(text)


def analyze_text(text: str, context: str = "", workspace: str = None) -> Dict:
    """全量文本分析"""
    nlp = get_enhanced_nlp(workspace)
    return nlp.analyze(text, context)


if __name__ == "__main__":
    nlp = EnhancedNLP()

    print("=== 1. 依存句法 ===")
    dep = nlp.dep_parser.parse("GalaxyOS使用Python开发")
    print(f"  核心词: {dep.root_word}")
    for r in dep.relations:
        print(f"  {r.relation}: {r.head_word}({r.head_pos}) ← {r.dep_word}({r.dep_pos})")
    triple = nlp.dep_parser.extract_triple("GalaxyOS系统使用Python")
    print(f"  三元组: {triple}")

    print("\n=== 2. 实体链接 ===")
    links = nlp.entity_linker.link("对比一下GalaxyOS和无问芯穹哪个更好")
    for m, e, c in links:
        print(f"  {m} → {e.name} ({e.type}) [{c}]")

    print("\n=== 3. 指代消解 ===")
    nlp.coref_resolver.update_context(["GalaxyOS", "DAG", "突触网络"])
    res = nlp.coref_resolver.resolve("它好用吗", "GalaxyOS的DAG上下文管理器")
    for k, v in res.items():
        print(f"  {k} → {v}")

    print("\n=== 4. 对比检测 ===")
    tests = [
        "GalaxyOS比腾讯云插件更方便",
        "突触网络不如DAG稳定",
        "今天天气很好",
    ]
    for t in tests:
        r = nlp.comparison_detector.detect(t)
        if r:
            print(f"  ✅ {r.relation}: {r.subject_a} vs {r.subject_b} ({r.dimension})")
        else:
            print(f"  ❌ 非对比句: {t[:30]}")

    print("\n=== 5. 全量分析 ===")
    result = nlp.analyze("DAG上下文管理器比腾讯云插件稳定得多", "GalaxyOS架构")
    print(json.dumps(result, indent=2, ensure_ascii=False))
