#!/usr/bin/env python3
"""Stop hook: judge changed prose against the writing-style rules.

The word list in check-forbidden-words.py is a regex, so it catches banned words
and nothing else. The rules also ask for short sentences, one idea per sentence,
no hedging, complete sentences, and a comment that describes the thing itself
rather than its caller. No regex can check those. A small model reading the rules
and the changed lines can.

Two kinds of prose are sent:

- Added lines in documentation files (.md, .txt and the like).
- Changed comment and docstring lines in code files, with a few lines of the code
  around them. A KDoc cannot be judged on its own: the rules ask whether it
  describes the thing itself, and that needs the declaration below it. Plain code
  lines are marked as context and are never judged.

The model runs as a separate `claude -p` process, so it starts with an empty
context and reads only the rules and the changed lines. That is the point: the
judge keeps to the rules in full, unlike a long session.

It runs once when the turn ends, not once per write. Register it on Stop with
"async": true and "asyncRewake": true so the turn is not held up: the check runs
in the background and wakes Claude with the findings when it finds something.

Off by default, because it costs a model call per turn. Switch it on in the "env"
block of ~/.claude/settings.json:

    "env": { "CLAUDE_WRITING_STYLE_LLM": "1" }

Claude Code puts that block in the environment of every hook it runs, and
claude-box copies the host settings.json into each box, so one entry covers the
host and every box. An `export` in your shell reaches a host session only: a box
is started by `docker compose run`, which passes on nothing from your shell.

Other settings, all optional and read the same way:

    CLAUDE_WRITING_STYLE_MODEL          model for the judge (default "haiku")
    CLAUDE_WRITING_STYLE_LLM_TIMEOUT    seconds for the call (default 240)
    CLAUDE_WRITING_STYLE_RULES          path to the rules file
    CLAUDE_WRITING_STYLE_NO_COMMENTS    set to 1 to check documentation only

`claude -p` runs with CLAUDE_WRITING_STYLE_CHILD=1 in its environment, and this
script stops at once when that variable is set. Without it the child's own Stop
hook would start another judge, and so on.
"""

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

sys.dont_write_bytecode = True

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

RULES = os.environ.get("CLAUDE_WRITING_STYLE_RULES") or os.path.join(
    os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude"),
    "rules", "writing-style.md",
)

MODEL = os.environ.get("CLAUDE_WRITING_STYLE_MODEL") or "haiku"
# A cold `claude -p` start plus the call took 50 to 155 seconds in testing.
TIMEOUT = int(os.environ.get("CLAUDE_WRITING_STYLE_LLM_TIMEOUT") or 240)
CHECK_COMMENTS = os.environ.get("CLAUDE_WRITING_STYLE_NO_COMMENTS") != "1"

# Files that are prose from the first character.
PROSE_SUFFIXES = (".md", ".mdx", ".markdown", ".txt", ".rst", ".adoc")

# How a comment is written, per file suffix. Add a suffix here to cover another
# language. "hash" is a line comment only, "python" adds the docstring, and
# "cstyle" is // plus a /* */ block.
COMMENT_STYLE = {
    ".kt": "cstyle", ".kts": "cstyle", ".java": "cstyle",
    ".ts": "cstyle", ".tsx": "cstyle", ".js": "cstyle", ".mjs": "cstyle",
    ".py": "python",
    ".sh": "hash", ".bash": "hash", ".zsh": "hash",
    ".yml": "hash", ".yaml": "hash", ".toml": "hash",
}

# Comment lines that are an instruction to a tool, not prose.
DIRECTIVES = re.compile(
    r"^(?:#!|#\s*(?:type:|noqa|nosec|pylint|ruff|mypy|fmt:|isort:|shellcheck|"
    r"-\*-|coding[:=])|//\s*(?:@formatter|noinspection|eslint|prettier|ts-))",
    re.IGNORECASE,
)

# Enough to cover a normal turn's writing, short enough to keep the call cheap.
MAX_LINES = 120
MAX_COMMENT_LINES = 90
# Per file as well, so one heavily commented file leaves room for the others.
MAX_COMMENT_LINES_PER_FILE = 30
MAX_LINE_CHARS = 300
MAX_FILE_BYTES = 400_000
MAX_REPORTED = 15

