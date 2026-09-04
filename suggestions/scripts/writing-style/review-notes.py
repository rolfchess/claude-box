#!/usr/bin/env python3
"""Block a merge request note that uses a forbidden word.

bash/check-bash-command.py calls check() for every Bash call, with the command
already split into its skeleton and its heredoc bodies.

A review comment is prose, and the writing rules apply to it. Nothing checked it
before: check-forbidden-words.py reads the input of a Write or an Edit, and
scan-changed-files.py reads `git diff`. A note goes out through `glab` or
post-draft.py and never reaches a file, so both hooks miss it.

The check reads the Bash command the call would run, takes the note text out of
it, and matches that text against the same word list. The caller then stops the
call and shows the message to Claude, so the note is reworded before it reaches
GitLab. The message names the replacement for each word.

Which command posts a note is decided on the skeleton, so a heredoc that writes
*about* posting one is left alone.

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


def note_text(skeleton, bodies):
    """The prose the command would post, or an empty string."""
    parts = [bodies] if bodies.strip() else []
    for match in MESSAGE.finditer(skeleton):
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


def check(skeleton, bodies):
    """The message to show Claude, or None when the note may be posted.

    `skeleton` is the command without its heredoc bodies, `bodies` is those
    bodies on their own.
    """
    if not POSTS_NOTE.search(skeleton):
        return None

    text = note_text(skeleton, bodies)
    if not text.strip():
        return None

    checker = load_checker()
    found = checker.find_forbidden(text, checker.load_allowlist())
    key = note_key(skeleton)
    if not found:
        checker.clear_block(key)
        return None

    count = checker.record_block(key)
    message = (
        "Writing-style check failed. This merge request note uses a forbidden "
        "word. Use the replacement:\n"
        f"{checker.describe(found)}\n"
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
    return message
