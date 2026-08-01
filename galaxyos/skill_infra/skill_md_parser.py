"""
SKILLMDParser — 解析 mattpocock/skills 格式的 SKILL.md 文件

支持：
1. YAML frontmatter（name, description, disable-model-invocation, dependencies）
2. Markdown 正文步骤解析（completion_criterion, leading_words）
3. references/ 目录引用关系解析
4. leading words 提取
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

import yaml


@dataclass
class SkillStep:
    text: str
    completion_criterion: Optional[str] = None
    leading_words: List[str] = field(default_factory=list)


@dataclass
class SkillReference:
    path: str
    load_trigger: Optional[str] = None


@dataclass
class ParsedSkill:
    name: str
    description: str
    disable_model_invocation: bool = False
    leading_words: List[str] = field(default_factory=list)
    steps: List[SkillStep] = field(default_factory=list)
    references: List[SkillReference] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    raw_body: str = ""


_LEADING_WORDS_PATTERN = re.compile(
    r'"([^"]+)"|'  # quoted terms
    r'`([^`]+)`|'  # backtick terms
    r'\b([A-Z][a-z]+(?:[A-Z][a-z]+)+)\b',  # CamelCase
)

_COMPLETION_CRITERION_PATTERN = re.compile(
    r'(?:when|once|after|until|verify|confirm|ensure|check)\s+'
    r'(.+?)(?:\.\s|\.$|\n)',
    re.IGNORECASE,
)

_CONTEXT_POINTER_PATTERN = re.compile(
    r'(?:see|refer to|consult|check)\s+`?([^`\n]+\.md)`?',
    re.IGNORECASE,
)


class SKILLMDParser:
    def parse(self, content: str, skill_dir: Optional[Path] = None) -> ParsedSkill:
        frontmatter, body = self._split_frontmatter(content)

        name = frontmatter.get("name", "")
        description = frontmatter.get("description", "")
        disable_model_invocation = frontmatter.get("disable-model-invocation", False)
        dependencies = frontmatter.get("dependencies", [])
        if isinstance(dependencies, str):
            dependencies = [dependencies]

        leading_words = self._extract_leading_words(description, body)
        steps = self._parse_steps(body)
        references = self._parse_references(body)

        if skill_dir and skill_dir.exists():
            references.extend(self._scan_references_dir(skill_dir))

        return ParsedSkill(
            name=name,
            description=description,
            disable_model_invocation=disable_model_invocation,
            leading_words=leading_words,
            steps=steps,
            references=references,
            dependencies=dependencies,
            raw_body=body,
        )

    def parse_file(self, path: Path) -> ParsedSkill:
        content = path.read_text(encoding="utf-8")
        return self.parse(content, skill_dir=path.parent)

    def _split_frontmatter(self, content: str) -> tuple:
        if not content.startswith("---"):
            return {}, content
        parts = content.split("---", 2)
        if len(parts) < 3:
            return {}, content
        try:
            frontmatter = yaml.safe_load(parts[1]) or {}
        except yaml.YAMLError:
            frontmatter = {}
        body = parts[2].lstrip("\n")
        return frontmatter, body

    def _extract_leading_words(self, description: str, body: str) -> List[str]:
        words = set()
        for text in [description, body[:500]]:
            for match in _LEADING_WORDS_PATTERN.finditer(text):
                for group in match.groups():
                    if group and len(group) > 2:
                        words.add(group)
        return sorted(words)

    def _parse_steps(self, body: str) -> List[SkillStep]:
        steps = []
        lines = body.split("\n")
        current_step_lines: List[str] = []

        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#") or stripped.startswith("##"):
                if current_step_lines:
                    steps.append(self._build_step(current_step_lines))
                current_step_lines = [stripped]
            elif stripped.startswith("- ") or stripped.startswith("* "):
                if current_step_lines:
                    steps.append(self._build_step(current_step_lines))
                current_step_lines = [stripped]
            else:
                current_step_lines.append(stripped)

        if current_step_lines:
            steps.append(self._build_step(current_step_lines))

        return steps

    def _build_step(self, lines: List[str]) -> SkillStep:
        text = "\n".join(lines).strip()
        criterion = None
        match = _COMPLETION_CRITERION_PATTERN.search(text)
        if match:
            criterion = match.group(1).strip()
        leading = self._extract_leading_words(text, "")
        return SkillStep(text=text, completion_criterion=criterion, leading_words=leading)

    def _parse_references(self, body: str) -> List[SkillReference]:
        refs = []
        for match in _CONTEXT_POINTER_PATTERN.finditer(body):
            path = match.group(1).strip()
            trigger = f"see {path}"
            refs.append(SkillReference(path=path, load_trigger=trigger))
        return refs

    def _scan_references_dir(self, skill_dir: Path) -> List[SkillReference]:
        refs = []
        refs_dir = skill_dir / "references"
        if refs_dir.is_dir():
            for f in refs_dir.iterdir():
                if f.suffix in (".md", ".txt"):
                    refs.append(SkillReference(
                        path=str(f.relative_to(skill_dir)),
                        load_trigger=f"reference: {f.name}",
                    ))
        glossary = skill_dir / "GLOSSARY.md"
        if glossary.exists():
            refs.append(SkillReference(
                path="GLOSSARY.md",
                load_trigger="glossary lookup",
            ))
        return refs
