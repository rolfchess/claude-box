#!/usr/bin/env python3
"""PreToolUse hook: block Write/Edit when the new content uses a forbidden word.

The forbidden words come from ~/.claude/rules/writing-style.md. When one is found,
the hook exits with code 2, which stops the tool call and shows the message below
to Claude. Claude then rewords and tries again.

Read the hook input JSON from stdin and scan the text the call would put in the
file: `content` for Write, `new_string` for Edit, every `edits[].new_string` for
MultiEdit, and `new_source` for NotebookEdit. Files that legitimately quote the
forbidden words (the rules doc, this script, and memory files) are skipped.

Changes made any other way -- a Bash command, an MCP server, a subagent -- are not
visible here. scan-changed-files.py checks those against the same word list.

Escape routes for a word that is genuinely correct:
- Add the exact phrase (a whole clause or sentence) to allowlist.txt next to this
  script. Only text inside that phrase is exempt, so allowing one sentence does not
  un-ban the word everywhere else.
- After the same file is blocked several times in a row, the message tells Claude to
  stop and ask the user rather than keep rewriting.
"""

import hashlib
import json
import os
import re
import sys
import tempfile
import time

# Each entry is a regex matched case-insensitively against the new content.
# Word boundaries keep them off innocent substrings (for example "gate" does not
# match "gateway", "utilise" does not match "utility").
FORBIDDEN = [
    # From writing-style.md "Words to avoid".
    r"appetite",
    r"mint(?:s|ed|ing)?",
    r"exercising",
    r"keyed on",
    r"key on",
    r"carr(?:y|ies|ying|ied)",
    r"pinned",
    r"pinning",
    r"surfac(?:es|ing|ed)",
    r"gate",
    r"gated",
    r"gating",
    r"hold onto",
    r"travel(?:s|ling|led)? with",
    # Fancy verbs where a plain one works.
    r"scaffold(?:s|ed|ing)?",
    r"fold(?:s|ed|ing)?",
    r"leverag(?:e|es|ed|ing)",
    r"utili[sz](?:e|es|ed|ing|ation)",
    r"facilitat(?:e|es|ed|ing|ion)",
    r"streamlin(?:e|es|ed|ing)",
    r"delv(?:e|es|ed|ing)",
    r"empower(?:s|ed|ing|ment)?",
    r"underpin(?:s|ned|ning)?",
    r"bolster(?:s|ed|ing)?",
    r"elevat(?:e|es|ed|ing)",
    r"spearhead(?:s|ed|ing)?",
    r"foster(?:s|ed|ing)?",
    r"boast(?:s|ed|ing)?",
    # Vague or marketing adjectives.
    r"robust",
    r"powerful",
    r"seamless(?:ly)?",
    r"intuitive",
    r"delightful",
    r"effortless(?:ly)?",
    r"holistic",
    r"cutting-edge",
    r"state-of-the-art",
    r"best-in-class",
    r"game-chang(?:er|ing)",
    # Jargon nouns.
    r"synerg(?:y|ies)",
    r"paradigm(?:s)?",
    r"tapestry",
    r"seed(?:s|ed|ing)?",
    # Filler.
    r"basically",
    r"essentially",
    r"simply",
    r"in order to",
    # Idioms and metaphors.
    r"escape hatch(?:es)?",
    r"deep[ -]dive",
    r"low-hanging fruit",
    r"move the needle",
    r"circle back",
    r"double down",
    r"lean into",
    r"pave the way",
    r"baked[ -]in",
    r"out[ -]of[ -]the[ -]box",
    r"at the end of the day",
    r"(?:spin|spins|spinning|spun) up",
    r"(?:wire|wires|wired|wiring) up",
    r"(?:kick|kicks|kicked|kicking) off",
    # Latin-isms; use plain English instead.
    r"caveat(?:s)?",
    r"vis-à-vis",
    r"vis-a-vis",
    r"per se",
    r"ergo",
    r"de facto",
    r"de jure",
    r"ad[- ]hoc",
    r"a[- ]priori",
    r"bona[- ]fide",
    r"status quo",
    r"vice[- ]versa",
    r"in situ",
    r"prima facie",
    r"ipso facto",
    r"quid pro quo",
    r"modus operandi",
    r"inter alia",
    r"circa",
]

# Abbreviations end in a full stop, so a trailing \b does not fit; match them
# with a leading letter-boundary only.
ABBREVIATIONS = [
    r"e\.g\.",
    r"i\.e\.",
    r"etc\.",
    r"et al\.?",
    r"viz\.",
    r"cf\.",
]

