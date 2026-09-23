#!/usr/bin/env python3
"""Print the writing-style rules again, at the end of the context.

The rules in ~/.claude/rules/writing-style.md are part of every request already,
in the instructions block near the start. Nothing removes them, not even
compaction. What fades is attention: the further a rule sits from the end of the
context, the less it shapes the answer, and in a long session the recent code and
tool output win. Printing the rules again puts them where the model reads them.

Four modes, one per hook event. Each reads the hook input JSON from stdin.

    --mode=prompt    UserPromptSubmit. Its stdout is shown to Claude, so the
                     rules land at the end of the context on every user turn.
    --mode=batch     PostToolBatch. Fires once after each batch of tool calls
                     resolves, which is the only re-injection point in a long run
                     with no user turn. Prints the rules every EVERY_N batches,
                     and sooner when the batch wrote a documentation file.
    --mode=write     PreToolUse on a file edit. The text of an edit is written
                     before any hook runs, so a print that comes with the edit
                     comes too late for it. When the last print is WRITE_GAP or
                     more batches old, the edit is refused with the rules, and
                     Claude writes it again with the rules just read.
    --mode=compact   PreCompact. Its stdout is appended to the compact
                     instructions, which keeps the rules in the summary.

The "Comments" section applies only to code. It is left out of what is printed
until the session edits a file that is not documentation.

The generated word list is left out of what is printed. A regex already blocks
every word in it before the write lands, and the block message names the
replacement, so repeating the list mid-session adds nothing. What is printed is
the part no regex can check, plus one line saying the hook holds the list.

Injected context is cut off at 8000 characters and 200 lines, so a rules file
longer than that is trimmed here first.

The script does nothing when the rules file is absent, and never exits non-zero:
a failure here must not stop a turn. On PostToolBatch an exit code of 2 stops the
agentic loop, so this mode reports through stdout only.
"""

import hashlib
import importlib.util
import json
import os
import sys
import tempfile

sys.dont_write_bytecode = True

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

RULES = os.environ.get("CLAUDE_WRITING_STYLE_RULES") or os.path.join(
    os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"),
    "rules", "writing-style.md",
)

# Batches between two prints in a run without user turns.
EVERY_N = int(os.environ.get("CLAUDE_WRITING_STYLE_EVERY_N") or 15)

# After a batch that wrote a documentation file, print again once this many
# batches have passed since the last print.
PROSE_GAP = 3

# An edit is refused when the rules were last printed this many batches ago or
# more. 0 turns the check off.
WRITE_GAP = int(os.environ.get("CLAUDE_WRITING_STYLE_WRITE_GAP") or 5)

# The tools that edit a file.
EDIT_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")

# The section that is printed only once the session has edited code.
COMMENTS_HEADING = "## Comments"

# Suffixes of files that are prose from the first character. A code file holds
# prose too, in its comments, but those are covered by the regular print.
PROSE_SUFFIXES = (".md", ".mdx", ".markdown", ".txt", ".rst", ".adoc")

# The limits on injected context.
MAX_CHARS = 7500
MAX_LINES = 180


def load_checker():
    """The check-forbidden-words module, imported by path.

    Its file name has dashes, so a plain import statement does not reach it.
    """
    path = os.path.join(SCRIPT_DIR, "check-forbidden-words.py")
    spec = importlib.util.spec_from_file_location("check_forbidden_words", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def without_section(text, heading):
    """The text without the section under `heading`, up to the next heading or comment."""
    start = text.find("\n" + heading + "\n")
    if start < 0:
        return text
    ends = [i for i in (text.find("\n## ", start + 1), text.find("\n<!--", start + 1)) if i >= 0]
    end = min(ends) if ends else len(text)
    return text[:start] + text[end:]


def rules_text(with_comments=True):
    """The rules, without the word list, trimmed to what fits, or None."""
    try:
        with open(RULES, encoding="utf-8") as handle:
            text = handle.read().strip()
    except OSError:
        return None
    if not text:
        return None
    try:
        text = load_checker().rules_without_words(text).strip()
    except Exception:
        pass  # print the rules in full rather than none at all
    if not with_comments:
        text = without_section(text, COMMENTS_HEADING)
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
    """The batch count, the batch of the last print, and whether code was edited."""
    try:
        with open(state_path(session_id), encoding="utf-8") as handle:
            saved = json.load(handle)
        return {
            "count": int(saved.get("count", 0)),
            "printed": int(saved.get("printed", 0)),
            "code": bool(saved.get("code", False)),
        }
    except (OSError, ValueError, TypeError, AttributeError):
        return {"count": 0, "printed": 0, "code": False}


def save_state(session_id, state):
    path = state_path(session_id)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(state, handle)
    except OSError:
        pass


def edited_path(tool_input):
    """The file a tool call edits, or an empty string."""
    if not isinstance(tool_input, dict):
        return ""
    path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
    return path if isinstance(path, str) else ""


def is_prose(path):
    return path.lower().endswith(PROSE_SUFFIXES)


def edited_paths(data):
    """The files the tool calls in this batch edit."""
    paths = []
    for call in data.get("tool_calls") or []:
        if isinstance(call, dict) and call.get("tool_name") in EDIT_TOOLS:
            path = edited_path(call.get("tool_input"))
            if path:
                paths.append(path)
    return paths


def batch_mode(data):
    """Print the rules when enough tool batches have passed."""
    session_id = data.get("session_id")
    state = load_state(session_id)
    state["count"] += 1

    paths = edited_paths(data)
    if any(not is_prose(path) for path in paths):
        state["code"] = True

    since = state["count"] - state["printed"]
    due = since >= EVERY_N or (any(map(is_prose, paths)) and since >= PROSE_GAP)
    if due:
        state["printed"] = state["count"]
    save_state(session_id, state)

    text = rules_text(state["code"]) if due else None
    if not text:
        return
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PostToolBatch",
            "additionalContext": wrapped(text),
        },
        "suppressOutput": True,
    }))


def write_mode(data):
    """Refuse an edit when the rules were last printed too long ago."""
    session_id = data.get("session_id")
    state = load_state(session_id)
    path = edited_path(data.get("tool_input"))
    if not path:
        return
    if not is_prose(path):
        state["code"] = True

    stale = WRITE_GAP > 0 and state["count"] - state["printed"] >= WRITE_GAP
    if stale:
        state["printed"] = state["count"]
    save_state(session_id, state)

    text = rules_text(state["code"]) if stale else None
    if not text:
        return
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "Nothing was written. The writing-style rules are far back in "
                "the context, so read them now and make the same edit again, "
                "following them.\n\n" + wrapped(text)
            ),
        },
        "suppressOutput": True,
    }))


def main() -> int:
    mode = "prompt"
    for argument in sys.argv[1:]:
        if argument.startswith("--mode="):
            mode = argument.split("=", 1)[1]

    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}

    if mode == "batch":
        batch_mode(data)
        return 0
    if mode == "write":
        write_mode(data)
        return 0

    session_id = data.get("session_id")
    state = load_state(session_id)
    text = rules_text(state["code"])
    if not text:
        return 0

    if mode == "prompt":
        state["printed"] = state["count"]
        save_state(session_id, state)
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": wrapped(text),
            },
            "suppressOutput": True,
        }))
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
