#!/usr/bin/env python3
"""Check files changed on disk against the writing-style word list.

check-forbidden-words.py only sees what a Write or Edit call passes as its input.
A Bash command, an MCP server or a subagent writes the file directly, so nothing
scannable reaches that hook. This script reads the files instead.

Two modes, both reading the hook input JSON from stdin:

    --mode=bash   PostToolUse on Bash. Runs after every Bash call.
    --mode=stop   Stop and SubagentStop. The last check before the turn ends.

Both find the changed text with git, so the cost is one lstat per tracked file --
git compares the stat data cached in the index and opens only the files whose stat
differs. Unchanged files are never read. Outside a git repository the script does
nothing.

Only added lines are scanned, and only violations not yet reported in this session.
Both hooks exit 2 with the message on stderr, which shows it to Claude. The write
has already happened by then, so the file is fixed afterwards rather than blocked.

The word list, the allowlist and the skipped paths all come from
check-forbidden-words.py, so the two hooks stay in step.
"""

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile

# Importing the checker by path would otherwise leave a __pycache__ directory next
# to it.
sys.dont_write_bytecode = True

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# Files whose added lines are not prose worth checking.
SKIP_SUFFIXES = (
    ".lock", ".min.js", ".min.css", ".map", ".svg", ".snap",
    ".png", ".jpg", ".jpeg", ".gif", ".pdf", ".ico", ".woff", ".woff2",
)
SKIP_NAMES = (
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "Cargo.lock", "go.sum", "Gemfile.lock", "composer.lock",
)

# A command that rewrites a generated or data file hands us the whole file as added
# lines. Past this many, the file is left alone and named in the message.
MAX_ADDED_LINES = 400
MAX_UNTRACKED_BYTES = 256_000
MAX_UNTRACKED_FILES = 50
MAX_REPORTED = 20


