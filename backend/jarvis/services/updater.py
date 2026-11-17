"""Notice a new version of Jarvis (a new commit on this checkout) and offer to restart into it.

Reads .git directly (no git process): HEAD names a branch, the branch file (or packed-refs) names the commit.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

log = logging.getLogger(__name__)


def head_commit(root: Path) -> str:
    """The commit this checkout is on, or "" when it can't be read."""
    git = root / ".git"
    try:
        head = (git / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head
        ref = head[4:].strip()
        f = git / ref
        if f.is_file():
            return f.read_text(encoding="utf-8").strip()
        packed = git / "packed-refs"
        if packed.is_file():
            m = re.search(rf"^([0-9a-f]{{40}}) {re.escape(ref)}$", packed.read_text(encoding="utf-8"), re.M)
            if m:
                return m.group(1)
    except OSError as e:
        log.debug("can't read the git head: %s", e)
    return ""


def commit_subject(root: Path, commit: str) -> str:
    """The first line of a commit's message, from the reflog (git writes it there on every commit)."""
    try:
        lines = (root / ".git" / "logs" / "HEAD").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return ""
    for line in reversed(lines[-50:]):
        parts = line.split("\t", 1)
        if len(parts) == 2 and parts[0].split(" ")[1:2] == [commit]:
            msg = parts[1]
            return re.sub(r"^commit(?: \([^)]*\))?: ", "", msg).strip()
    return ""


def spoken_subject(subject: str) -> str:
    """A commit subject Jarvis can say: no quotes or code marks, short."""
    s = re.sub(r"[\"`*_]", "", subject).strip().rstrip(".")
    return s[:120]
