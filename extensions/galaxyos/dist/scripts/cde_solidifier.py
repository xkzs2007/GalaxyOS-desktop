#!/usr/bin/env python3
"""
cde_solidifier.py — Code solidification (CDE: Code Definition Engine)

CodeSolidifier:
  - scan(skill_text) → list of parameterized templates
  - solidify(template, params) → executable script string
  - detect_templates(): regex patterns for curl, API calls, shell commands with {param} placeholders
  - solidify_template(template, context) → instantiated script

CDE 核心思想：将技能文本中的可执行指令固化为模板，
运行时只需填入参数即可生成可执行的脚本。
"""

import re
import json
import shlex
import logging
from typing import Dict, List, Optional, Any, Tuple
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class SolidifiedTemplate:
    """
    固态化模板

    Attributes:
        description: 模板描述/用途
        language: 脚本语言 (python, bash, curl, node, text)
        template_text: 含参数占位符的原始模板
        param_names: 参数名列表
        required_params: 必需参数列表
        default_params: 默认参数字典
        validation_rules: 参数验证规则（可选的正则/类型）
    """
    description: str = ""
    language: str = "text"
    template_text: str = ""
    param_names: List[str] = field(default_factory=list)
    required_params: List[str] = field(default_factory=list)
    default_params: Dict[str, str] = field(default_factory=dict)
    validation_rules: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "description": self.description,
            "language": self.language,
            "template_text": self.template_text,
            "param_names": self.param_names,
            "required_params": self.required_params,
            "default_params": self.default_params,
            "validation_rules": self.validation_rules,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "SolidifiedTemplate":
        return cls(
            description=data.get("description", ""),
            language=data.get("language", "text"),
            template_text=data.get("template_text", ""),
            param_names=data.get("param_names", []),
            required_params=data.get("required_params", []),
            default_params=data.get("default_params", {}),
            validation_rules=data.get("validation_rules", {}),
        )


