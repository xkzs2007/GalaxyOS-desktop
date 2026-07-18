"""
DSLBridge — TokUI DSL ↔ EUI-NEO DSL 双向转换引擎

将 GalaxyOS 的 TokUI DSL 转换为 EUI-NEO 原生渲染可理解的 DSL，
支持声明式映射表、动态组件注册、转换置信度评估和超时检测。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

DEFAULT_MAPPINGS = {
    "card": {"eui_type": "card", "attrs": {"padding": "8", "radius": "4"}, "confidence": 1.0},
    "p": {"eui_type": "text", "attrs": {}, "confidence": 1.0},
    "btn": {"eui_type": "button", "attrs": {}, "confidence": 1.0},
    "table": {"eui_type": "dataTable", "attrs": {}, "confidence": 0.9},
    "chart": {"eui_type": "lineChart", "attrs": {}, "confidence": 0.8},
    "img": {"eui_type": "image", "attrs": {}, "confidence": 1.0},
    "input": {"eui_type": "input", "attrs": {}, "confidence": 1.0},
    "list": {"eui_type": "scrollView", "attrs": {}, "confidence": 0.8},
    "vlist": {"eui_type": "virtualList", "attrs": {}, "confidence": 0.9},
    "menu": {"eui_type": "contextMenu", "attrs": {}, "confidence": 0.9},
    "tabs": {"eui_type": "tabs", "attrs": {}, "confidence": 1.0},
    "dialog": {"eui_type": "dialog", "attrs": {}, "confidence": 1.0},
    "progress": {"eui_type": "progress", "attrs": {}, "confidence": 1.0},
    "markdown": {"eui_type": "markdown", "attrs": {}, "confidence": 1.0},
    "sidebar": {"eui_type": "sidebar", "attrs": {}, "confidence": 1.0},
    "slider": {"eui_type": "slider", "attrs": {}, "confidence": 1.0},
    "switch": {"eui_type": "switch", "attrs": {}, "confidence": 1.0},
    "checkbox": {"eui_type": "checkbox", "attrs": {}, "confidence": 1.0},
    "radio": {"eui_type": "radio", "attrs": {}, "confidence": 1.0},
    "dropdown": {"eui_type": "dropdown", "attrs": {}, "confidence": 1.0},
    "tooltip": {"eui_type": "tooltip", "attrs": {}, "confidence": 1.0},
    "toast": {"eui_type": "toast", "attrs": {}, "confidence": 1.0},
    "carousel": {"eui_type": "carousel", "attrs": {}, "confidence": 1.0},
    "stepper": {"eui_type": "stepper", "attrs": {}, "confidence": 1.0},
    "segmented": {"eui_type": "segmented", "attrs": {}, "confidence": 1.0},
    "date-picker": {"eui_type": "datePicker", "attrs": {}, "confidence": 1.0},
    "time-picker": {"eui_type": "timePicker", "attrs": {}, "confidence": 1.0},
    "color-picker": {"eui_type": "colorPicker", "attrs": {}, "confidence": 1.0},
    "bar-chart": {"eui_type": "barChart", "attrs": {}, "confidence": 1.0},
    "pie-chart": {"eui_type": "pieChart", "attrs": {}, "confidence": 1.0},
    "memory-panel": {"eui_type": "panel", "attrs": {"panel_type": "memory"}, "confidence": 1.0, "builder": "ProgressBuilder + InputBuilder + SegmentedBuilder"},
    "rccam-progress": {"eui_type": "panel", "attrs": {"panel_type": "rccam"}, "confidence": 1.0, "builder": "ProgressBuilder + ButtonBuilder + SliderBuilder + DropdownBuilder"},
    "dag-tree": {"eui_type": "panel", "attrs": {"panel_type": "dag_tree"}, "confidence": 1.0, "builder": "TextBuilder + ButtonBuilder (recursive)"},
    "memory-search": {"eui_type": "panel", "attrs": {"panel_type": "search"}, "confidence": 1.0, "builder": "InputBuilder + SegmentedBuilder + ScrollViewBuilder"},
    "rccam-control": {"eui_type": "panel", "attrs": {"panel_type": "rccam_control"}, "confidence": 1.0, "builder": "ButtonBuilder + SliderBuilder + DropdownBuilder"},
    "dag-node-expand": {"eui_type": "panel", "attrs": {"panel_type": "dag_expand"}, "confidence": 1.0, "builder": "TextBuilder + ButtonBuilder"},
    "cognitive-panel": {"eui_type": "panel", "attrs": {"panel_type": "cognitive"}, "confidence": 1.0, "builder": "SidebarBuilder + TabsBuilder"},
    "chat-renderer": {"eui_type": "panel", "attrs": {"panel_type": "chat"}, "confidence": 1.0, "builder": "ScrollViewBuilder + TextBuilder"},
    "message-renderer": {"eui_type": "panel", "attrs": {"panel_type": "message"}, "confidence": 1.0, "builder": "TextBuilder + MarkdownBuilder"},
}

CONVERSION_TIMEOUT_MS = 5.0


@dataclass
class DSLBridgeResult:
    output_dsl: str
    mapping_confidence: float
    unsupported_components: List[str]
    dropped_attrs: List[str]
    conversion_time_ms: float


class DSLBridge:
    def __init__(self, config_path: str = ""):
        self._mappings: Dict[str, Dict[str, Any]] = dict(DEFAULT_MAPPINGS)
        self._config_path = config_path or os.path.join(
            os.environ.get("GALAXYOS_HOME", os.path.join(os.path.dirname(__file__), "..", "..")),
            "config", "dsl_bridge_mappings.json"
        )
        self._load_mappings()

    def _load_mappings(self) -> None:
        if os.path.isfile(self._config_path):
            try:
                with open(self._config_path, "r", encoding="utf-8") as f:
                    custom = json.load(f)
                for k, v in custom.get("mappings", {}).items():
                    self._mappings[k] = v
                logger.info(f"Loaded {len(custom.get('mappings', {}))} custom DSL mappings from {self._config_path}")
            except Exception as e:
                logger.warning(f"Failed to load DSL mappings: {e}")

    def _save_mappings(self) -> None:
        try:
            os.makedirs(os.path.dirname(self._config_path), exist_ok=True)
            with open(self._config_path, "w", encoding="utf-8") as f:
                json.dump({"mappings": self._mappings}, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logger.warning(f"Failed to save DSL mappings: {e}")

    def tokui_to_eui(self, tokui_dsl: str) -> DSLBridgeResult:
        start = time.time()
        unsupported = []
        dropped = []
        total_confidence = 0.0
        mapped_count = 0

        tokens = self._tokenize_tokui(tokui_dsl)
        eui_components = []

        for token in tokens:
            comp_type = token.get("type", "")
            mapping = self._mappings.get(comp_type)

            if not mapping:
                unsupported.append(comp_type)
                eui_components.append(self._fallback_component(token))
                continue

            eui_type = mapping.get("eui_type", "Unknown")
            confidence = mapping.get("confidence", 0.5)
            base_attrs = dict(mapping.get("attrs", {}))

            merged_attrs = {**base_attrs, **token.get("attrs", {})}
            for attr_name in list(merged_attrs.keys()):
                if attr_name.startswith("_"):
                    dropped.append(f"{comp_type}.{attr_name}")
                    del merged_attrs[attr_name]

            eui_components.append({
                "type": eui_type,
                "attrs": merged_attrs,
                "children": token.get("children", []),
                "content": token.get("content", ""),
            })

            total_confidence += confidence
            mapped_count += 1

        avg_confidence = total_confidence / max(mapped_count, 1)
        elapsed_ms = (time.time() - start) * 1000

        if elapsed_ms > CONVERSION_TIMEOUT_MS:
            logger.warning(f"DSL conversion took {elapsed_ms:.1f}ms (timeout: {CONVERSION_TIMEOUT_MS}ms)")

        output = json.dumps({"components": eui_components}, ensure_ascii=False)

        return DSLBridgeResult(
            output_dsl=output,
            mapping_confidence=round(avg_confidence, 3),
            unsupported_components=unsupported,
            dropped_attrs=dropped,
            conversion_time_ms=round(elapsed_ms, 2),
        )

    def eui_to_tokui(self, eui_dsl: str) -> DSLBridgeResult:
        start = time.time()
        unsupported = []
        dropped = []
        total_confidence = 0.0
        mapped_count = 0

        reverse_map = {}
        for tokui_type, mapping in self._mappings.items():
            eui_type = mapping.get("eui_type", "")
            if eui_type:
                reverse_map[eui_type] = {"tokui_type": tokui_type, "confidence": mapping.get("confidence", 0.5)}

        try:
            eui_data = json.loads(eui_dsl) if isinstance(eui_dsl, str) else eui_dsl
        except json.JSONDecodeError:
            return DSLBridgeResult(
                output_dsl="", mapping_confidence=0, unsupported_components=["invalid_json"],
                dropped_attrs=[], conversion_time_ms=0,
            )

        tokui_components = []
        for comp in eui_data.get("components", []):
            eui_type = comp.get("type", "")
            rev = reverse_map.get(eui_type)

            if not rev:
                unsupported.append(eui_type)
                tokui_components.append(f"[{eui_type}]")
                continue

            tokui_type = rev["tokui_type"]
            confidence = rev["confidence"]
            attrs = comp.get("attrs", {})
            content = comp.get("content", "")

            attr_str = " ".join(f'{k}:{v}' for k, v in attrs.items() if not k.startswith("_"))
            tokui_components.append(f"[{tokui_type}{' ' + attr_str if attr_str else ''}]{content}[/{tokui_type}]")

            total_confidence += confidence
            mapped_count += 1

        avg_confidence = total_confidence / max(mapped_count, 1)
        elapsed_ms = (time.time() - start) * 1000

        return DSLBridgeResult(
            output_dsl="".join(tokui_components),
            mapping_confidence=round(avg_confidence, 3),
            unsupported_components=unsupported,
            dropped_attrs=dropped,
            conversion_time_ms=round(elapsed_ms, 2),
        )

    def register_component_mapping(
        self, tokui_type: str, eui_type: str, attr_mapping: Optional[Dict[str, Any]] = None, confidence: float = 0.5
    ) -> None:
        self._mappings[tokui_type] = {
            "eui_type": eui_type,
            "attrs": attr_mapping or {},
            "confidence": confidence,
        }
        self._save_mappings()
        logger.info(f"Registered DSL mapping: {tokui_type} → {eui_type} (confidence={confidence})")

    def get_unsupported_components(self, dsl: str) -> List[str]:
        tokens = self._tokenize_tokui(dsl)
        return [t.get("type", "") for t in tokens if t.get("type", "") not in self._mappings]

    def get_mapping_stats(self) -> Dict[str, Any]:
        cognitive = [k for k in self._mappings if k in DEFAULT_MAPPINGS and DEFAULT_MAPPINGS[k].get("confidence", 0) >= 0.6]
        basic = [k for k in self._mappings if k in DEFAULT_MAPPINGS and k not in cognitive]
        custom = [k for k in self._mappings if k not in DEFAULT_MAPPINGS]
        return {
            "total_mappings": len(self._mappings),
            "cognitive_components": len(cognitive),
            "basic_components": len(basic),
            "custom_mappings": len(custom),
            "cognitive_list": cognitive,
            "basic_list": basic,
            "custom_list": custom,
        }

    def _tokenize_tokui(self, dsl: str) -> List[Dict[str, Any]]:
        tokens = []
        pattern = r'\[(\S+?)(?:\s+([^\]]*))?\](.*?)\[/\1\]'
        for match in re.finditer(pattern, dsl, re.DOTALL):
            comp_type = match.group(1)
            attr_str = match.group(2) or ""
            content = match.group(3).strip()

            attrs = {}
            for attr_pair in re.findall(r'(\w+):([^\s]+)', attr_str):
                attrs[attr_pair[0]] = attr_pair[1]

            tokens.append({"type": comp_type, "attrs": attrs, "content": content, "children": []})

        if not tokens:
            tokens.append({"type": "p", "attrs": {}, "content": dsl.strip(), "children": []})

        return tokens

    def _fallback_component(self, token: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "type": "Text",
            "attrs": {"fallback": True, "original_type": token.get("type", "unknown")},
            "content": token.get("content", ""),
            "children": [],
        }
