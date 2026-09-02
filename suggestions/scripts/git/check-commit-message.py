#!/usr/bin/env python3
"""PreToolUse hook: block a commit or request body that credits Claude.

A commit belongs to the user. A `Co-Authored-By: Claude` trailer, or a
"Generated with Claude Code" line in a pull request body, puts the tool in the
history instead. Both are forbidden by ~/.claude/rules/git-commits.md, and the
model adds them anyway, because its own instructions ask for them.

This hook reads the Bash command the call would run and blocks it when the
command creates a commit, a pull request or a merge request and its text has one
of those lines. A hit exits with code 2, which stops the call and shows the
message to Claude, so the line is removed before anything is written.

Which command creates one is decided on the command with its heredoc bodies
removed, and the text of those bodies is then searched for the forbidden lines.
That way `git commit -F -` with the message in a heredoc is caught, while a
heredoc that merely writes *about* a trailer, such as this file or the README, is
not. The forbidden line must also stand at the start of its own line, so a
sentence that names a trailer is left alone.

It reads the command itself, which covers `-m` and a heredoc body. It also reads
a message or body file named on the command line. What it cannot see is a
message held in a shell variable, written by another process, or typed into the
editor. A git `commit-msg` hook is the place to catch those, because git runs it
whatever wrote the message.
"""

import json
import os
import re
import sys

sys.dont_write_bytecode = True

# A command that writes a commit message, a pull request body or a merge request
# description. It has to stand at the start of the command or after a shell
# operator, so the same words inside a longer command (`echo "gh pr create"`) do
# not match. Only an option may stand between `git` and `commit`, so a read such
# as `git log --grep "Co-Authored-By"` does not match either.
WRITES_MESSAGE = re.compile(
    r"(?:^|[;&|(\n`]|\$\()\s*"
    r"(?:git\s+(?:-[A-Za-z-]+(?:=\S+)?\s+|-[Cc]\s+\S+\s+)*commit\b"
    r"|gh\s+pr\s+(?:create|edit)\b"
    r"|glab\s+mr\s+(?:create|update)\b)",
    re.IGNORECASE,
)

# The lines that credit Claude. Each has to start its own line, which is where a
# trailer and a generated-by line stand, so prose that names one does not match.
ATTRIBUTION = re.compile(
    r"^\s*(?:[-*>#]\s*)?(?:\W{0,4}\s*)?"
    r"(?:co[-\s]?authored[-\s]?by:[^\n]*(?:claude|anthropic)"
    r"|generated\s+with[^\n]{0,40}claude)",
    re.IGNORECASE | re.MULTILINE,
)

# A heredoc opener: << or <<-, an optional quote, then the terminator word.
HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")

# A file whose contents become the message or the body.
MESSAGE_FILE = re.compile(
    r"(?:-F|--file|--body-file|--description-file)[=\s]+(?:'([^']*)'|\"([^\"]*)\"|(\S+))",
)

MAX_FILE_BYTES = 100_000


def split_heredocs(command):
    """The command without its heredoc bodies, and those bodies on their own."""
    skeleton = []
    bodies = []
    pending = []        # terminators of the heredocs opened on the line just read
    terminator = None
    for line in command.splitlines():
        if terminator is not None:
            if line.strip() == terminator:
                terminator = pending.pop(0) if pending else None
            else:
                bodies.append(line)
            continue
        skeleton.append(line)
        pending = [match.group(2) for match in HEREDOC.finditer(line)]
        if pending:
            terminator = pending.pop(0)
    return "\n".join(skeleton), "\n".join(bodies)


def named_files(command):
    """The message and body files named on the command line."""
    paths = []
    for match in MESSAGE_FILE.finditer(command):
        path = next((group for group in match.groups() if group), None)
        if path and path != "-":
            paths.append(os.path.expanduser(path))
    return paths


def file_text(command):
    """The contents of the message files the command names."""
    parts = []
    for path in named_files(command):
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as handle:
                parts.append(handle.read(MAX_FILE_BYTES))
        except OSError:
            continue
    return "\n".join(parts)


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # never block on a hook failure

    command = (data.get("tool_input") or {}).get("command")
    if not isinstance(command, str):
        return 0

    skeleton, bodies = split_heredocs(command)
    if not WRITES_MESSAGE.search(skeleton):
        return 0

    text = "\n".join([skeleton, bodies, file_text(skeleton)])
    found = ATTRIBUTION.search(text)
    if not found:
        return 0

    print(
        f'This message credits Claude: "{found.group(0).strip()}".\n'
        "A commit and a request body belong to the user, so no trailer or line "
        "may name Claude or Anthropic. See ~/.claude/rules/git-commits.md.\n"
        "Remove the line and run the command again. Do not ask whether to keep "
        "it, and do not add it back later in the session.",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # a hook failure must not block the call
        sys.exit(0)