class CodeSolidifier:
    """
    Code Solidifier

    从技能文本中检测和提取参数化模板，
    并支持运行时填入参数生成可执行脚本。
    """

    # 模板检测模式
    TEMPLATE_PATTERNS = [
        # curl 命令
        {
            "name": "curl",
            "pattern": r'curl\s+.*?\{\w+\}.*?(?=\n|$)',
            "language": "bash",
        },
        # API 调用 (requests.get / requests.post)
        {
            "name": "python_requests",
            "pattern": r'requests\.(?:get|post|put|delete)\s*\(.*?\{?\w+\}?.*?\).*?(?=\n|$)',
            "language": "python",
        },
        # shell 命令含参数
        {
            "name": "shell_cmd",
            "pattern": r'(?:bash|sh|shell|exec)\s+.*?\{?\w+\}?.*?(?=\n|$)',
            "language": "bash",
        },
        # Node.js fetch
        {
            "name": "node_fetch",
            "pattern": r'fetch\s*\(.*?\{?\w+\}?.*?\).*?(?=\n|$)',
            "language": "node",
        },
        # 通用大括号参数 {param}
        {
            "name": "generic_brace",
            "pattern": r'.*?\{[a-zA-Z_][a-zA-Z0-9_]*\}.*?(?=\n|$)',
            "language": "text",
        },
    ]

    def __init__(self):
        self._cached_templates: List[SolidifiedTemplate] = []

    def scan(self, skill_text: str) -> List[SolidifiedTemplate]:
        """
        扫描技能文本，提取所有参数化模板

        Args:
            skill_text: 技能原始文本

        Returns:
            SolidifiedTemplate 列表
        """
        templates: List[SolidifiedTemplate] = []
        seen_templates: set = set()

        lines = skill_text.split("\n")
        current_section = ""
        in_code_block = False
        code_block_lang = ""

        for line in lines:
            stripped = line.strip()

            # 检测代码块
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                if in_code_block:
                    code_block_lang = stripped[3:].strip() or "text"
                else:
                    code_block_lang = ""
                continue

            # 检测 section 标题
            if stripped.startswith("## "):
                current_section = stripped[3:].strip()
                continue

            # 如果是代码块，对整个块做模板检测
            if in_code_block:
                templates_in_line = self._detect_templates_in_line(
                    stripped, code_block_lang, current_section, seen_templates
                )
                templates.extend(templates_in_line)
            else:
                # 普通行
                templates_in_line = self._detect_templates_in_line(
                    stripped, "text", current_section, seen_templates
                )
                templates.extend(templates_in_line)

        self._cached_templates = templates
        logger.info(f"CodeSolidifier: scanned {len(templates)} templates from {len(lines)} lines")
        return templates

    def detect_templates(self, skill_text: str) -> List[Dict[str, Any]]:
        """
        detect_templates 接口（与 skill_compiler 的调用一致）
        
        Args:
            skill_text: 技能文本
            
        Returns:
            template dict 列表
        """
        templates = self.scan(skill_text)
        return [t.to_dict() for t in templates]

    def solidify(self, template: Dict[str, Any], params: Dict[str, str]) -> str:
        """
        将模板实例化（填入参数）

        Args:
            template: 模板数据（SolidifiedTemplate.to_dict 或 dict）
            params: 参数键值对

        Returns:
            实例化的可执行脚本字符串

        Raises:
            ValueError: 缺少必需参数
        """
        template_obj = template if isinstance(template, SolidifiedTemplate) else \
            SolidifiedTemplate.from_dict(template)

        # 1. 参数验证
        missing_required = [
            p for p in template_obj.required_params
            if p not in params or not params[p]
        ]
        if missing_required:
            raise ValueError(f"Missing required params: {missing_required}")

        # 2. 合并默认参数
        merged_params = dict(template_obj.default_params)
        merged_params.update(params)

        # 3. 验证规则检查
        for param_name, rule in template_obj.validation_rules.items():
            if param_name in merged_params:
                value = merged_params[param_name]
                if rule == "numeric" and not value.isdigit():
                    raise ValueError(f"Param '{param_name}' must be numeric, got '{value}'")
                if rule == "url" and not value.startswith(("http://", "https://")):
                    raise ValueError(f"Param '{param_name}' must be a URL, got '{value}'")
                if rule.startswith("regex:"):
                    pattern = rule[6:]
                    if not re.match(pattern, value):
                        raise ValueError(f"Param '{param_name}' doesn't match regex '{pattern}', got '{value}'")

        # 4. 参数替换
        script = template_obj.template_text
        for param_name, param_value in merged_params.items():
            placeholder = "{" + param_name + "}"
            if placeholder in script:
                # 根据语言类型选择转义方式
                if template_obj.language in ("bash", "shell"):
                    script = script.replace(placeholder, shlex.quote(str(param_value)))
                else:
                    script = script.replace(placeholder, str(param_value))
            else:
                # 尝试双大括号
                placeholder2 = "{{" + param_name + "}}"
                script = script.replace(placeholder2, str(param_value))

        return script

    def solidify_batch(
        self,
        templates: List[Dict[str, Any]],
        params_list: List[Dict[str, str]],
    ) -> List[str]:
        """批量实例化模板"""
        if len(templates) != len(params_list):
            raise ValueError(
                f"templates ({len(templates)}) and params_list ({len(params_list)}) "
                f"must have same length"
            )
        return [
            self.solidify(t, p)
            for t, p in zip(templates, params_list)
        ]

    def _detect_templates_in_line(
        self,
        line: str,
        language: str,
        section: str,
        seen: set,
    ) -> List[SolidifiedTemplate]:
        """
        检测单行中的模板

        Args:
            line: 当前行文本
            language: 当前语言（代码块语言或 text）
            section: 当前 section 标题
            seen: 已检测的模板文本集合（去重）

        Returns:
            新检测的模板列表
        """
        templates = []

        if not line or len(line) < 10:
            return templates

        # 跳过注释行和纯文字描述
        stripped = line.strip()
        if stripped.startswith("#") or stripped.startswith("//") or stripped.startswith("<!--"):
            return templates

        for pattern_def in self.TEMPLATE_PATTERNS:
            name = pattern_def["name"]
            pattern = pattern_def["pattern"]
            pattern_lang = pattern_def["language"]

            # 语言匹配
            if language != "text" and language != pattern_lang:
                continue

            matches = re.findall(pattern, line, re.IGNORECASE)
            for match in matches:
                match_stripped = match.strip()
                if match_stripped in seen or len(match_stripped) < 10:
                    continue
                seen.add(match_stripped)

                # 提取参数名
                param_names = re.findall(r'\{([a-zA-Z_][a-zA-Z0-9_]*)\}', match_stripped)
                if not param_names:
                    continue

                template = SolidifiedTemplate(
                    description=f"{name} in '{section}'" if section else name,
                    language=pattern_lang if pattern_lang != "text" else language,
                    template_text=match_stripped,
                    param_names=param_names,
                    required_params=param_names,  # 默认所有参数必需
                )
                templates.append(template)

        return templates

    def get_template_stats(self) -> Dict[str, Any]:
        """获取模板统计信息"""
        if not self._cached_templates:
            return {"total": 0}
        return {
            "total": len(self._cached_templates),
            "by_language": {
                lang: len([t for t in self._cached_templates if t.language == lang])
                for lang in set(t.language for t in self._cached_templates)
            },
            "param_count_histogram": {
                str(n): len([t for t in self._cached_templates if len(t.param_names) == n])
                for n in set(len(t.param_names) for t in self._cached_templates)
            },
        }


# ── 便捷函数 ──

def solidify_from_skill(
    skill_text: str,
    params: Dict[str, str],
) -> List[str]:
    """
    从技能文本直接生成可执行脚本（scans → solidifies）

    Args:
        skill_text: 技能文本
        params: 参数键值对

    Returns:
        实例化后的脚本列表
    """
    solidifier = CodeSolidifier()
    templates = solidifier.scan(skill_text)
    scripts = []
    for t in templates:
        try:
            script = solidifier.solidify(t.to_dict(), params)
            scripts.append(script)
        except Exception as e:
            logger.warning(f"Solidify failed for '{t.description}': {e}")
    return scripts


# ── 测试入口 ──

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    test_skill = """
## Step 1: Search
```bash
curl -X GET "https://api.example.com/search?q={query}&limit={limit}"
```

## Step 2: Fetch Details
```python
import requests
response = requests.get("https://api.example.com/item/{item_id}")
data = response.json()
```

## Step 3: Process
```python
def process_data(items):
    return sorted(items, key=lambda x: x["score"], reverse=True)[:10]
```
    """

    solidifier = CodeSolidifier()

    # Scan
    templates = solidifier.scan(test_skill)
    print(f"Detected {len(templates)} templates:")
    for t in templates:
        print(f"  [{t.language}] {t.description}")
        print(f"    params: {t.param_names}")
        print(f"    template: {t.template_text[:80]}...")

    # Solidify
    scripts = solidify_from_skill(test_skill, {
        "query": "hello world",
        "limit": "10",
        "item_id": "12345",
    })
    print(f"\nSolidified scripts ({len(scripts)}):")
    for i, s in enumerate(scripts):
        print(f"\n── Script {i+1} ──")
        print(s)

    print(f"\nStats: {solidifier.get_template_stats()}")