# Lines of code shown around a changed comment. A KDoc sits above the thing it
# describes, so what follows matters more than what comes before.
CONTEXT_BEFORE = 1
CONTEXT_AFTER = 3

# Findings allowed on one line in a session. Past this the line is left out of
# the request, so a rewording cannot come back under a new name.
LINE_LIMIT = 2

# Two changed comment lines this close together are shown as one block.
BLOCK_GAP = 2

JUDGE = (
    "You check written English against a fixed style guide. You answer with JSON "
    "and nothing else. You are strict about the rules you are given and silent "
    "about everything else."
)

INSTRUCTIONS = """\
Below are the style rules, then the prose a colleague just wrote.

Report only clear breaches of these rules. Say nothing about taste, structure,
accuracy or anything the rules do not mention. Ignore code, identifiers, URLs,
file paths, table markup and text that is quoting someone else. A line you are
unsure about is not a breach.

Two limits on what counts. A metaphor is a breach only when a reader could take
it the wrong way; an ordinary verb used of a thing is plain English, so "a rule
is far from the end", "the value goes into the column" and "the check runs" are
all fine. And report each line once, with the single clearest breach: never list
one line twice under two names for the same fault.

The prose comes in two sections. "Added documentation lines" are plain lines from
a documentation file. "Changed comments in code" show a comment or a docstring
with the code around it: a line whose number is followed by `+` is a changed
comment line to judge, and a line without `+` is code shown for context only.
Never report a context line. Use the context to judge whether a comment describes
the thing itself, which the rules ask for, and report the comment line when it
does not.

Answer with a JSON array, one object per breach:

[{"path": "<path>", "line": <number>, "rule": "<the rule, in a few words>",
  "fix": "<the line rewritten>"}]

Answer with [] when the prose keeps the rules. Output the JSON array only.
"""


