"""
SkillDiscovery — 在 WorkSpace 中发现已安装的技能

支持：
1. 按调用类型过滤（user-invoked / model-invoked / all）
2. 语义搜索（model-invoked 技能 description 匹配）
3. Router Skill（ask-matt）支持
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from galaxyos.skill_infra.skill_md_parser import SKILLMDParser, ParsedSkill


@dataclass
class DiscoveredSkill:
    name: str
    description: str
    invocation_type: str
    directory: Path
    dependencies: List[str] = field(default_factory=list)
    leading_words: List[str] = field(default_factory=list)


class SkillDiscovery:
    USER_SCOPE_DIR = Path.home() / ".pilotdeck" / "skills"
    PROJECT_SCOPE_DIR = Path(".pilotdeck") / "skills"

    def __init__(self, parser: Optional[SKILLMDParser] = None):
        self._parser = parser or SKILLMDParser()
        self._cache: Dict[str, ParsedSkill] = {}

    def discover(
        self,
        workspace_id: str = "default",
        invocation_type: str = "all",
        query: str = "",
    ) -> List[DiscoveredSkill]:
        skills = []
        for scope_dir in [self.USER_SCOPE_DIR, self.PROJECT_SCOPE_DIR]:
            if scope_dir.exists():
                skills.extend(self._scan_dir(scope_dir, invocation_type, query))
        return skills

    def get_skill(self, skill_name: str) -> Optional[ParsedSkill]:
        if skill_name in self._cache:
            return self._cache[skill_name]

        for scope_dir in [self.USER_SCOPE_DIR, self.PROJECT_SCOPE_DIR]:
            skill_dir = scope_dir / skill_name
            skill_md = skill_dir / "SKILL.md"
            if skill_md.exists():
                parsed = self._parser.parse_file(skill_md)
                self._cache[skill_name] = parsed
                return parsed
        return None

    def get_router_skill(self) -> Optional[DiscoveredSkill]:
        all_skills = self.discover(invocation_type="user-invoked")
        if len(all_skills) >= 5:
            ask_matt = next((s for s in all_skills if s.name == "ask-matt"), None)
            if ask_matt:
                return ask_matt
        return None

    def _scan_dir(
        self,
        scope_dir: Path,
        invocation_type: str = "all",
        query: str = "",
    ) -> List[DiscoveredSkill]:
        results = []
        if not scope_dir.exists():
            return results

        for skill_dir in scope_dir.iterdir():
            if not skill_dir.is_dir():
                continue
            skill_md = skill_dir / "SKILL.md"
            if not skill_md.exists():
                continue

            try:
                parsed = self._parser.parse_file(skill_md)
                self._cache[parsed.name] = parsed

                inv_type = "user-invoked" if parsed.disable_model_invocation else "model-invoked"

                if invocation_type != "all" and inv_type != invocation_type:
                    continue

                if query and query.lower() not in parsed.description.lower():
                    continue

                results.append(DiscoveredSkill(
                    name=parsed.name,
                    description=parsed.description,
                    invocation_type=inv_type,
                    directory=skill_dir,
                    dependencies=parsed.dependencies,
                    leading_words=parsed.leading_words,
                ))
            except Exception:
                continue

        return results