PATTERNS = [
    re.compile(r"\b(" + "|".join(FORBIDDEN) + r")\b", re.IGNORECASE),
    re.compile(r"(?<![A-Za-z])(" + "|".join(ABBREVIATIONS) + r")", re.IGNORECASE),
]

# Paths that are allowed to contain the forbidden words as data or examples.
SKIP_PATH_MARKERS = ("writing-style", "check-forbidden-words", "/memory/")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ALLOWLIST_FILE = os.path.join(SCRIPT_DIR, "allowlist.txt")

# Loop detection: after this many blocks on the same file within the window, the
# message tells Claude to stop rewriting and ask the user.
LOOP_THRESHOLD = 3
LOOP_WINDOW_SECONDS = 900


def load_allowlist():
    """Return the exact phrases the user allows, lowercased.

    Each phrase exempts only the text it covers, not the word everywhere. Put a
    whole clause or sentence here, not a bare word, unless you really do mean to
    allow that word in every position.
    """
    try:
        with open(ALLOWLIST_FILE, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    phrases = []
    for line in lines:
        text = line.strip().lower()
        if text and not text.startswith("#"):
            phrases.append(text)
    return phrases


def exempt_ranges(content, phrases):
    """Character ranges in content covered by an allowed phrase."""
    lowered = content.lower()
    ranges = []
    for phrase in phrases:
        start = 0
        while True:
            index = lowered.find(phrase, start)
            if index < 0:
                break
            ranges.append((index, index + len(phrase)))
            start = index + len(phrase)
    return ranges


def find_forbidden(content, phrases):
    ranges = exempt_ranges(content, phrases)
    found = []
    seen = set()
    for pattern in PATTERNS:
        for match in pattern.finditer(content):
            if any(match.start() >= a and match.end() <= b for a, b in ranges):
                continue
            word = match.group(0)
            low = word.lower()
            if low in seen:
                continue
            seen.add(low)
            found.append(word)
    return found


def new_content(tool_input):
    """The text a Write, Edit, MultiEdit or NotebookEdit call would put in the file.

    Write stores it in `content`, Edit in `new_string`, MultiEdit in a list of
    `edits` each with their own `new_string`, and NotebookEdit in `new_source`.
    """
    parts = []
    for key in ("content", "new_string", "new_source"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            parts.append(value)
    for edit in tool_input.get("edits") or []:
        if isinstance(edit, dict) and isinstance(edit.get("new_string"), str):
            parts.append(edit["new_string"])
    return "\n".join(parts)


def state_path(file_path):
    key = hashlib.sha1(file_path.encode("utf-8")).hexdigest()
    directory = os.path.join(tempfile.gettempdir(), "claude-writing-style")
    return os.path.join(directory, key + ".json")


def record_block(file_path):
    """Count consecutive blocks on a file and return the new count."""
    path = state_path(file_path)
    now = time.time()
    count = 0
    try:
        with open(path, encoding="utf-8") as handle:
            saved = json.load(handle)
        if now - saved.get("ts", 0) <= LOOP_WINDOW_SECONDS:
            count = saved.get("count", 0)
    except (OSError, ValueError):
        count = 0
    count += 1
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"count": count, "ts": now}, handle)
    except OSError:
        pass
    return count


def clear_block(file_path):
    try:
        os.remove(state_path(file_path))
    except OSError:
        pass


def main() -> int:
    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0  # never block on a hook failure

    tool_input = data.get("tool_input") or {}
    file_path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""

    if any(marker in file_path for marker in SKIP_PATH_MARKERS):
        return 0

    content = new_content(tool_input)
    if not content:
        return 0

    found = find_forbidden(content, load_allowlist())
    if not found:
        clear_block(file_path)
        return 0

    count = record_block(file_path)
    words = ", ".join(f'"{w}"' for w in found)
    message = (
        "Writing-style check failed. Forbidden word(s) in the content: "
        f"{words}.\n"
        "See ~/.claude/rules/writing-style.md for the plain-word replacement. "
        "Reword and write the file again."
    )
    if count >= LOOP_THRESHOLD:
        message += (
            f"\n\nThis file has been blocked {count} times in a row. Stop "
            "rewriting. Tell the user which word is blocking and that the "
            "original wording may be correct, then ask how to proceed: reword, "
            "add an allowing phrase to "
            "~/.claude/scripts/writing-style/allowlist.txt, or skip the file."
        )
    print(message, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
