#!/usr/bin/env python3
"""PreToolUse hook: check a Bash command before it runs.

Two checks read the same command, so they share one process:

    git/commit-message.py          blocks a commit or a request body that
                                   credits Claude
    writing-style/review-notes.py  blocks a merge request note that uses a
                                   forbidden word

Both need the command with its heredoc bodies removed, and those bodies on their
own. That split happens here, once, and each check reads the result. A command
that writes a message inside a heredoc is checked, while a heredoc that writes
*about* a message is not.

The first check with something to say ends it. Its message goes to stderr and
the hook exits with code 2, which stops the tool call and shows the message to
Claude. A missing or broken check is skipped, because a failure here must not
block the call.
"""

import importlib.util
import json
import os
import re
import sys

sys.dont_write_bytecode = True

SCRIPTS_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Each check is a module and the function in it that reads (skeleton, bodies).
CHECKS = (
    ("git", "commit-message.py", "check"),
    ("writing-style", "review-notes.py", "check"),
)

# A heredoc opener: << or <<-, an optional quote, then the terminator word.
HEREDOC = re.compile(r"<<-?\s*(['\"]?)([A-Za-z_][A-Za-z0-9_]*)\1")


def load(group, name):
    """A check module, imported by path, or None when it cannot be read.

    Its file name has dashes, so a plain import statement does not reach it.
    """
    path = os.path.join(SCRIPTS_DIR, group, name)
    try:
        module_name = os.path.splitext(name)[0].replace("-", "_")
        spec = importlib.util.spec_from_file_location(module_name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    except Exception:
        return None


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


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # never block on a hook failure

    command = (data.get("tool_input") or {}).get("command")
    if not isinstance(command, str) or not command.strip():
        return 0

    skeleton, bodies = split_heredocs(command)

    for group, name, function in CHECKS:
        module = load(group, name)
        if module is None:
            continue
        try:
            message = getattr(module, function)(skeleton, bodies)
        except Exception:
            continue
        if message:
            print(message, file=sys.stderr)
            return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # a hook failure must not block the call
        sys.exit(0)
