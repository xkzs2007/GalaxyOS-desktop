"""
PyTokUIBuilder — Python 原生 TokUI DSL 生成器

链式调用 API 生成 TokUI DSL 字符串，作为 GalaxyOS 统一回复格式化器注入 Agent 回复管线。

核心能力：
  - 30+ 内置组件方法（card, table, chart, form, markdown, code 等）
  - 6 个 GalaxyOS 自定义组件（memory-panel, rccam-progress, dag-tree 等）
  - 链式调用：builder.card(tt="标题").p("内容").end().build()
  - 嵌套容器：[card tt:标题][p 内容][/card]
  - 事件安全：命名引用格式 clk:handlerName / sub:handlerName
  - 容错降级：build() 超时降级为纯文本
"""

from __future__ import annotations

import re
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence, Union


SELF_CLOSING_TYPES = frozenset({
    "h1", "h2", "h3", "h4", "h5", "h6",
    "p", "a", "img", "badge", "btn", "alert", "divider",
    "stat", "progress", "code", "md",
    "desc", "tr", "upd",
    "input", "select", "radio", "switch", "date", "picker",
    "tag", "chip", "icon", "avatar", "tooltip", "copy",
    "quick-reply", "agent",
    "memory-panel", "rccam-progress", "memory-search",
    "rccam-control", "dag-node-expand",
})

CONTAINER_TYPES = frozenset({
    "card", "ft", "row", "col", "list", "table", "thead", "tbody",
    "form", "tabs", "tab", "accordion", "collapse", "dialog",
    "btngroup", "timeline", "steps", "drawer", "ol", "ul", "i",
    "item", "think", "bubble", "toolbar", "badge-box",
    "dropdown", "transfer", "cascader", "tree", "tn", "step",
    "carousel", "popover", "input-tag", "watermark", "menu",
    "imgs", "textarea", "dag-tree",
})

BOOLEAN_ATTRS = frozenset({
    "stripe", "dis", "ro", "req", "chk", "multi", "auto",
    "plain", "round", "closable", "bordered", "open", "pill",
    "dot", "leaf", "inline", "rounded", "container",
})

ATTR_SHORTHANDS = {
    "tt": "title",
    "tx": "text",
    "l": "label",
    "ph": "placeholder",
    "u": "url",
    "s": "src",
    "n": "name",
    "v": "value",
    "act": "action",
    "mtd": "method",
    "clk": "onclick",
    "sub": "onsubmit",
    "dis": "disabled",
    "ro": "readonly",
    "req": "required",
    "chk": "checked",
    "id": "id",
    "w": "width",
    "h": "height",
    "bg": "background",
    "fc": "fontColor",
}


@dataclass
class ASTNode:
    type: str
    attrs: Dict[str, str] = field(default_factory=dict)
    children: List[Union["ASTNode", str]] = field(default_factory=list)
    is_container: bool = False
    self_closing: bool = False


