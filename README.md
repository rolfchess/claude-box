# claude-box

Run Claude Code inside an isolated Docker container, with permission prompts
turned off (`--dangerously-skip-permissions`) but the blast radius limited to a
single mounted directory. Each mounted directory gets its **own memory**.

## What's inside the container

- `git`
- `glab` (GitLab CLI)
- Maven 3.9 + JDK 21
- Node.js 22 + Claude Code
- Docker CLI (talks to the host daemon for Testcontainers)

## Usage

```bash
./claude-box                      # mount the current directory
./claude-box ~/code/my-app        # mount a specific directory
./claude-box --shell ~/x          # drop into a bash shell instead of Claude
./claude-box --name api ~/x       # name the box (container name + notifications)
./claude-box --workspace ~/x      # mount at /workspace instead of the host path
./claude-box --no-share ~/x       # clean slate, no host ~/.claude config
./claude-box --no-share-settings ~/x # don't merge host settings.json + CLAUDE.md
./claude-box --rebuild            # rebuild the image (after editing the Dockerfile)
./install-defaults.sh             # install the shared rules + hooks into ~/.claude
```

First run builds the image (a few minutes). By default the project is mounted
at its **real host path** inside the container (e.g. `~/code/my-app` →
`/Users/you/code/my-app`) so Testcontainers file mounts line up — see below.
Claude starts in that directory.

The container is named `claude-box-<name-or-directory>` (e.g. `claude-box-my-app`)
rather than the random name compose would pick, so `docker ps` / `docker exec`
stay readable with several boxes running. A `-2`, `-3`, … suffix is added if the
name is taken; `CLAUDE_BOX_CONTAINER_NAME` overrides it entirely.

### First-time setup inside the container

