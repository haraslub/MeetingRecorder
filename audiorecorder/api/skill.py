"""Install the bundled agent skill into the personal skill folders of Claude Code and Codex.

A copy, not a link: an installed build unpacks its files into a temporary folder that is
gone after it exits. A folder that is already a link, as in a development checkout, is
left alone, because it points at the source the developer edits.
"""

import os
import shutil
from pathlib import Path

SKILL_NAME = "meeting-recorder"
# From source this is the repository's skills/ folder; a build bundles it at the same place
# relative to the package (see MeetingRecorder.spec).
SKILL_SOURCE = Path(__file__).resolve().parents[2] / "skills" / SKILL_NAME


def skill_targets(home=None, env=None):
    home = Path.home() if home is None else Path(home)
    env = os.environ if env is None else env
    codex = Path(env["CODEX_HOME"]) if env.get("CODEX_HOME") else home / ".codex"
    return {
        "Claude Code": home / ".claude" / "skills" / SKILL_NAME,
        "Codex": codex / "skills" / SKILL_NAME,
    }


def install_skill(source=SKILL_SOURCE, targets=None):
    """[(agent, target, status)]; status is installed, updated, linked or refused."""
    results = []
    for agent, target in (skill_targets() if targets is None else targets).items():
        if target.is_symlink() or target.is_junction():
            status = "linked"
        elif target.exists() and not _ours(target):
            status = "refused"
        else:
            status = "updated" if target.exists() else "installed"
            shutil.copytree(source, target, dirs_exist_ok=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        results.append((agent, target, status))
    return results


def _ours(target):
    try:
        text = (target / "SKILL.md").read_text(encoding="utf-8")
    except OSError:
        return False
    return f"\nname: {SKILL_NAME}\n" in text.replace("\r\n", "\n")
