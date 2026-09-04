#!/usr/bin/env python3
"""Render words.txt into the word list section of writing-style.md.

The words and their replacements are in words.txt only. This script writes the
groups marked "teach" into the rules file, between the markers

    <!-- words-to-avoid:start -->
    <!-- words-to-avoid:end -->

so the words the model is told about are the words the hook blocks. A group
marked "hook-only" is left out and the section says how many words that is: the
hook blocks those either way, and a reminder a model does not need costs space
in every session and every box. install-defaults.sh runs this after it copies
the rules, so editing words.txt and running the installer is all it takes.

    render-words.py --check            report a line the hook cannot read
    render-words.py --rules FILE       print FILE with the section replaced
    render-words.py --rules FILE --in-place    write it back

--check exits with code 1 when a line is wrong, so the installer can stop.
"""

import argparse
import importlib.util
import os
import re
import sys

sys.dont_write_bytecode = True

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

INTRO = (
    "Use the plain word on the right. A hook blocks a write that uses one of "
    "these and names the replacement, so a blocked write tells you what to "
    "write instead."
)

TAIL = (
    "- Any fancy or figurative verb where a plain one exists. A column, order, "
    "row or object does not \"carry\", \"pin\", \"hold onto\", \"own\" or "
    "\"travel with\" a value -- it \"has\" or \"stores\" one."
)


def load_checker():
    """The check-forbidden-words module, imported by path.

    Its file name has dashes, so a plain import statement does not reach it.
    """
    path = os.path.join(SCRIPT_DIR, "check-forbidden-words.py")
    spec = importlib.util.spec_from_file_location("check_forbidden_words", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def problems(checker):
    """Every reason the hook could not read words.txt, as a list of lines.

    Every group is checked, taught or not: a hook-only group still blocks.
    """
    found = []
    groups = checker.load_words()
    if not groups:
        return [f"{checker.WORDS_FILE} is missing or holds no entry"]
    for heading, _, _, entries in groups:
        if not entries:
            found.append(f'group "{heading}" holds no entry')
        for regex, words, replacement, _ in entries:
            name = ", ".join(words) or "(no word)"
            try:
                pattern = re.compile(regex)
            except re.error as error:
                found.append(f'{name}: the regex "{regex}" does not compile: {error}')
                continue
            if pattern.search(""):
                found.append(f'{name}: the regex "{regex}" matches an empty string')
            if not replacement:
                found.append(f"{name}: no replacement")
    try:
        checker.compiled_patterns()
    except re.error as error:
        found.append(f"the combined regex does not compile: {error}")
    return found


def line_for(words, replacement, note):
    """One bullet of the rendered list."""
    quoted = ", ".join(f'"{word}"' for word in words)
    text = f"- {quoted} -> {replacement}"
    if not text.endswith((".", "?", "!")):
        text += "."
    if note:
        text += f" {note}"
    return text


def section(checker):
    """The whole generated section, markers included.

    Only the groups marked "teach" are written out. The rest are counted in one
    closing line, so a reader knows the section is not the whole list.
    """
    groups = checker.load_words()
    parts = [
        checker.SECTION_START,
        "## Words to avoid, and what to use instead",
        "",
        INTRO,
        "",
        TAIL,
    ]
    for group in groups:
        if not group.teach:
            continue
        parts += ["", f"### {group.heading}", ""]
        for _, words, replacement, note in group.entries:
            parts.append(line_for(words, replacement, note))

    untaught = sum(len(g.entries) for g in groups if not g.teach)
    if untaught:
        parts += ["", (
            f"The hook holds {untaught} more words that are not written out "
            "here, because they hardly ever come up. It names the replacement "
            "for those too."
        )]
    parts.append(checker.SECTION_END)
    return "\n".join(parts)


def rendered(checker, rules_path):
    """The rules file with the generated section replaced."""
    with open(rules_path, encoding="utf-8") as handle:
        text = handle.read()
    start = text.find(checker.SECTION_START)
    end = text.find(checker.SECTION_END)
    if start < 0 or end < 0 or end < start:
        raise SystemExit(
            f"render-words: {rules_path} has no "
            f"{checker.SECTION_START} / {checker.SECTION_END} pair"
        )
    return text[:start] + section(checker) + text[end + len(checker.SECTION_END):]


def main() -> int:
    parser = argparse.ArgumentParser(add_help=True)
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--rules")
    parser.add_argument("--in-place", action="store_true")
    options = parser.parse_args()

    checker = load_checker()

    if options.check:
        found = problems(checker)
        if found:
            print("render-words: words.txt is not usable:", file=sys.stderr)
            for line in found:
                print(f"  {line}", file=sys.stderr)
            return 1
        groups = checker.load_words()
        entries = sum(len(group.entries) for group in groups)
        taught = sum(len(group.entries) for group in groups if group.teach)
        print(
            f"render-words: {entries} entries, all readable "
            f"({taught} written into the rules, {entries - taught} left to the hook)"
        )
        return 0

    if not options.rules:
        parser.error("give --check or --rules FILE")

    text = rendered(checker, options.rules)
    if options.in_place:
        with open(options.rules, "w", encoding="utf-8") as handle:
            handle.write(text)
        print(f"render-words: wrote the word list into {options.rules}")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