- **Claude login:** Claude will print an OAuth URL. Open it in your Mac
  browser, approve, paste the code back. The credentials are shared across all
  boxes (see [Shared login](#shared-login)), so you log in once for every
  worktree/project. Pass `--no-shared-auth` to keep the login per-directory.
- **GitLab:** run `glab auth login`. Stored per-project under
  `~/.claude-box/projects/<key>/glab`.
- Your host `~/.gitconfig` (name/email) is mounted read-only, so commits are
  attributed correctly.

## Per-directory memory

State lives on the host under `~/.claude-box/projects/<basename>-<hash>/`:

```
~/.claude-box/projects/
  my-app-1a2b3c4d/
    claude/   <- Claude's home (memory, settings, credentials)
    glab/     <- GitLab CLI config
```

The `<hash>` is derived from the full path, so two directories with the same
name don't collide, and re-mounting the same directory always reuses its
memory. The Maven cache (`~/.m2`) is a shared Docker volume across all projects
(no point re-downloading dependencies per project).

Override the base location with `CLAUDE_BOX_HOME=/some/path ./claude-box`.

## Host config sharing

Your host `~/.claude` is **not** used as the container's home (that would break
Claude — it needs to *write* credentials, memory, and runtime state, and it
would collapse the per-directory isolation). Instead, the static, shareable
parts are overlaid **read-only** on top of the per-project home:

| Shared by default (read-only)                                                                   | Not shared |
| ----------------------------------------------------------------------------------------------- | ---------- |
| `skills/`, `commands/`, `agents/`, `rules/`, `scripts/`, `output-styles/`, `settings.json`, `CLAUDE.md` | everything else |

Memory, `projects/`, `todos/`, etc. stay **writable and per-project**.
Credentials are **writable and shared** (see [Shared login](#shared-login)).
Use `--no-share` for a completely clean slate.

### Shared login

Because the state key is derived from the directory path, every worktree of a
repo is a separate project — which would otherwise mean re-authenticating in
each one. To avoid that, the Claude login is shared: a single credentials file
at `~/.claude-box/credentials.json` is bind-mounted into every box at
`~/.claude/.credentials.json`, on top of the per-project home. Log in once and
all boxes — every worktree, every project — reuse the session.

This is safe across token refresh: Claude writes the file in place on login,
and on refresh its atomic rename fails with `EBUSY` against the bind-mount and
falls back to an in-place copy, so the host file stays in sync.

- Disable with `--no-shared-auth` (or `CLAUDE_BOX_SHARED_AUTH=0`) to keep
  credentials per-directory, as before.
- Override the file location with `CLAUDE_BOX_CREDENTIALS=/some/path`.

### App config (`~/.config/...`)

So tools find their config natively, `~/.config/dticket` is mounted (if it
exists on the host) at the same path inside the box — read-write, so the app
behaves exactly as on the host. No flag needed. To mount more such dirs:

```bash
CLAUDE_BOX_CONFIG_DIRS="dticket othertool" ./claude-box ~/code/app
```

This is unaffected by `--no-share` (which only governs `~/.claude`). Make a
mount read-only by adding `:ro` to the relevant line in `claude-box`.

Your host `settings.json` is **not** bind-mounted; it is merged into the
generated box settings (see below) so the guardrail survives, and your
`CLAUDE.md` is overlaid read-only. Pass `--no-share-settings` to skip both.

The merged file sits at the **user** level inside the box. A project's own
`.claude/settings.json` and `.claude/settings.local.json` are read from the
mounted project directory as before and still take precedence, so a project
keeps its own model, permissions and hooks. Two details on how the levels
combine: hooks from every level all run (they do not replace each other), and
permissions are a union in which `deny` beats `allow`. So a hook or `deny` rule
you set once on the host applies in every box, and the box guardrails are merged
last so they always win.

## Shared defaults: writing rules and hooks

`suggestions/` holds a starting set of rules and hooks that every box (and your
host Claude) can use. Install it into your own `~/.claude` with:

```bash
./install-defaults.sh              # install into ~/.claude
./install-defaults.sh --dry-run    # show what would change, write nothing
```

It needs `jq` and `python3` on the host — `jq` to merge the settings, `python3`
to run the hooks.

The directory mirrors `~/.claude`, so you can also copy the files by hand:

| From `suggestions/`                            | To                                  | What it does                                                     |
| ---------------------------------------------- | ----------------------------------- | ---------------------------------------------------------------- |
| `rules/writing-style.md`                       | `~/.claude/rules/`                  | Plain-English writing rules, loaded as a global instruction       |
| `scripts/writing-style/`                       | `~/.claude/scripts/`                | Five scripts: three enforce the word list, one prints the rules again where the model reads them, one sends the changed prose to a small model |
| `settings.json`                                | merged into `~/.claude/settings.json` | Registers the hooks, denies reads of `.env`/secrets, asks before `git commit`/`push` |

Installing on the host is enough for every box, because `claude-box` mounts
`~/.claude/rules` and `~/.claude/scripts` read-only and merges
`~/.claude/settings.json` (see [Host config sharing](#host-config-sharing)).
Every hook is registered as `$HOME/.claude/scripts/...`, which resolves on the
host and inside a box alike, and each does nothing when its file is absent — so
`--no-share` still gives a clean slate.

`install-defaults.sh` merges, never replaces: your existing `deny`/`ask` entries
and hooks are kept, a writing-style hook is added only if its command is not
registered already, and a hook from an earlier install has its matcher brought up
to date instead of duplicated. A timestamped backup of `settings.json` is written
first. Running it twice changes nothing the second time, and an `allowlist.txt`
you have already edited is left alone.

### How the word-list checks fit together

`check-forbidden-words.py` runs **before** a `Write`, `Edit`, `MultiEdit` or
`NotebookEdit` and reads the text the call would put in the file. A banned word
stops the write, and Claude rewords before anything reaches disk.

That hook only sees what a tool call passes as its input. A `sed` command, a
heredoc, an MCP server or a subagent writes the file directly, so there is
nothing for it to read — which is how a banned word used to slip through.
`scan-changed-files.py` checks those: it runs **after** every `Bash` call and
again when Claude stops, finds the changed lines with `git diff`, and matches
them against the same word list. The write has already happened by then, so it
names the file and line and Claude fixes it afterwards.

It reads changes, not the tree. Git compares the stat data cached in its index
and opens only the files whose stat differs, and only added lines are scanned.
On a 4000-file, 2.8 GB repository the check takes about 0.35 seconds, nearly all
of it process startup. A violation is reported once per session, so a working
tree that is already dirty is not re-reported on every `Bash` call.

Three things it does not check:

- **Work outside a git repository.** The scan needs `git diff` to find changes,
  so it does nothing elsewhere — including `~/.claude` itself.
- **Files you edit yourself.** Hooks only see what Claude does.
- **Generated files.** A file with more than 400 added lines is named in the
  message and left unscanned, so a rebuilt data file does not bury the report.

### Review comments on a merge request

A review comment is prose, and the rules apply to it. Neither word-list hook saw
one: a note goes out through `glab` or `post-draft.py` and never reaches a file,
so there was nothing for them to read.

`check-review-notes.py` runs **before** a `Bash` call, takes the note text out of
the command and matches it against the same word list. A banned word stops the
call and Claude rewords before anything reaches GitLab. It checks three shapes:

| Command | Where the text is |
| ------- | ----------------- |
| `glab api .../draft_notes\|notes\|discussions` | the heredoc body |
| `post-draft.py general\|file\|reply` | the heredoc body |
| `glab mr note\|comment` | the value of `-m` or `--message` |

A `GET` on `.../notes` has no body, so it passes. A heredoc in an unrelated
command is not read at all, because the command must name one of the three above.
What the hook cannot see is a body read from a file or held in a shell variable:
the text is not in the command then. Repeated blocks are counted per merge
request, so after three the message tells Claude to stop rewriting and ask you.

### Keeping the rules where the model reads them

The rules are part of every request, in the instructions block near the start.
Nothing removes them, not even compaction. What fades is attention: the further a
rule is from the end of the context, the less it shapes the writing. After a
hundred thousand tokens of code and tool output, the rules are ignored while they
are still in the request.

`inject-rules.py` prints the rules again at the end of the context. It runs on
three events:

| Event | When | What it does |
| ----- | ---- | ------------ |
| `UserPromptSubmit` | every message you send | Prints the rules, which land right after your prompt |
| `PostToolBatch` | after each batch of tool calls resolves | Prints them every fifteenth batch, and after three when the batch wrote a `.md` file. This is the only place to print them in a long run with no message from you |
| `PreCompact` | before compaction | Asks the compactor to keep the rules in the summary, word for word |

The rules file is 41 lines, so one print costs about 700 tokens. Injected context
is added after the cached part of the request, so it does not cost a cache miss.
`CLAUDE_WRITING_STYLE_EVERY_N` changes the batch interval.

### The model-backed check

A regex checks words. It cannot check short sentences, one idea per sentence,
hedging, a dropped subject, or whether a comment describes the thing itself.
`check-prose-style.py` sends the changed documentation lines and the rules to a
small model and reports what comes back.

It is off until you add this to `~/.claude/settings.json`:

```json
"env": { "CLAUDE_WRITING_STYLE_LLM": "1" }
```

Claude Code puts that block in the environment of every hook it runs, and
`claude-box` copies the host `settings.json` into each box (`claude-box:307`), so
one entry covers the host and every box. An `export` in your shell reaches a host
session only. A box is started by `docker compose run`, which passes on nothing
from your shell — only the variables listed in `docker-compose.yml`. Add
`CLAUDE_WRITING_STYLE_LLM: ${CLAUDE_WRITING_STYLE_LLM:-}` to the `environment:`
block there if you would rather switch it on per box from the shell.

It runs when the turn ends, once per turn, over two kinds of prose: the added
lines in `.md`, `.txt` and similar files, and the changed comments and docstrings
in code files. It knows `//` and `/* */` for Kotlin, Java and TypeScript, `#` for
Python, Bash, YAML and TOML, and the Python docstring; `COMMENT_STYLE` in the
script maps a suffix to one of those three. A shebang and a tool directive such
as `# shellcheck` or `# type:` are left out, and a triple-quoted string assigned
to a name is data, so it is left out too.

A comment is sent with one line above it and three below, marked as context. The
rules ask whether a comment describes the thing itself rather than its caller,
and that cannot be judged without seeing the declaration under it. Context lines
are labelled in the prompt and the judge is told never to report one. On a test
file it found the caller-facing KDoc in Kotlin, the same fault in a Python
docstring and a Bash comment, a dropped subject, and a comment on
self-explanatory code — and reported no context line.

One line is reported at most twice per session. Rewording a line changes what
the judge calls the rule, so the same line used to come back under a new name and
the rewriting never ended. Past `LINE_LIMIT` findings the line is left out of the
request. The judge is also told to report each line once, with the clearest
breach, and to count a metaphor only where a reader could take it the wrong way —
without that it reports an ordinary verb such as "a rule sits at the start".

Set `CLAUDE_WRITING_STYLE_NO_COMMENTS` to `1` to check documentation files only.
At most 120 documentation lines and 90 comment lines are sent, 30 of them per
file, so one heavily commented file leaves room for the others.

The judge is a separate `claude -p --safe-mode` process, so it starts empty: no
hooks, no `CLAUDE.md`, no rules of its own, and no way to start a second judge.
It reads the rules and the changed lines, and answers with JSON. A fresh reader
keeps to the rules in full, which is the whole reason to run the judge outside
the session.

Its hook entry sets `"async": true` and `"asyncRewake": true`, so the turn ends
without waiting: the check runs in the background, which took 50 to 155 seconds
in testing, and wakes Claude with the findings. Drop both fields from the entry
if you would rather the turn wait for the answer.

Two more settings, both optional and read the same way:
`CLAUDE_WRITING_STYLE_MODEL` (default `haiku`) and
`CLAUDE_WRITING_STYLE_LLM_TIMEOUT` (default 240 seconds). The same holds for
`CLAUDE_WRITING_STYLE_EVERY_N` in the section above: put it in the `env` block,
not in your shell, or the box will not see it.

### Living with the writing-style hooks

When the first hook blocks a write, it names the word and Claude rewords. The
second hook reports the file and line instead, because the write already
happened. If a banned word is genuinely right, add the whole phrase (a clause or
a sentence, not a bare word) to
`~/.claude/scripts/writing-style/allowlist.txt`; only text inside that phrase is
exempt. After three blocks on the same file the hook tells Claude to stop
rewriting and ask you instead.

Inside a box that file is on a read-only mount, so edit it on the host. Drop the
`:ro` from the `scripts` mount in `claude-box` if you would rather let Claude
edit it in the box.

Edit `FORBIDDEN` in `check-forbidden-words.py` to change the word list, and
`rules/writing-style.md` to change the guidance. Keep the two in step: the rules
file is what Claude reads, the script is what enforces it. `scan-changed-files.py`
imports the list, the allowlist and the skipped paths from
`check-forbidden-words.py`, so both hooks stay in step on their own.
`inject-rules.py` and `check-prose-style.py` read `rules/writing-style.md` at run
time, so a change to the rules reaches them with no edit.

## Blocking specific commands (without prompts for everything else)

The box runs with `--dangerously-skip-permissions`, but in current Claude Code
`deny` rules **and** `PreToolUse` hooks are still enforced in bypass mode. So
you get zero permission prompts while specific commands stay blocked.

There are two separate block lists. Both are written into the per-project
Claude home and enforced by a **PreToolUse hook** (`hooks/block-cmds.sh`) that
reads the real command and `exit 2` blocks it (catching compound commands and
aliases), backed by visible **deny rules** for the command list:

**1. Blocked commands** (`CLAUDE_BOX_BLOCK`, default `git commit,git push`) —
each entry is a list of words that must appear in order (`git commit` matches
`git … commit`).

```bash
CLAUDE_BOX_BLOCK="git commit,git push" ./claude-box ~/code/app
CLAUDE_BOX_BLOCK="" ./claude-box ~/code/app   # turn this block off
```

**2. Blocked paths** (`CLAUDE_BOX_BLOCK_PATHS`, default `.m2/repository`) — any
command whose text mentions one of these paths is blocked. This stops Claude
decompiling/extracting jars or reading classes in the Maven repo
(`~/.m2/repository`), while `mvn` itself still works because Maven never writes
that path into the command.

```bash
CLAUDE_BOX_BLOCK_PATHS=".m2/repository,/secrets" ./claude-box ~/code/app
CLAUDE_BOX_BLOCK_PATHS="" ./claude-box ~/code/app   # turn this block off
```

The box rewrites its `settings.json` on each launch, so don't hand-edit it —
edit `CLAUDE_BOX_BLOCK` (or this script) instead.

> **Still note:** `glab`/`git` reach the real remotes over the network with
> your token — the sandbox contains the *filesystem*, not network actions.
> `git push` is blocked by default; add any `glab api --method POST` style
> calls to `CLAUDE_BOX_BLOCK` if you want those stopped too.

## Testcontainers

**Yes, Testcontainers works** — the container bind-mounts the host Docker
socket (`/var/run/docker.sock`), so `mvn test` can start containers. A few
things to know, because containers started by your tests are **siblings** on
the host daemon, not children of the claude-box container:

1. **Docker Desktop:** make sure *Settings → Advanced → "Allow the default
   Docker socket to be used"* is enabled, otherwise `/var/run/docker.sock`
   won't exist on the host to mount.

2. **Networking is already configured.** The compose file sets
   `TESTCONTAINERS_HOST_OVERRIDE=host.docker.internal` and adds a
   `host-gateway` host entry, so Testcontainers reaches the ports your test
   containers expose. `getMappedPort()` / `getHost()` work as usual.

3. **File mounts — handled by default.** When a test bind-mounts a file into a
   container (`MountableFile`, `withFileSystemBind`,
   `withClasspathResourceMapping`), the **host daemon** resolves the source
   path, not the claude-box container. To make those line up, the project is
   mounted at its real host path by default (e.g. `/Users/you/app` →
   `/Users/you/app`), so any source path under the project resolves identically
   on host and container. No action needed.
   - If you run with `--workspace` (project mounted at `/workspace`), this no
     longer holds — paths under `/workspace` don't exist on the host. In that
     mode, use `withCopyFileToContainer(...)` / `withCopyToContainer`, which
     copy through the Docker API and always work regardless of mount path.
   - Mounts of files *outside* the project tree (e.g. `/tmp/...`) still need to
     exist on the host daemon either way.

4. **Ryuk** (the resource reaper) runs fine over the mounted socket and cleans
   up leftover containers. Disable it with `TESTCONTAINERS_RYUK_DISABLED=true`
   in `docker-compose.yml` only if you hit issues.

### Security note

Mounting the Docker socket gives the container effective root-level control of
your host's Docker. That's the deliberate trade-off for Testcontainers support.
If you don't need it, remove the `/var/run/docker.sock` volume from
`docker-compose.yml` for stricter isolation.

## Files

| File                 | Purpose                                             |
| -------------------- | --------------------------------------------------- |
| `claude-box`         | Start script (build + run + mount + memory routing) |
| `docker-compose.yml` | Service, volumes, Testcontainers env                |
| `Dockerfile`         | Image: git, glab, Maven/JDK 21, Node, Claude Code   |
| `entrypoint.sh`      | Fixes socket perms, drops root → `claude` user      |
| `install-defaults.sh` | Installs `suggestions/` into your `~/.claude`       |
| `suggestions/`       | Shared writing rules + hooks, laid out like `~/.claude` |
