#!/usr/bin/env python3
"""PreToolUse hook: block Write/Edit when the new content uses a forbidden word.

The words and their replacements come from words.txt next to this script, which
is the only place the list lives. When a word is found, the hook exits with code
2, which stops the tool call and shows the message below to Claude. The message
names the replacement, so the next attempt does not have to guess.

Read the hook input JSON from stdin and scan the text the call would put in the
file: `content` for Write, `new_string` for Edit, every `edits[].new_string` for
MultiEdit, and `new_source` for NotebookEdit. Files that legitimately quote the
forbidden words (the rules doc, this script, and memory files) are skipped.

Changes made any other way -- a Bash command, an MCP server, a subagent -- are not
visible here. scan-changed-files.py checks those against the same word list.

Without words.txt the hook blocks nothing, the same as any other failure here.

Escape routes for a word that is genuinely correct:
- Add the exact phrase (a whole clause or sentence) to allowlist.txt next to this
  script. Only text inside that phrase is exempt, so allowing one sentence does not
  un-ban the word everywhere else.
- After the same file is blocked several times in a row, the message tells Claude to
  stop and ask the user rather than keep rewriting.
"""

import collections
import hashlib
import json
import os
import re
import sys
import tempfile
import time

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
WORDS_FILE = os.path.join(SCRIPT_DIR, "words.txt")
ALLOWLIST_FILE = os.path.join(SCRIPT_DIR, "allowlist.txt")

# Paths that are allowed to contain the forbidden words as data or examples.
SKIP_PATH_MARKERS = ("writing-style", "check-forbidden-words", "/memory/")

# The section of writing-style.md that render-words.py owns.
SECTION_START = "<!-- words-to-avoid:start -->"
SECTION_END = "<!-- words-to-avoid:end -->"

# What replaces that section when the rules are printed again mid-session. The
# words themselves are left out: a regex already blocks every one of them before
# the write lands, and it names the replacement when it does.
SECTION_SUMMARY = (
    "## Words to avoid, and what to use instead\n\n"
    "A hook holds the list and blocks a write that uses one of the words, "
    "naming the plain replacement. Use no fancy or figurative verb where a "
    "plain one exists: a column, order, row or object does not \"carry\", "
    "\"pin\", \"hold onto\", \"own\" or \"travel with\" a value -- it "
    "\"has\" or \"stores\" one."
)

# Loop detection: after this many blocks on the same file within the window, the
# message tells Claude to stop rewriting and ask the user.
LOOP_THRESHOLD = 3
LOOP_WINDOW_SECONDS = 900

# A boundary style, per group, and how it wraps the group's alternation. "word"
# needs the match to be a whole word. "abbreviation" is for a word ending in a
# full stop, where a closing word boundary does not fit.
BOUNDARIES = {
    "word": (r"\b(?:", r")\b"),
    "abbreviation": (r"(?<![A-Za-z])(?:", r")"),
}

# Whether a group is written into the rules file. Every group is blocked either
# way; this decides only what the model is told in advance. "teach" is for words
# a model writes unprompted, where the reminder is worth its space in the
# instructions. "hook-only" is for words it hardly ever writes, where one block
# with a named replacement costs less than the space would.
TEACHING = ("teach", "hook-only")

Group = collections.namedtuple("Group", "heading boundary teach entries")

_groups = None
_compiled = None


def split_fields(line):
    """The fields of one line, split on " | ".

    A separator is a pipe with a space before it, and a space or the end of the
    line after it. An alternation inside a regex is written with no space around
    its pipe, so it stays whole. render-words.py --check reports a line whose
    regex does not compile.
    """
    return [field.strip() for field in re.split(r"(?<=\s)\|(?=\s|$)", line.strip())]


def load_words():
    """The word list, as a list of Group.

    Each entry in a group is (regex, words, replacement, note). An entry with no
    regex of its own uses its first word as the regex. The order of the groups
    and of the entries within them is kept, so the rules file reads in the order
    the file is written.
    """
    global _groups
    if _groups is not None:
        return _groups

    groups = []
    boundary = "word"
    teach = True
    entries = None
    try:
        with open(WORDS_FILE, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
    except OSError:
        _groups = []
        return _groups

    for line in lines:
        text = line.strip()
        if text.startswith("## "):
            parts = split_fields(text[3:])
            heading = parts[0]
            marks = [part for part in parts[1:] if part]
            boundary = next((m for m in marks if m in BOUNDARIES), "word")
            teach = next((m for m in marks if m in TEACHING), "teach") == "teach"
            entries = []
            groups.append(Group(heading, boundary, teach, entries))
            continue
        if not text or text.startswith("#"):
            continue

        fields = split_fields(text)
        fields += [""] * (4 - len(fields))
        words, regex, replacement, note = fields[:4]
        if not words:
            continue
        words = [word.strip() for word in words.split(",") if word.strip()]
        if not regex:
            regex = words[0]
        if entries is None:
            entries = []
            groups.append(Group("Words to avoid", boundary, teach, entries))
        entries.append((regex, words, replacement, note))

    _groups = groups
    return _groups


def compiled_patterns():
    """One regex per boundary style, plus the replacement for each alternative.

    Every alternative is a named group, so the match itself says which entry hit
    without a second pass over the text.
    """
    global _compiled
    if _compiled is not None:
        return _compiled

    by_boundary = {}
    replacements = {}
    index = 0
    for group in load_words():
        for regex, _, replacement, _note in group.entries:
            name = f"w{index}"
            index += 1
            replacements[name] = replacement
            by_boundary.setdefault(group.boundary, []).append(f"(?P<{name}>{regex})")

    patterns = []
    for boundary, alternatives in by_boundary.items():
        prefix, suffix = BOUNDARIES[boundary]
        pattern = prefix + "|".join(alternatives) + suffix
        patterns.append(re.compile(pattern, re.IGNORECASE))
    _compiled = (patterns, replacements)
    return _compiled


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
    """The forbidden words in the content, as [(word, replacement), ...].

    Each word is reported once, in the form it was written.
    """
    patterns, replacements = compiled_patterns()
    ranges = exempt_ranges(content, phrases)
    found = []
    seen = set()
    for pattern in patterns:
        for match in pattern.finditer(content):
            if any(match.start() >= a and match.end() <= b for a, b in ranges):
                continue
            word = match.group(0)
            low = word.lower()
            if low in seen:
                continue
            seen.add(low)
            name = match.lastgroup
            if name is None:
                name = next(
                    (key for key, value in match.groupdict().items() if value),
                    None,
                )
            found.append((word, replacements.get(name, "")))
    return found


def describe(found):
    """The found words and their replacements, one per line."""
    lines = []
    for word, replacement in found:
        if replacement:
            lines.append(f'  "{word}" -> {replacement}')
        else:
            lines.append(f'  "{word}"')
    return "\n".join(lines)


def rules_without_words(text):
    """The rules with the generated word list replaced by a short summary.

    The list is worth its space in the instructions, which are read once. It is
    not worth repeating mid-session, because the hook enforces it anyway.
    """
    start = text.find(SECTION_START)
    end = text.find(SECTION_END)
    if start < 0 or end < 0 or end < start:
        return text
    return text[:start] + SECTION_SUMMARY + text[end + len(SECTION_END):]


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
    message = (
        "Writing-style check failed. Forbidden word(s) in the content, with the "
        "replacement to use:\n"
        f"{describe(found)}\n"
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
    try:
        sys.exit(main())
    except Exception:  # a hook failure must not block the call
        sys.exit(0)
