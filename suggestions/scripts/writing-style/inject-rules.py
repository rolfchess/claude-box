#!/usr/bin/env python3
"""Print the writing-style rules again, at the end of the context.

The rules in ~/.claude/rules/writing-style.md are part of every request already,
in the instructions block near the start. Nothing removes them, not even
compaction. What fades is attention: the further a rule sits from the end of the
context, the less it shapes the answer, and in a long session the recent code and
tool output win. Printing the rules again puts them where the model reads them.

Three modes, one per hook event. Each reads the hook input JSON from stdin.

    --mode=prompt    UserPromptSubmit. Its stdout is shown to Claude, so the
                     rules land at the end of the context on every user turn.
    --mode=batch     PostToolBatch. Fires once after each batch of tool calls
                     resolves, which is the only re-injection point in a long run
                     with no user turn. Prints the rules every EVERY_N batches,
                     and sooner when the batch wrote a documentation file.
    --mode=compact   PreCompact. Its stdout is appended to the compact
                     instructions, which keeps the rules in the summary.

Injected context is cut off at 8000 characters and 200 lines, so a rules file
longer than that is trimmed here first.

The script does nothing when the rules file is absent, and never exits non-zero:
a failure here must not stop a turn. On PostToolBatch an exit code of 2 stops the
agentic loop, so this mode reports through stdout only.
"""

import hashlib
import json
import os
import sys
import tempfile

RULES = os.environ.get("CLAUDE_WRITING_STYLE_RULES") or os.path.join(
    os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"),
    "rules", "writing-style.md",
)

# Batches between two prints in a run without user turns.
EVERY_N = int(os.environ.get("CLAUDE_WRITING_STYLE_EVERY_N") or 15)

# After a batch that wrote a documentation file, print again once this many
# batches have passed since the last print.
PROSE_GAP = 3

# Suffixes of files that are prose from the first character. A code file holds
# prose too, in its comments, but those are covered by the regular print.
PROSE_SUFFIXES = (".md", ".mdx", ".markdown", ".txt", ".rst", ".adoc")

# The limits on injected context.
MAX_CHARS = 7500
MAX_LINES = 180


def rules_text():
    """The rules, trimmed to what an injection can hold, or None."""
    try:
        with open(RULES, encoding="utf-8") as handle:
            text = handle.read().strip()
    except OSError:
        return None
    if not text:
        return None
    lines = text.splitlines()[:MAX_LINES]
    return "\n".join(lines)[:MAX_CHARS]


def wrapped(text):
    return (
        "<writing-style-rules>\n"
        "These rules are already in your instructions. They are repeated here "
        "because they apply to every word you write from now on: code comments, "
        "commit messages, documentation and your answers.\n\n"
        f"{text}\n"
        "</writing-style-rules>"
    )


def state_path(session_id):
    directory = os.path.join(tempfile.gettempdir(), "claude-writing-style")
    name = hashlib.sha1((session_id or "none").encode("utf-8")).hexdigest()
    return os.path.join(directory, "batches-" + name + ".json")


def load_state(session_id):
    try:
        with open(state_path(session_id), encoding="utf-8") as handle:
            saved = json.load(handle)
        return int(saved.get("count", 0)), int(saved.get("printed", 0))
    except (OSError, ValueError, TypeError):
        return 0, 0


def save_state(session_id, count, printed):
    path = state_path(session_id)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"count": count, "printed": printed}, handle)
    except OSError:
        pass


def wrote_prose(data):
    """True when a tool call in this batch wrote a documentation file."""
    for call in data.get("tool_calls") or []:
        if not isinstance(call, dict):
            continue
        tool_input = call.get("tool_input")
        if not isinstance(tool_input, dict):
            continue
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        if isinstance(path, str) and path.lower().endswith(PROSE_SUFFIXES):
            return True
    return False


def batch_mode(data, text):
    """Print the rules when enough tool batches have passed."""
    session_id = data.get("session_id")
    count, printed = load_state(session_id)
    count += 1

    since = count - printed
    due = since >= EVERY_N or (wrote_prose(data) and since >= PROSE_GAP)
    if due:
        printed = count
    save_state(session_id, count, printed)

    if not due:
        return
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolBatch",
            "additionalContext": wrapped(text),
        },
        "suppressOutput": True,
    }))


def main() -> int:
    mode = "prompt"
    for argument in sys.argv[1:]:
        if argument.startswith("--mode="):
            mode = argument.split("=", 1)[1]

    text = rules_text()
    if not text:
        return 0

    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        data = {}

    if mode == "prompt":
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": wrapped(text),
            },
            "suppressOutput": True,
        }))
    elif mode == "batch":
        batch_mode(data, text)
    elif mode == "compact":
        print(
            "Keep the writing-style rules in the summary, in full and word for "
            f"word, under their own heading:\n\n{text}"
        )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # a hook failure must not stop the turn
        sys.exit(0)