def load_checker():
    """The check-forbidden-words module, imported by path.

    Its file name has dashes, so a plain import statement does not reach it.
    """
    path = os.path.join(SCRIPT_DIR, "check-forbidden-words.py")
    spec = importlib.util.spec_from_file_location("check_forbidden_words", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(root, *args):
    """Run a git command in root and return its stdout, or None if it fails."""
    try:
        result = subprocess.run(
            ["git", "-c", "core.quotepath=false", "-C", root, *args],
            capture_output=True, timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.decode("utf-8", errors="replace")


def repo_root(cwd):
    output = git(cwd, "rev-parse", "--show-toplevel")
    return output.strip() if output else None


def skipped(path, skip_markers):
    name = os.path.basename(path)
    return (
        any(marker in path for marker in skip_markers)
        or name in SKIP_NAMES
        or path.endswith(SKIP_SUFFIXES)
    )


def parse_diff(text):
    """Yield (path, line_number, line_text) for every added line in a -U0 diff."""
    path = None
    line_number = 0
    for line in text.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            path = None if target == "/dev/null" else target
        elif line.startswith("--- "):
            continue
        elif line.startswith("@@"):
            # "@@ -old,count +new,count @@" -- the added lines start at new.
            try:
                new_part = line.split("+", 1)[1].split(" ", 1)[0]
                line_number = int(new_part.split(",")[0])
            except (IndexError, ValueError):
                line_number = 0
        elif line.startswith("+") and path:
            yield path, line_number, line[1:]
            line_number += 1


def tracked_changes(root):
    """Added lines in tracked files, staged or not."""
    common = ["diff", "-U0", "--no-color", "--no-prefix", "--no-renames",
              "--diff-filter=ACMR"]
    text = git(root, *common, "HEAD", "--")
    if text is None:
        # A repository with no commits has no HEAD to compare against.
        text = (git(root, *common) or "") + (git(root, *common, "--cached") or "")
    return parse_diff(text)


def untracked_lines(root, skip_markers):
    """Added lines in new files git does not track yet."""
    output = git(root, "ls-files", "-o", "--exclude-standard", "-z")
    if not output:
        return
    paths = [p for p in output.split("\0") if p]
    for path in paths[:MAX_UNTRACKED_FILES]:
        if skipped(path, skip_markers):
            continue
        full = os.path.join(root, path)
        try:
            if os.path.getsize(full) > MAX_UNTRACKED_BYTES:
                continue
            with open(full, "rb") as handle:
                raw = handle.read()
        except OSError:
            continue
        if b"\0" in raw:
            continue
        text = raw.decode("utf-8", errors="replace")
        for number, line in enumerate(text.splitlines(), start=1):
            yield path, number, line


def state_path(session_id):
    directory = os.path.join(tempfile.gettempdir(), "claude-writing-style")
    name = hashlib.sha1((session_id or "none").encode("utf-8")).hexdigest()
    return os.path.join(directory, "reported-" + name + ".json")


def load_reported(session_id):
    try:
        with open(state_path(session_id), encoding="utf-8") as handle:
            return set(json.load(handle))
    except (OSError, ValueError, TypeError):
        return set()


def save_reported(session_id, keys):
    path = state_path(session_id)
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(sorted(keys), handle)
    except OSError:
        pass


def scan(root, checker):
    """Return the violations in the changed lines, and the files left unscanned."""
    phrases = checker.load_allowlist()
    skip_markers = checker.SKIP_PATH_MARKERS
    counts = {}
    oversized = set()
    found = []
    lines = list(tracked_changes(root)) + list(untracked_lines(root, skip_markers))
    for path, number, text in lines:
        if skipped(path, skip_markers):
            continue
        counts[path] = counts.get(path, 0) + 1
        if counts[path] > MAX_ADDED_LINES:
            oversized.add(path)
            continue
        for word in checker.find_forbidden(text, phrases):
            found.append((path, number, word, text.strip()))
    return found, sorted(oversized)


def main() -> int:
    mode = "bash"
    for argument in sys.argv[1:]:
        if argument.startswith("--mode="):
            mode = argument.split("=", 1)[1]

    try:
        data = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        data = {}

    # A Stop hook that blocks is itself followed by another Stop. Run once.
    if mode == "stop" and data.get("stop_hook_active"):
        return 0

    root = repo_root(data.get("cwd") or os.getcwd())
    if not root:
        return 0

    try:
        checker = load_checker()
        found, oversized = scan(root, checker)
    except Exception:  # never block on a hook failure
        return 0
    if not found:
        return 0

    session_id = data.get("session_id")
    reported = load_reported(session_id)
    fresh = []
    for path, number, word, text in found:
        key = "|".join([
            path,
            word.lower(),
            hashlib.sha1(text.encode("utf-8")).hexdigest()[:12],
        ])
        if key not in reported:
            reported.add(key)
            fresh.append((path, number, word, text))
    if not fresh:
        return 0
    save_reported(session_id, reported)

    header = (
        "Writing-style check failed. These changed lines were written without going "
        "through Write or Edit, so the usual check did not see them."
    )
    if mode == "stop":
        header = (
            "Writing-style check failed. The turn cannot end with forbidden words in "
            "the changed lines."
        )
    lines = [header, ""]
    for path, number, word, text in fresh[:MAX_REPORTED]:
        lines.append(f'  {path}:{number}  "{word}"  {text[:100]}')
    if len(fresh) > MAX_REPORTED:
        lines.append(f"  ... and {len(fresh) - MAX_REPORTED} more")
    if oversized:
        lines.append("")
        lines.append(
            "Not scanned, over "
            f"{MAX_ADDED_LINES} added lines: {', '.join(oversized)}"
        )
    lines.append("")
    lines.append(
        "See ~/.claude/rules/writing-style.md for the plain-word replacement. "
        "Fix each line above. If a word is genuinely correct, tell the user and ask "
        "whether to add the phrase to "
        "~/.claude/scripts/writing-style/allowlist.txt."
    )
    print("\n".join(lines), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