class PyTokUIBuilder:
    BUILD_TIMEOUT_S = 5.0
    MAX_DSL_SIZE = 100_000

    def __init__(self, timeout_s: float = 0, max_dsl_size: int = 0):
        self._nodes: List[ASTNode] = []
        self._stack: List[ASTNode] = []
        self._timeout_s = timeout_s or self.BUILD_TIMEOUT_S
        self._max_dsl_size = max_dsl_size or self.MAX_DSL_SIZE
        self._stream_id: str = ""

    def reset(self) -> "PyTokUIBuilder":
        self._nodes.clear()
        self._stack.clear()
        self._stream_id = ""
        return self

    @property
    def stream_id(self) -> str:
        if not self._stream_id:
            self._stream_id = str(uuid.uuid4())
        return self._stream_id

    def _current_parent(self) -> Optional[ASTNode]:
        return self._stack[-1] if self._stack else None

    def _add_node(self, node: ASTNode) -> "PyTokUIBuilder":
        parent = self._current_parent()
        if parent:
            parent.children.append(node)
        else:
            self._nodes.append(node)
        return self

    def _open(self, type_: str, **attrs) -> "PyTokUIBuilder":
        node = ASTNode(
            type=type_,
            attrs=self._serialize_attrs(attrs),
            is_container=True,
            self_closing=False,
        )
        self._add_node(node)
        self._stack.append(node)
        return self

    def _self_closing(self, type_: str, **attrs) -> "PyTokUIBuilder":
        node = ASTNode(
            type=type_,
            attrs=self._serialize_attrs(attrs),
            is_container=False,
            self_closing=True,
        )
        self._add_node(node)
        return self

    def end(self, count: int = 1) -> "PyTokUIBuilder":
        for _ in range(min(count, len(self._stack))):
            if self._stack:
                self._stack.pop()
        return self

    def endAll(self) -> "PyTokUIBuilder":
        self._stack.clear()
        return self

    def text(self, content: str) -> "PyTokUIBuilder":
        parent = self._current_parent()
        if parent:
            parent.children.append(content)
        else:
            self._nodes.append(content)
        return self

    def build(self) -> str:
        start = time.monotonic()
        try:
            dsl = self._build_nodes(self._nodes)
            if len(dsl) > self._max_dsl_size:
                dsl = dsl[: self._max_dsl_size]
            return dsl
        except Exception:
            if time.monotonic() - start > self._timeout_s:
                return self._fallback_text()
            raise

    def toChunks(self, max_chunk_size: int = 32768) -> List[str]:
        dsl = self.build()
        if len(dsl) <= max_chunk_size:
            return [dsl]
        return self._split_dsl(dsl, max_chunk_size)

    def _fallback_text(self) -> str:
        texts = []
        for node in self._nodes:
            if isinstance(node, str):
                texts.append(node)
            elif isinstance(node, ASTNode):
                if node.attrs.get("tt"):
                    texts.append(node.attrs["tt"])
                for child in node.children:
                    if isinstance(child, str):
                        texts.append(child)
        return " ".join(texts) if texts else "[p 回复生成超时]"

    def _build_nodes(self, nodes: List[Union[ASTNode, str]]) -> str:
        parts = []
        for node in nodes:
            if isinstance(node, str):
                parts.append(self._escape_text(node))
            elif isinstance(node, ASTNode):
                parts.append(self._build_node(node))
        return "".join(parts)

    def _build_node(self, node: ASTNode) -> str:
        attr_str = self._format_attrs(node.attrs)
        tag_open = f"[{node.type}"
        if attr_str:
            tag_open += f" {attr_str}"

        if node.self_closing or not node.is_container:
            content = ""
            if node.children:
                content = self._build_nodes(node.children)
            if content:
                return f"{tag_open} {content}]"
            return f"{tag_open}]"

        inner = self._build_nodes(node.children)
        return f"{tag_open}{inner}][/{node.type}]"

    def _format_attrs(self, attrs: Dict[str, str]) -> str:
        if not attrs:
            return ""
        parts = []
        for key, value in attrs.items():
            if key in BOOLEAN_ATTRS and value in ("true", "1", ""):
                parts.append(key)
            elif " " in value or '"' in value or "]" in value:
                escaped = value.replace('"', '\\"')
                parts.append(f'{key}:"{escaped}"')
            else:
                parts.append(f"{key}:{value}")
        return " ".join(parts)

    def _serialize_attrs(self, attrs: Dict[str, Any]) -> Dict[str, str]:
        result = {}
        for key, value in attrs.items():
            if value is None or value is False:
                continue
            if value is True:
                result[key] = ""
            elif isinstance(value, (list, tuple)):
                result[key] = ",".join(str(v) for v in value)
            else:
                result[key] = str(value)
        return result

    def _escape_text(self, text: str) -> str:
        return text.replace("]", "\\]").replace("[", "\\[")

    def _split_dsl(self, dsl: str, max_size: int) -> List[str]:
        chunks = []
        pos = 0
        while pos < len(dsl):
            if pos + max_size >= len(dsl):
                chunks.append(dsl[pos:])
                break
            cut = pos + max_size
            boundary = dsl.rfind("][", pos, cut)
            if boundary > pos:
                cut = boundary + 1
            else:
                boundary = dsl.rfind("]", pos, cut)
                if boundary > pos:
                    cut = boundary + 1
            chunks.append(dsl[pos:cut])
            pos = cut
        return chunks

    # ── 基础布局组件 ──

    def card(self, tt: str = "", **attrs) -> "PyTokUIBuilder":
        if tt:
            attrs["tt"] = tt
        return self._open("card", **attrs)

    def ft(self, **attrs) -> "PyTokUIBuilder":
        return self._open("ft", **attrs)

    def row(self, **attrs) -> "PyTokUIBuilder":
        return self._open("row", **attrs)

    def col(self, **attrs) -> "PyTokUIBuilder":
        return self._open("col", **attrs)

    def tabs(self, **attrs) -> "PyTokUIBuilder":
        return self._open("tabs", **attrs)

    def tab(self, tt: str = "", **attrs) -> "PyTokUIBuilder":
        if tt:
            attrs["tt"] = tt
        return self._open("tab", **attrs)

    def accordion(self, **attrs) -> "PyTokUIBuilder":
        return self._open("accordion", **attrs)

    def collapse(self, tt: str = "", **attrs) -> "PyTokUIBuilder":
        if tt:
            attrs["tt"] = tt
        return self._open("collapse", **attrs)

    def dialog(self, tt: str = "", **attrs) -> "PyTokUIBuilder":
        if tt:
            attrs["tt"] = tt
        return self._open("dialog", **attrs)

    def divider(self, **attrs) -> "PyTokUIBuilder":
        return self._self_closing("divider", **attrs)

    def list_container(self, ordered: bool = False, **attrs) -> "PyTokUIBuilder":
        return self._open("ol" if ordered else "ul", **attrs)

    def item(self, **attrs) -> "PyTokUIBuilder":
        return self._open("item", **attrs)

    def imgs(self, **attrs) -> "PyTokUIBuilder":
        return self._open("imgs", **attrs)

    def desc(self, **attrs) -> "PyTokUIBuilder":
        return self._self_closing("desc", **attrs)

    # ── 标题与文本组件 ──

    def h1(self, content: str = "", **attrs) -> "PyTokUIBuilder":
        if content:
            attrs["tx"] = content
        return self._self_closing("h1", **attrs)

    def h2(self, content: str = "", **attrs) -> "PyTokUIBuilder":
        if content:
            attrs["tx"] = content
        return self._self_closing("h2", **attrs)

    def h3(self, content: str = "", **attrs) -> "PyTokUIBuilder":
        if content:
            attrs["tx"] = content
        return self._self_closing("h3", **attrs)

    def h4(self, content: str = "", **attrs) -> "PyTokUIBuilder":
        if content:
            attrs["tx"] = content
        return self._self_closing("h4", **attrs)

    def h5(self, content: str = "", **attrs) -> "PyTokUIBuilder":
        if content:
            attrs["tx"] = content
        return self._self_closing("h5", **attrs)

    def h6(self, content: str = "", **attrs) -> "PyTokUIBuilder":
        if content:
            attrs["tx"] = content
        return self._self_closing("h6", **attrs)

    def p(self, content: str = "", **attrs) -> "PyTokUIBuilder":
        if content:
            attrs["tx"] = content
        return self._self_closing("p", **attrs)

    def a(self, content: str = "", u: str = "", **attrs) -> "PyTokUIBuilder":
        if content:
            attrs["tx"] = content
        if u:
            attrs["u"] = u
        return self._self_closing("a", **attrs)

    def badge(self, content: str = "", v: str = "", **attrs) -> "PyTokUIBuilder":
        if content:
            attrs["tx"] = content
        if v:
            attrs["v"] = v
        return self._self_closing("badge", **attrs)

    def btn(self, content: str = "", clk: str = "", v: str = "", **attrs) -> "PyTokUIBuilder":
        if content:
            attrs["tx"] = content
        if clk:
            attrs["clk"] = clk
        if v:
            attrs["v"] = v
        return self._self_closing("btn", **attrs)

    def alert(self, content: str = "", v: str = "info", **attrs) -> "PyTokUIBuilder":
        if content:
            attrs["tx"] = content
        attrs.setdefault("v", v)
        return self._self_closing("alert", **attrs)

    def stat(self, l: str = "", v: str = "", **attrs) -> "PyTokUIBuilder":
        if l:
            attrs["l"] = l
        if v:
            attrs["v"] = v
        return self._self_closing("stat", **attrs)

    def progress(self, v: str = "", l: str = "", **attrs) -> "PyTokUIBuilder":
        if v:
            attrs["v"] = v
        if l:
            attrs["l"] = l
        return self._self_closing("progress", **attrs)

    def tag(self, content: str = "", **attrs) -> "PyTokUIBuilder":
        if content:
            attrs["tx"] = content
        return self._self_closing("tag", **attrs)

    def chip(self, content: str = "", **attrs) -> "PyTokUIBuilder":
        if content:
            attrs["tx"] = content
        return self._self_closing("chip", **attrs)

    def icon(self, n: str = "", **attrs) -> "PyTokUIBuilder":
        if n:
            attrs["n"] = n
        return self._self_closing("icon", **attrs)

    def avatar(self, s: str = "", **attrs) -> "PyTokUIBuilder":
        if s:
            attrs["s"] = s
        return self._self_closing("avatar", **attrs)

    def tooltip(self, content: str = "", **attrs) -> "PyTokUIBuilder":
        if content:
            attrs["tx"] = content
        return self._self_closing("tooltip", **attrs)

    def copy(self, v: str = "", **attrs) -> "PyTokUIBuilder":
        if v:
            attrs["v"] = v
        return self._self_closing("copy", **attrs)

    def quick_reply(self, items: str = "", **attrs) -> "PyTokUIBuilder":
        if items:
            attrs["v"] = items
        return self._self_closing("quick-reply", **attrs)

    def agent(self, n: str = "", **attrs) -> "PyTokUIBuilder":
        if n:
            attrs["n"] = n
        return self._self_closing("agent", **attrs)

    # ── 内容组件 ──

    def markdown(self, content: str = "", **attrs) -> "PyTokUIBuilder":
        if content:
            attrs["tx"] = content
        return self._self_closing("md", **attrs)

    def code(self, content: str = "", language: str = "", **attrs) -> "PyTokUIBuilder":
        if content:
            attrs["tx"] = content
        if language:
            attrs["l"] = language
        return self._self_closing("code", **attrs)

    def image(self, s: str = "", alt: str = "", **attrs) -> "PyTokUIBuilder":
        if s:
            attrs["s"] = s
        if alt:
            attrs["l"] = alt
        return self._self_closing("img", **attrs)

    # ── 数据展示组件 ──

    def table(self, stripe: bool = False, **attrs) -> "PyTokUIBuilder":
        if stripe:
            attrs["stripe"] = True
        return self._open("table", **attrs)

    def thead(self, **attrs) -> "PyTokUIBuilder":
        return self._open("thead", **attrs)

    def tbody(self, **attrs) -> "PyTokUIBuilder":
        return self._open("tbody", **attrs)

    def tr(self, **attrs) -> "PyTokUIBuilder":
        return self._self_closing("tr", **attrs)

    def chart(self, chart_type: str = "bar", data: str = "", **attrs) -> "PyTokUIBuilder":
        attrs["v"] = chart_type
        if data:
            attrs["s"] = data
        return self._self_closing("chart", **attrs)

    def timeline(self, **attrs) -> "PyTokUIBuilder":
        return self._open("timeline", **attrs)

    def steps(self, **attrs) -> "PyTokUIBuilder":
        return self._open("steps", **attrs)

    def step(self, tt: str = "", **attrs) -> "PyTokUIBuilder":
        if tt:
            attrs["tt"] = tt
        return self._open("step", **attrs)

    # ── 表单组件 ──

    def form(self, act: str = "", mtd: str = "POST", sub: str = "", **attrs) -> "PyTokUIBuilder":
        if act:
            attrs["act"] = act
        if mtd:
            attrs["mtd"] = mtd
        if sub:
            attrs["sub"] = sub
        return self._open("form", **attrs)

    def input(self, l: str = "", ph: str = "", n: str = "", **attrs) -> "PyTokUIBuilder":
        if l:
            attrs["l"] = l
        if ph:
            attrs["ph"] = ph
        if n:
            attrs["n"] = n
        return self._self_closing("input", **attrs)

    def select(self, n: str = "", l: str = "", **attrs) -> "PyTokUIBuilder":
        if n:
            attrs["n"] = n
        if l:
            attrs["l"] = l
        return self._open("select", **attrs)

    def radio(self, n: str = "", l: str = "", **attrs) -> "PyTokUIBuilder":
        if n:
            attrs["n"] = n
        if l:
            attrs["l"] = l
        return self._self_closing("radio", **attrs)

    def switch(self, n: str = "", l: str = "", **attrs) -> "PyTokUIBuilder":
        if n:
            attrs["n"] = n
        if l:
            attrs["l"] = l
        return self._self_closing("switch", **attrs)

    def date(self, n: str = "", l: str = "", **attrs) -> "PyTokUIBuilder":
        if n:
            attrs["n"] = n
        if l:
            attrs["l"] = l
        return self._self_closing("date", **attrs)

    def picker(self, **attrs) -> "PyTokUIBuilder":
        return self._open("picker", **attrs)

    def textarea(self, n: str = "", ph: str = "", l: str = "", **attrs) -> "PyTokUIBuilder":
        if n:
            attrs["n"] = n
        if ph:
            attrs["ph"] = ph
        if l:
            attrs["l"] = l
        return self._open("textarea", **attrs)

    def input_tag(self, n: str = "", ph: str = "", **attrs) -> "PyTokUIBuilder":
        if n:
            attrs["n"] = n
        if ph:
            attrs["ph"] = ph
        return self._open("input-tag", **attrs)

    # ── 其他容器组件 ──

    def btngroup(self, **attrs) -> "PyTokUIBuilder":
        return self._open("btngroup", **attrs)

    def drawer(self, tt: str = "", **attrs) -> "PyTokUIBuilder":
        if tt:
            attrs["tt"] = tt
        return self._open("drawer", **attrs)

    def think(self, **attrs) -> "PyTokUIBuilder":
        return self._open("think", **attrs)

    def bubble(self, **attrs) -> "PyTokUIBuilder":
        return self._open("bubble", **attrs)

    def toolbar(self, **attrs) -> "PyTokUIBuilder":
        return self._open("toolbar", **attrs)

    def badge_box(self, **attrs) -> "PyTokUIBuilder":
        return self._open("badge-box", **attrs)

    def dropdown(self, **attrs) -> "PyTokUIBuilder":
        return self._open("dropdown", **attrs)

    def transfer(self, **attrs) -> "PyTokUIBuilder":
        return self._open("transfer", **attrs)

    def cascader(self, **attrs) -> "PyTokUIBuilder":
        return self._open("cascader", **attrs)

    def tree(self, **attrs) -> "PyTokUIBuilder":
        return self._open("tree", **attrs)

    def carousel(self, **attrs) -> "PyTokUIBuilder":
        return self._open("carousel", **attrs)

    def popover(self, tt: str = "", **attrs) -> "PyTokUIBuilder":
        if tt:
            attrs["tt"] = tt
        return self._open("popover", **attrs)

    def watermark(self, **attrs) -> "PyTokUIBuilder":
        return self._open("watermark", **attrs)

    def menu(self, **attrs) -> "PyTokUIBuilder":
        return self._open("menu", **attrs)

    # ── 布局别名（避让表格 row） ──

    def row_layout(self, **attrs) -> "PyTokUIBuilder":
        return self._open("row", **attrs)

    def col_layout(self, **attrs) -> "PyTokUIBuilder":
        return self._open("col", **attrs)

    # ── 6 个 GalaxyOS 自定义组件 ──

    def memory_panel(
        self,
        engram_count: int = 0,
        neural_count: int = 0,
        synapse_count: int = 0,
        consolidation_status: str = "idle",
        search_handler: str = "clk:memory-search",
        filter_handler: str = "sub:memory-filter",
        **attrs,
    ) -> "PyTokUIBuilder":
        attrs["engram_count"] = str(engram_count)
        attrs["neural_count"] = str(neural_count)
        attrs["synapse_count"] = str(synapse_count)
        attrs["consolidation"] = consolidation_status
        attrs["clk"] = search_handler
        attrs["sub"] = filter_handler
        return self._self_closing("memory-panel", **attrs)

    def rccam_progress(
        self,
        current_stage: str = "idle",
        stages_completed: int = 0,
        total_stages: int = 5,
        stage_details: str = "",
        pause_handler: str = "clk:rccam-pause",
        resume_handler: str = "clk:rccam-resume",
        depth_handler: str = "clk:rccam-depth",
        strategy_handler: str = "clk:rccam-strategy",
        **attrs,
    ) -> "PyTokUIBuilder":
        attrs["stage"] = current_stage
        attrs["completed"] = str(stages_completed)
        attrs["total"] = str(total_stages)
        if stage_details:
            attrs["details"] = stage_details
        attrs["clk:pause"] = pause_handler
        attrs["clk:resume"] = resume_handler
        attrs["clk:depth"] = depth_handler
        attrs["clk:strategy"] = strategy_handler
        return self._self_closing("rccam-progress", **attrs)

    def dag_tree(
        self,
        nodes: str = "",
        edges: str = "",
        active_node_id: str = "",
        summary_chain: str = "",
        expand_handler: str = "clk:dag-expand",
        collapse_handler: str = "clk:dag-collapse",
        summary_handler: str = "clk:dag-summary",
        **attrs,
    ) -> "PyTokUIBuilder":
        if nodes:
            attrs["nodes"] = nodes
        if edges:
            attrs["edges"] = edges
        if active_node_id:
            attrs["active"] = active_node_id
        if summary_chain:
            attrs["summary"] = summary_chain
        attrs["clk:expand"] = expand_handler
        attrs["clk:collapse"] = collapse_handler
        attrs["clk:summary"] = summary_handler
        return self._open("dag-tree", **attrs)

    def memory_search(
        self,
        query: str = "",
        results: str = "",
        search_handler: str = "clk:memory-search",
        filter_handler: str = "sub:memory-filter",
        **attrs,
    ) -> "PyTokUIBuilder":
        if query:
            attrs["ph"] = query
        if results:
            attrs["results"] = results
        attrs["clk"] = search_handler
        attrs["sub"] = filter_handler
        return self._self_closing("memory-search", **attrs)

    def rccam_control(
        self,
        is_running: bool = False,
        current_strategy: str = "direct_reply",
        retrieval_depth: int = 3,
        pause_handler: str = "clk:rccam-pause",
        resume_handler: str = "clk:rccam-resume",
        depth_handler: str = "clk:rccam-depth",
        strategy_handler: str = "clk:rccam-strategy",
        **attrs,
    ) -> "PyTokUIBuilder":
        attrs["running"] = str(is_running).lower()
        attrs["strategy"] = current_strategy
        attrs["depth"] = str(retrieval_depth)
        attrs["clk:pause"] = pause_handler
        attrs["clk:resume"] = resume_handler
        attrs["clk:depth"] = depth_handler
        attrs["clk:strategy"] = strategy_handler
        return self._self_closing("rccam-control", **attrs)

    def dag_node_expand(
        self,
        node_id: str = "",
        node_content: str = "",
        children: str = "",
        summary: str = "",
        expand_handler: str = "clk:dag-expand",
        collapse_handler: str = "clk:dag-collapse",
        summary_handler: str = "clk:dag-summary",
        **attrs,
    ) -> "PyTokUIBuilder":
        if node_id:
            attrs["id"] = node_id
        if node_content:
            attrs["tx"] = node_content
        if children:
            attrs["children"] = children
        if summary:
            attrs["summary"] = summary
        attrs["clk:expand"] = expand_handler
        attrs["clk:collapse"] = collapse_handler
        attrs["clk:summary"] = summary_handler
        return self._self_closing("dag-node-expand", **attrs)

    # ── 便捷方法 ──

    def card_with_content(self, tt: str, content: str, **attrs) -> "PyTokUIBuilder":
        return self.card(tt=tt, **attrs).p(content).end()

    def table_with_data(
        self,
        headers: Sequence[str],
        rows: Sequence[Sequence[str]],
        stripe: bool = True,
    ) -> "PyTokUIBuilder":
        self.table(stripe=stripe)
        self.thead()
        header_data = "|".join(headers)
        self.tr(v=header_data)
        self.end()
        self.tbody()
        for row in rows:
            row_data = "|".join(row)
            self.tr(v=row_data)
        self.end()
        self.end()
        return self

    def stat_group(self, stats: Sequence[Dict[str, str]]) -> "PyTokUIBuilder":
        self.row()
        for s in stats:
            self.col().stat(l=s.get("label", ""), v=s.get("value", "")).end()
        self.end()
        return self
