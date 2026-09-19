#!/usr/bin/env python3
"""Computes the next semantic-version bump from Conventional Commits history.

Single source of truth shared by two entry points:

    python scripts/versioning.py validate-message <commit-msg-file>
        Used by the `commit-msg` git hook (scripts/githooks/commit-msg,
        installed via `make hooks`). Rejects a commit whose subject line isn't
        `<type>[(scope)][!]: description` with a recognized type, and prints a
        preview of the bump the next release would pick up.

    python scripts/versioning.py next [--kind major|minor|patch]
        Used by `make release` (auto) / `make release-patch|minor|major`
        (override). Re-scans `git log <last tag>..HEAD` rather than trusting
        any state the hook may have seen, so it stays correct even if the
        release is cut on a machine where the hook was never installed.

Bump rules (Conventional Commits, Angular-style type set):
    feat                          -> minor
    fix, perf, security           -> patch
    docs, chore, refactor, style,
    test, build, ci, rename       -> none (doesn't trigger a release by itself)
    `<type>!:` or a `BREAKING CHANGE:` footer, on ANY type -> major
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

MINOR_TYPES = {"feat"}
PATCH_TYPES = {"fix", "perf", "security"}
NONE_TYPES = {"docs", "chore", "refactor", "style", "test", "build", "ci", "rename"}
VALID_TYPES = MINOR_TYPES | PATCH_TYPES | NONE_TYPES

_SUBJECT_RE = re.compile(r"^(?P<type>[a-z]+)(\([^)]*\))?(?P<bang>!)?:\s+\S")
_BREAKING_RE = re.compile(r"^BREAKING[ -]CHANGE:", re.MULTILINE)
_EXEMPT_RE = re.compile(r"^(Merge |Revert |fixup!|squash!)")

_LEVELS = {"none": 0, "patch": 1, "minor": 2, "major": 3}


def classify(subject: str, body: str = "") -> str | None:
    """Returns "major"/"minor"/"patch"/"none", or None if `subject` doesn't
    match `<type>[(scope)][!]: description` with a recognized type."""
    subject = subject.strip()
    if _EXEMPT_RE.match(subject):
        return "none"
    match = _SUBJECT_RE.match(subject)
    if not match:
        return None
    commit_type = match.group("type")
    if commit_type not in VALID_TYPES:
        return None
    if match.group("bang") or _BREAKING_RE.search(body or ""):
        return "major"
    if commit_type in MINOR_TYPES:
        return "minor"
    if commit_type in PATCH_TYPES:
        return "patch"
    return "none"


def strip_comments(message: str) -> str:
    return "\n".join(line for line in message.splitlines() if not line.startswith("#"))


def split_subject_body(message: str) -> tuple[str, str]:
    cleaned = strip_comments(message).strip("\n")
    if not cleaned:
        return "", ""
    lines = cleaned.split("\n")
    return lines[0], "\n".join(lines[1:])


def last_tag() -> str | None:
    result = subprocess.run(
        ["git", "describe", "--tags", "--abbrev=0", "--match=v*"],
        capture_output=True,
        text=True,
    )
    return result.stdout.strip() or None


def commits_since(tag: str | None) -> list[tuple[str, str]]:
    range_spec = f"{tag}..HEAD" if tag else "HEAD"
    result = subprocess.run(
        ["git", "log", range_spec, "--no-merges", "--pretty=format:%s%x00%b%x03"],
        capture_output=True,
        text=True,
        check=True,
    )
    commits = []
    for chunk in result.stdout.split("\x03"):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        subject, _, body = chunk.partition("\x00")
        commits.append((subject, body))
    return commits


def aggregate(commits: list[tuple[str, str]]) -> str | None:
    """Highest severity across `commits`. Unparseable/pre-convention commits
    count as "none" so old history never blocks a release. Returns None if
    nothing in the range would trigger a release."""
    best = "none"
    for subject, body in commits:
        level = classify(subject, body) or "none"
        if _LEVELS[level] > _LEVELS[best]:
            best = level
    return None if best == "none" else best


def cmd_validate_message(path: str) -> int:
    message = Path(path).read_text(encoding="utf-8")
    subject, body = split_subject_body(message)
    if not subject:
        return 0  # empty message: let git's own "aborting commit" handle it
    level = classify(subject, body)
    if level is None:
        valid = ", ".join(sorted(VALID_TYPES))
        print(
            "commit-msg: subject line must be `<type>[(scope)][!]: description`\n"
            f"  got:         {subject!r}\n"
            f"  valid types: {valid}\n"
            "  breaking change: `<type>!:` prefix or a `BREAKING CHANGE:` footer\n"
            "  example:     feat(cli): add --json output to `status`",
            file=sys.stderr,
        )
        return 1
    bump = aggregate(commits_since(last_tag()) + [(subject, body)])
    if bump:
        print(f"commit-msg: next release would bump to a {bump} version")
    return 0


def cmd_next(kind: str | None) -> int:
    if kind:
        print(kind)
        return 0
    bump = aggregate(commits_since(last_tag()))
    if bump is None:
        print(
            "versioning: no feat/fix/perf/security/breaking-change commit since "
            "the last tag -- nothing to release "
            "(use `make release-patch` to force one anyway)",
            file=sys.stderr,
        )
        return 2
    print(bump)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_validate = sub.add_parser("validate-message")
    p_validate.add_argument("path")

    p_next = sub.add_parser("next")
    p_next.add_argument("--kind", choices=("major", "minor", "patch"), default=None)

    args = parser.parse_args()
    if args.command == "validate-message":
        return cmd_validate_message(args.path)
    return cmd_next(args.kind)


if __name__ == "__main__":
    raise SystemExit(main())