def load_scanner():
    """The scan-changed-files module, imported by path.

    Its file name has dashes, so a plain import statement does not reach it.
    """
    path = os.path.join(SCRIPT_DIR, "scan-changed-files.py")
    spec = importlib.util.spec_from_file_location("scan_changed_files", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def state_path(session_id):
    directory = os.path.join(tempfile.gettempdir(), "claude-writing-style")
    name = hashlib.sha1((session_id or "none").encode("utf-8")).hexdigest()
    return os.path.join(directory, "prose-" + name + ".json")


def load_reported(session_id):
    """What this session has already reported.

    Returns the finding keys and the count of findings per line. An older state
    file holds the keys as a plain list, which still loads.
    """
    try:
        with open(state_path(session_id), encoding="utf-8") as handle:
            saved = json.load(handle)
    except (OSError, ValueError, TypeError):
        return set(), {}
    if isinstance(saved, list):
        return set(saved), {}
    keys = set(saved.get("keys") or [])
    counts = saved.get("lines")
    return keys, dict(counts) if isinstance(counts, dict) else {}


def save_reported(session_id, keys, counts):
    path = state_path(session_id)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump({"keys": sorted(keys), "lines": counts}, handle)
    except OSError:
        pass


def line_key(path, number):
    return f"{path}:{number}"


def capped(counts, path, number):
    """True when this line has been reported as often as it may be.

    Rewording a line changes what the judge calls the rule, so the same line
    comes back under a new name and the rewriting never ends. After LINE_LIMIT
    findings the line is left out of the request.
    """
    return counts.get(line_key(path, number), 0) >= LINE_LIMIT


def changed_lines(scanner, root):
    """Every added line in the working tree, as (path, number, text)."""
    skip_markers = scanner.load_checker().SKIP_PATH_MARKERS
    lines = list(scanner.tracked_changes(root))
    lines += list(scanner.untracked_lines(root, skip_markers))
    return [item for item in lines if not scanner.skipped(item[0], skip_markers)]


def documentation_lines(lines, counts):
    """The added lines that are in a documentation file."""
    kept = []
    for path, number, text in lines:
        if not path.lower().endswith(PROSE_SUFFIXES):
            continue
        if text.strip() and not capped(counts, path, number):
            kept.append((path, number, text[:MAX_LINE_CHARS]))
    return kept[:MAX_LINES]


def read_file(root, path):
    """The file's lines, or None when it cannot be read as text."""
    full = os.path.join(root, path)
    try:
        if os.path.getsize(full) > MAX_FILE_BYTES:
            return None
        with open(full, "rb") as handle:
            raw = handle.read()
    except OSError:
        return None
    if b"\0" in raw:
        return None
    return raw.decode("utf-8", errors="replace").splitlines()


def python_prose_lines(lines):
    """The line numbers that hold a `#` comment or a docstring.

    A docstring is a triple-quoted string that starts a line, with an optional
    r, b, f or u prefix. A triple-quoted string assigned to a name is data, not
    prose, so its lines are left out — but its quotes are still paired, because
    a mark that is counted twice or not at all makes every later line read as a
    docstring.
    """
    marks = ('"""', "'''")
    numbers = set()
    quote = None        # the triple-quote mark that is still open
    in_doc = False      # the open string began as a docstring

    for number, line in enumerate(lines, start=1):
        indent = len(line) - len(line.lstrip())
        line_in_doc = in_doc
        opened_doc = False

        index = 0
        while index < len(line):
            if quote:
                end = line.find(quote, index)
                if end < 0:
                    break
                index = end + len(quote)
                quote = None
                in_doc = False
                continue
            found = [(line.find(mark, index), mark) for mark in marks]
            found = [(at, mark) for at, mark in found if at >= 0]
            if not found:
                break
            at, mark = min(found)
            quote = mark
            in_doc = line[indent:at].strip("rRbBfFuU") == ""
            opened_doc = opened_doc or in_doc
            index = at + len(mark)

        text = line.strip()
        if line_in_doc or opened_doc:
            numbers.add(number)
        elif not quote and text.startswith("#") and not DIRECTIVES.match(text):
            numbers.add(number)
    return numbers


def comment_line_numbers(lines, style):
    """The line numbers that hold a comment or a docstring.

    A light scan, not a parser: a comment marker inside a string literal counts
    as a comment, and a comment after code on the same line does not count at
    all. It leaves a line out rather than include it, so code is never judged as
    prose.
    """
    if style == "python":
        return python_prose_lines(lines)

    numbers = set()
    in_block = False
    for number, line in enumerate(lines, start=1):
        text = line.strip()

        if style == "hash":
            if text.startswith("#") and not DIRECTIVES.match(text):
                numbers.add(number)
            continue

        # cstyle
        if in_block:
            numbers.add(number)
            if "*/" in text:
                in_block = False
            continue
        if text.startswith("/*"):
            numbers.add(number)
            if "*/" not in text[2:]:
                in_block = True
            continue
        if text.startswith("//") and not DIRECTIVES.match(text):
            numbers.add(number)
    return numbers


def comment_blocks(root, lines, counts):
    """The changed comments in code files, with the code around them.

    Each block is (path, [(line number, text, is a changed comment line)]).
    """
    added = {}
    for path, number, _ in lines:
        if os.path.splitext(path)[1].lower() not in COMMENT_STYLE:
            continue
        if not capped(counts, path, number):
            added.setdefault(path, set()).add(number)

    blocks = []
    budget = MAX_COMMENT_LINES
    for path in sorted(added):
        if budget <= 0:
            break
        source = read_file(root, path)
        if source is None:
            continue
        style = COMMENT_STYLE[os.path.splitext(path)[1].lower()]
        changed = sorted(added[path] & comment_line_numbers(source, style))
        if not changed:
            continue

        # Group the changed comment lines that sit together.
        groups = [[changed[0]]]
        for number in changed[1:]:
            if number - groups[-1][-1] <= BLOCK_GAP:
                groups[-1].append(number)
            else:
                groups.append([number])

        for_file = min(budget, MAX_COMMENT_LINES_PER_FILE)
        for group in groups:
            if for_file <= 0:
                break
            first = max(1, group[0] - CONTEXT_BEFORE)
            last = min(len(source), group[-1] + CONTEXT_AFTER)
            shown = []
            for number in range(first, last + 1):
                if len(shown) >= for_file:
                    break
                text = source[number - 1][:MAX_LINE_CHARS]
                if text.strip():
                    shown.append((number, text, number in group))
            if shown:
                blocks.append((path, shown))
                for_file -= len(shown)
                budget -= len(shown)
    return blocks


def build_prompt(rules, docs, blocks):
    """The whole request for the judge."""
    parts = [INSTRUCTIONS, "--- style rules ---", rules, ""]

    if docs:
        parts.append("--- added documentation lines ---")
        parts += [f"{path}:{number}: {text}" for path, number, text in docs]
        parts.append("")

    if blocks:
        parts.append("--- changed comments in code ---")
        for path, shown in blocks:
            parts.append(f"{path}:")
            for number, text, is_comment in shown:
                parts.append(f"  {number}{'+' if is_comment else ' '} {text}")
            parts.append("")

    return "\n".join(parts)


def ask_model(prompt):
    """The judge's findings, or None when the call did not produce any JSON."""
    if not shutil.which("claude"):
        return None

    environment = dict(os.environ)
    environment["CLAUDE_WRITING_STYLE_CHILD"] = "1"

    try:
        result = subprocess.run(
            [
                "claude", "-p",
                "--model", MODEL,
                "--output-format", "text",
                # No hooks, no CLAUDE.md, no rules, no skills. The judge reads
                # the prompt below and nothing else, and its own Stop hook
                # cannot start a second judge. Auth and model choice still work.
                "--safe-mode",
                "--no-session-persistence",
                "--strict-mcp-config",
                "--disallowed-tools", "Bash", "Edit", "Write", "Read", "Task",
                "--system-prompt", JUDGE,
            ],
            input=prompt.encode("utf-8"),
            capture_output=True,
            timeout=TIMEOUT,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None

    answer = result.stdout.decode("utf-8", errors="replace")
    match = re.search(r"\[.*\]", answer, re.DOTALL)
    if not match:
        return None
    try:
        found = json.loads(match.group(0))
    except ValueError:
        return None
    if not isinstance(found, list):
        return None
    return [item for item in found if isinstance(item, dict)]


def main() -> int:
    if os.environ.get("CLAUDE_WRITING_STYLE_CHILD"):
        return 0
    if os.environ.get("CLAUDE_WRITING_STYLE_LLM") != "1":
        return 0

    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        data = {}

    # A Stop hook that blocks is followed by another Stop. Run once.
    if data.get("stop_hook_active"):
        return 0

    try:
        with open(RULES, encoding="utf-8") as handle:
            rules = handle.read().strip()
    except OSError:
        return 0
    if not rules:
        return 0

    scanner = load_scanner()
    root = scanner.repo_root(data.get("cwd") or os.getcwd())
    if not root:
        return 0

    session_id = data.get("session_id")
    reported, counts = load_reported(session_id)

    lines = changed_lines(scanner, root)
    docs = documentation_lines(lines, counts)
    blocks = comment_blocks(root, lines, counts) if CHECK_COMMENTS else []
    if not docs and not blocks:
        return 0

    found = ask_model(build_prompt(rules, docs, blocks))
    if not found:
        return 0

    fresh = []
    for item in found:
        path = str(item.get("path") or "")
        number = item.get("line")
        rule = str(item.get("rule") or "").strip()
        fix = str(item.get("fix") or "").strip()
        if not path or not rule:
            continue
        key = "|".join([path, str(number), rule.lower()[:60]])
        if key in reported or capped(counts, path, number):
            continue
        reported.add(key)
        counts[line_key(path, number)] = counts.get(line_key(path, number), 0) + 1
        fresh.append((path, number, rule, fix))
    if not fresh:
        return 0
    save_reported(session_id, reported, counts)

    report = [
        "Writing-style check failed. A reader of "
        "~/.claude/rules/writing-style.md found these breaches in the prose you "
        "changed:",
        "",
    ]
    for path, number, rule, fix in fresh[:MAX_REPORTED]:
        report.append(f"  {path}:{number}  {rule}")
        if fix:
            report.append(f"      suggestion: {fix}")
    if len(fresh) > MAX_REPORTED:
        report.append(f"  ... and {len(fresh) - MAX_REPORTED} more")
    report.append("")
    report.append(
        "Fix each line, or tell the user which finding you disagree with and why."
    )
    print("\n".join(report), file=sys.stderr)
    return 2


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # a hook failure must not stop the turn
        sys.exit(0)
