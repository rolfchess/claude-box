#!/usr/bin/env python3
"""PreToolUse hook: block a merge request note that uses a forbidden word.

A review comment is prose, and the writing rules apply to it. Nothing checked it
before: check-forbidden-words.py reads the input of a Write or an Edit, and
scan-changed-files.py reads `git diff`. A note goes out through `glab` or
post-draft.py and never reaches a file, so both hooks miss it.

This hook reads the Bash command the call would run, takes the note text out of
it, and matches that text against the same word list. A hit exits with code 2,
which stops the call and shows the message to Claude, so the note is reworded
before it reaches GitLab.

Commands it checks:

    glab api .../draft_notes|notes|discussions   with a heredoc body
    post-draft.py general|file|reply             with a heredoc body
    glab mr note|comment                         with -m or --message

What it cannot see: a body read from a file (`--input body.json`) or held in a
shell variable. The text is not in the command then, so there is nothing to
match.

The word list, the allowlist, the loop detection and the skipped paths all come
from check-forbidden-words.py, so this hook and the file hooks stay in step.
"""

import importlib.util
import json
import os
import re
import sys

sys.dont_write_bytecode = True

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# A command that posts a note. `glab mr view --comments` and a plain GET on
# .../notes do not match, because neither has a body to check.
POSTS_NOTE = re.compile(
    r"(?:glab\s+api\s+\S*(?:draft_notes|/notes|discussions)"
    r"|post-draft\.py\s+(?:general|file|reply)"
    r"|glab\s+mr\s+(?:note|comment))",
    re.IGNORECASE,
)

# A heredoc opener: << or <<-, an optional quote, then the terminator word.
HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")

# The value of -m or --message, in single quotes, double quotes or bare.
MESSAGE = re.compile(
    r"(?:-m|--message)[=\s]+(?:'([^']*)'|\"([^\"]*)\"|(\S+))",
)

# The merge request number, so repeated attempts on one note are counted
# together even after a rewording.
MR_ID = re.compile(r"merge_requests/(\d+)|(?:general|file|reply)\s+(\d+)|mr\s+(?:note|comment)\s+(\d+)")


def load_checker():
    """The check-forbidden-words module, imported by path.

    Its file name has dashes, so a plain import statement does not reach it.
    """
    path = os.path.join(SCRIPT_DIR, "check-forbidden-words.py")
    spec = importlib.util.spec_from_file_location("check_forbidden_words", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def heredoc_bodies(command):
    """The body of every heredoc in the command."""
    bodies = []
    lines = command.splitlines()
    index = 0
    while index < len(lines):
        match = HEREDOC.search(lines[index])
        if not match:
            index += 1
            continue
        terminator = match.group(2)
        body = []
        index += 1
        while index < len(lines) and lines[index].strip() != terminator:
            body.append(lines[index])
            index += 1
        index += 1  # step over the terminator
        if body:
            bodies.append("\n".join(body))
    return bodies


def note_text(command):
    """The prose the command would post, or an empty string."""
    parts = heredoc_bodies(command)
    for match in MESSAGE.finditer(command):
        value = match.group(1) or match.group(2) or match.group(3) or ""
        if value:
            parts.append(value)
    return "\n".join(parts)


def note_key(command):
    """A name for the note being posted, stable across a rewording."""
    match = MR_ID.search(command)
    if match:
        number = next((group for group in match.groups() if group), None)
        if number:
            return "review-note:mr-" + number
    return "review-note:unknown"


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # never block on a hook failure

    command = (data.get("tool_input") or {}).get("command")
    if not isinstance(command, str) or not POSTS_NOTE.search(command):
        return 0

    text = note_text(command)
    if not text.strip():
        return 0

    checker = load_checker()
    found = checker.find_forbidden(text, checker.load_allowlist())
    key = note_key(command)
    if not found:
        checker.clear_block(key)
        return 0

    count = checker.record_block(key)
    words = ", ".join(f'"{word}"' for word in found)
    message = (
        f"Writing-style check failed. This merge request note uses {words}.\n"
        "See ~/.claude/rules/writing-style.md for the plain-word replacement. "
        "Reword the note and post it again."
    )
    if count >= checker.LOOP_THRESHOLD:
        message += (
            f"\n\nThis note has been blocked {count} times in a row. Stop "
            "rewriting. Tell the user which word is blocking and that the "
            "original wording may be correct, then ask how to proceed: reword, "
            "add an allowing phrase to "
            "~/.claude/scripts/writing-style/allowlist.txt, or post it as it is."
        )
    print(message, file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # a hook failure must not block the call
        sys.exit(0)
