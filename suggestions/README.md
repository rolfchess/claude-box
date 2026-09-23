# Rules and hooks

The rules in `rules/` and the hooks in `scripts/` that enforce them. Install them
with `../install-defaults.sh` — see
[the main README](../README.md#shared-defaults-rules-and-hooks) for what that
does and how a box picks them up.

## The word list

The words to avoid are in `scripts/writing-style/words.txt`, and nowhere else.
One entry per line, four fields separated by `" | "`:

```
commence, commences | commenc(?:e|es|ed|ing) | "start", "begin" | Use plain English.
verbose | | "long", "wordy" |
```

The words as a reader sees them, a regex covering every form (empty means the
first word is the regex), the replacement, and an optional note. A line starting
with `## ` starts a group, whose text becomes a sub-heading in the rules file.
Those two entries are an example; neither word is on the real list.

A group heading takes marks after the heading, in any order:

| Mark | Default | What it does |
| ---- | ------- | ------------ |
| `word` | yes | The regex must match a whole word |
| `abbreviation` | | For a word ending in a full stop, where a closing word boundary does not fit |
| `teach` | yes | The group is written into `rules/writing-style.md` |
| `hook-only` | | The group is left out of `rules/writing-style.md` |

```
## Abbreviations | abbreviation
## Latin | hook-only
```

Every group is blocked, marked either way. `hook-only` decides only whether the
model is told in advance. 48 of the 84 entries are taught and 36 are left to the
hook — Latin, idioms and jargon nouns, which a model hardly ever writes. Writing
those into the rules would cost 359 tokens in every session and every box to
prevent something that almost never happens, and the hook still catches it with
a named replacement when it does.

Every hook builds its regexes from that file, and every message names the
replacement:

```
Writing-style check failed. Forbidden word(s) in the content, with the replacement to use:
  "commences" -> "start", "begin"
  "verbose" -> "long", "wordy"
Reword and write the file again.
```

Naming the replacement is what keeps a block from turning into three. A message
that only points at the rules file leaves Claude to guess, and a guess is another
round trip.

`render-words.py` writes the same list into the "Words to avoid" section of
`rules/writing-style.md`, between two markers, and `install-defaults.sh` runs it
after it copies the rules. So the list Claude reads and the list the hook applies
are one list. `render-words.py --check` reports a line the hook cannot read, and
the installer stops when it does — a `words.txt` with a broken regex would
otherwise stop blocking without a word of warning.

## The word-list checks

`check-forbidden-words.py` reads the text a tool call would put in the file, so a
banned word stops the write and Claude rewords before anything reaches disk. It
only sees what a call passes as its input. A `sed` command, a heredoc, an MCP
server or a subagent writes the file directly, which is how a banned word used to
slip through. `scan-changed-files.py` catches those: it finds the changed lines
with `git diff` and matches them against the same word list. The write has
already happened, so it names the file and line and Claude fixes it afterwards.

It reads changes, not the tree: git opens only the files whose cached stat data
differs, and only added lines are scanned. On a 4000-file, 2.8 GB repository the
check takes about 0.35 seconds, nearly all of it process startup. A violation is
reported once per session, so an already dirty working tree is not re-reported on
every `Bash` call.

Three things it does not check:

- **Work outside a git repository.** The scan needs `git diff`, so it does
  nothing elsewhere, including `~/.claude` itself.
- **Files you edit yourself.** Hooks only see what Claude does.
- **Generated files.** A file with more than 400 added lines is named in the
  message and left unscanned, so a rebuilt data file does not bury the report.

## Review comments on a merge request

A review comment is prose, and the rules apply to it. Neither word-list hook saw
one, because a note goes out through `glab` or `post-draft.py` and never reaches
a file. `review-notes.py` takes the note text out of the command and matches it
against the same word list, in three shapes:

| Command | Where the text is |
| ------- | ----------------- |
| `glab api .../draft_notes\|notes\|discussions` | the heredoc body |
| `post-draft.py general\|file\|reply` | the heredoc body |
| `glab mr note\|comment` | the value of `-m` or `--message` |

A `GET` on `.../notes` has no body, so it passes. A heredoc in an unrelated
command is not read, because the command must name one of the three above. The
hook cannot see a body read from a file or held in a shell variable. Blocks are
counted per merge request: after three, the message tells Claude to stop
rewriting and ask you.

## One process per Bash call

`bash/check-bash-command.py` is the only hook registered on `Bash`. It reads the
command once, splits the heredoc bodies off it once, and calls
`git/commit-message.py` and `writing-style/review-notes.py` with the result. The
first one with something to say ends it: the hook exits 2 with its message.

Both checks used to be hooks in their own right. Each of them returns at once
when its regex does not match the command, so the work was never the cost — a
cold `python3` is about 110 ms, and every `Bash` call started one twice. One
process saves about 150 ms per call. A check whose file is missing is skipped, so a
partial install still runs the other one.

## No credit to Claude in a commit

A commit belongs to you. Claude's own instructions ask it to add a
`Co-Authored-By: Claude` trailer to every commit message, and a "Generated with
Claude Code" line to every pull request body, so it adds them back however often
you say not to. `rules/git-commits.md` forbids both, and
`git/commit-message.py` enforces it: a commit, a pull request or a merge
request whose message names Claude or Anthropic is blocked, and the message has
to be sent again without the line.

Which command writes a message is decided on the command with its heredoc bodies
removed, and those bodies are then searched for the forbidden line. So a message
passed on standard input is read, while a heredoc that only writes *about* a
trailer, such as this README, is not. The line also has to start its own line, so
a sentence that names a trailer is left alone. A read such as
`git log --grep` never matches, because only an option may stand between `git`
and `commit`.

The hook cannot see a message held in a shell variable, written by another
process, or typed into the editor. A git `commit-msg` hook catches those, because
git runs it whatever wrote the message. Nothing here installs one, because it
would apply to your own commits in every repository too.

## Keeping the rules where the model reads them

The rules are part of every request and nothing removes them, not even
compaction. What fades is attention: the further a rule is from the end of the
context, the less it shapes the writing. After a hundred thousand tokens of code
and tool output the rules are ignored while they are still in the request.

`inject-rules.py` prints them again at the end of the context, on four events:

- **`UserPromptSubmit`** — every message you send, so the rules land right after
  your prompt.
- **`PostToolBatch`** — every fifteenth batch of tool calls, and after three when
  the batch wrote a `.md` file. This is the only place to print them in a long
  run with no message from you. `CLAUDE_WRITING_STYLE_EVERY_N` changes the
  interval.
- **`PreToolUse`** on `Write`, `Edit`, `MultiEdit` and `NotebookEdit` — see
  below.
- **`PreCompact`** — asks the compactor to keep the rules in the summary, word
  for word.

### Before an edit

Claude writes the text of an edit before any hook runs. A print that comes with
the edit, or after it, reaches Claude too late for that edit. It helps only the
next one. After a long stretch of reading code, the last print is far back, and
the first edit falls back to old habits.

So the `PreToolUse` mode refuses an edit when the rules were last printed five or
more tool batches ago. The refusal contains the rules, and Claude makes the same
edit again with the rules just read. The print resets the count, so the next
edits pass. The cost is one rewritten edit after each long stretch without a
print. `CLAUDE_WRITING_STYLE_WRITE_GAP` changes the five, and `0` turns the check
off. An edit made with `sed` or a heredoc in `Bash` is not checked here.

### Comments only after code

The "Comments" section applies only to code. It is left out of the print until
the session edits a file that is not documentation, and it is printed from then
on. A session that only writes chat and documentation saves 554 characters per
print, about a quarter. The section stays in `rules/writing-style.md`, so it is
always part of the instructions, and `check-prose-style.py` still sends it to
the judge.

The word list is left out of what is printed. A regex blocks every word in it
before the write lands, and the block message names the replacement, so
repeating the list mid-session adds nothing. What is printed is the part no regex
can check, plus one line saying a hook holds the list. That is 544 tokens per
print, against 745 for the whole file.

The taught words stay in `rules/writing-style.md`, which is part of the
instructions. They are what stops a banned word from being written in the first
place. A rules-file token is sent once and read from cache after that, while an
injected token is sent once per print and then stays in the context for every
later request. So one token in the rules file costs much less than one token
printed forty times, and the rules file is the right place for the list.

The trade is a bigger instruction block against a smaller print. The rules file
went from 687 to 1162 tokens, and each print dropped by 201, so the two designs
cost the same at 2.4 user turns and the new one is cheaper past that: 24% less
at 40 turns.

## The model-backed check

A regex checks words. It cannot check short sentences, one idea per sentence,
hedging, a dropped subject, or whether a comment describes the thing itself.
`check-prose-style.py` sends the changed prose and the rules to a small model and
reports what comes back. It sends the rules without the word list, for the same
reason: that list is enforced before the write lands, so sending it would only
add a second report of the same word. It is off until you add this to
`~/.claude/settings.json`:

```json
"env": { "CLAUDE_WRITING_STYLE_LLM": "1" }
```

Claude Code puts that block in the environment of every hook, and `claude-box`
copies the host `settings.json` into each box (`claude-box:307`), so one entry
covers the host and every box. An `export` in your shell reaches a host session
only: a box is started by `docker compose run`, which passes on nothing from your
shell. Add `CLAUDE_WRITING_STYLE_LLM: ${CLAUDE_WRITING_STYLE_LLM:-}` to the
`environment:` block in `docker-compose.yml` if you would rather switch it on per
box from the shell. The same holds for every variable below.

| Variable | Default | What it does |
| -------- | ------- | ------------ |
| `CLAUDE_WRITING_STYLE_LLM` | off | `1` turns the check on |
| `CLAUDE_WRITING_STYLE_MODEL` | `haiku` | The model that judges |
| `CLAUDE_WRITING_STYLE_LLM_TIMEOUT` | `240` | Seconds before it gives up |
| `CLAUDE_WRITING_STYLE_NO_COMMENTS` | off | `1` checks documentation files only |

It runs when the turn ends, once per turn, over two kinds of prose: the added
lines in `.md`, `.txt` and similar files, and the changed comments and docstrings
in code files. It knows `//` and `/* */` for Kotlin, Java and TypeScript, `#` for
Python, Bash, YAML and TOML, and the Python docstring; `COMMENT_STYLE` in the
script maps a suffix to one of those three. Left out: a shebang, a tool directive
such as `# shellcheck` or `# type:`, and a triple-quoted string assigned to a
name, which is data. At most 120 documentation lines and 90 comment lines are
sent, 30 of them per file, so one heavily commented file leaves room for the
others.

A comment is sent with one line above it and three below, marked as context: the
rules ask whether a comment describes the thing itself rather than its caller,
and that cannot be judged without seeing the declaration under it. The judge is
told never to report a context line, to report each line once with the clearest
breach, and to count a metaphor only where a reader could take it the wrong way.
A line is reported at most twice per session (`LINE_LIMIT`); without that limit,
rewording a line brought it back under a new rule name and the rewriting never
ended.

The judge is a separate `claude -p --safe-mode` process, so it starts empty: no
hooks, no `CLAUDE.md`, no rules of its own, and no way to start a second judge.
A fresh reader keeps to the rules in full, which is the whole reason to run it
outside the session. Its hook entry sets `"async": true` and
`"asyncRewake": true`, so the turn ends without waiting: the check runs in the
background, which took 50 to 155 seconds in testing, and wakes Claude with the
findings. Drop both fields if you would rather the turn wait.

## Living with the hooks

If a banned word is genuinely right, add the whole phrase (a clause or a
sentence, not a bare word) to `~/.claude/scripts/writing-style/allowlist.txt`.
Only text inside that phrase is exempt. After three blocks on the same file the
hook tells Claude to stop rewriting and ask you instead. Inside a box that file
is on a read-only mount, so edit it on the host, or drop the `:ro` from the
`scripts` mount in `claude-box`.

To change the word list, edit `scripts/writing-style/words.txt` and run
`../install-defaults.sh`. That is the only file to touch: the installer renders
the taught groups into `rules/writing-style.md` for you, and every hook reads the
same file. Mark a group `hook-only` to block its words without spending
instruction space on them. Edit `rules/writing-style.md` outside the two markers to change the rest of
the guidance.

The scripts follow on their own. `scan-changed-files.py` and `review-notes.py`
import the list, the allowlist and the skipped paths from
`check-forbidden-words.py`, and `inject-rules.py` and `check-prose-style.py` read
`rules/writing-style.md` at run time.
