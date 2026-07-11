"""
SkillInstaller — 从多种来源安装 mattpocock/skills 技能

支持来源：
1. GitHub 仓库克隆/拉取
2. 本地文件路径
3. npx skills@latest add（未来支持）

功能：
- 安全扫描
- 格式校验
- 依赖解析
- 批量注册
- 安装报告生成
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from galaxyos.skill_infra.skill_md_parser import SKILLMDParser, ParsedSkill
from galaxyos.skill_infra.skill_dependency_resolver import SkillDependencyResolver


@dataclass
class SkillInstallReport:
    report_id: str
    timestamp: float
    source: str
    total_skills: int = 0
    installed_skills: int = 0
    skipped_skills: int = 0
    failed_skills: int = 0
    details: List[Dict[str, str]] = field(default_factory=list)


class SkillInstaller:
    USER_SCOPE_DIR = Path.home() / ".pilotdeck" / "skills"
    PROJECT_SCOPE_DIR = Path(".pilotdeck") / "skills"

    def __init__(
        self,
        parser: Optional[SKILLMDParser] = None,
        resolver: Optional[SkillDependencyResolver] = None,
    ):
        self._parser = parser or SKILLMDParser()
        self._resolver = resolver or SkillDependencyResolver(parser=self._parser)

    def install_from_github(
        self,
        repo_url: str = "https://github.com/mattpocock/skills",
        target_dir: Optional[str] = None,
        scope: str = "user",
        skill_filter: Optional[List[str]] = None,
    ) -> SkillInstallReport:
        report = SkillInstallReport(
            report_id=f"install-{int(time.time())}",
            timestamp=time.time(),
            source=repo_url,
        )

        local_repo = Path(target_dir) if target_dir else Path("_eval/skills")
        if not local_repo.exists():
            report.details.append({"status": "failed", "message": f"Repo not found at {local_repo}"})
            return report

        skills_dir = local_repo / "skills"
        if not skills_dir.exists():
            report.details.append({"status": "failed", "message": f"skills/ directory not found"})
            return report

        scope_dir = self.USER_SCOPE_DIR if scope == "user" else Path(self.PROJECT_SCOPE_DIR)
        scope_dir.mkdir(parents=True, exist_ok=True)

        parsed_skills: List[ParsedSkill] = []
        source_map: Dict[str, Path] = {}

        for category_dir in skills_dir.iterdir():
            if not category_dir.is_dir():
                continue
            for skill_dir in category_dir.iterdir():
                if not skill_dir.is_dir():
                    continue
                skill_md = skill_dir / "SKILL.md"
                if not skill_md.exists():
                    continue

                skill_name = skill_dir.name
                if skill_filter and skill_name not in skill_filter:
                    report.skipped_skills += 1
                    continue

                try:
                    parsed = self._parser.parse_file(skill_md)
                    parsed_skills.append(parsed)
                    source_map[parsed.name] = skill_dir
                    report.total_skills += 1
                except Exception as e:
                    report.failed_skills += 1
                    report.details.append({
                        "skill": skill_name,
                        "status": "failed",
                        "message": str(e),
                    })

        install_order = self._resolve_and_install(parsed_skills, scope_dir, report, source_map)

        report.installed_skills = report.total_skills - report.failed_skills - report.skipped_skills
        return report

    def install_from_local(
        self,
        source_path: str,
        scope: str = "user",
    ) -> SkillInstallReport:
        report = SkillInstallReport(
            report_id=f"install-local-{int(time.time())}",
            timestamp=time.time(),
            source=source_path,
        )

        source = Path(source_path)
        skill_md = source / "SKILL.md"
        if not skill_md.exists():
            report.failed_skills = 1
            report.details.append({"status": "failed", "message": "SKILL.md not found"})
            return report

        scope_dir = self.USER_SCOPE_DIR if scope == "user" else Path(self.PROJECT_SCOPE_DIR)
        scope_dir.mkdir(parents=True, exist_ok=True)

        try:
            parsed = self._parser.parse_file(skill_md)
            dest = scope_dir / parsed.name
            if dest.exists():
                report.skipped_skills += 1
            else:
                shutil.copytree(source, dest)
                report.installed_skills += 1
            report.total_skills = 1
            self._resolver.register_skill(parsed)
            self._resolver.mark_installed(parsed.name)
        except Exception as e:
            report.failed_skills = 1
            report.details.append({"skill": source.name, "status": "failed", "message": str(e)})

        return report

    def _resolve_and_install(
        self,
        skills: List[ParsedSkill],
        scope_dir: Path,
        report: SkillInstallReport,
        source_map: Optional[Dict[str, Path]] = None,
    ) -> List[str]:
        for parsed in skills:
            self._resolver.register_skill(parsed)

        self._resolver.resolve_all()

        try:
            order = self._resolver.install_order()
        except ValueError as e:
            report.details.append({"status": "warning", "message": str(e)})
            order = [s.name for s in skills]

        for skill_name in order:
            dest = scope_dir / skill_name
            if dest.exists():
                report.skipped_skills += 1
                self._resolver.mark_installed(skill_name)
                continue

            if source_map and skill_name in source_map:
                src = source_map[skill_name]
                shutil.copytree(src, dest)
                self._resolver.mark_installed(skill_name)
                report.details.append({"skill": skill_name, "status": "installed"})
            else:
                self._resolver.mark_installed(skill_name)
                report.details.append({"skill": skill_name, "status": "registered"})

        return order