# claude-box

Run Claude Code inside a Docker container with permission prompts turned off
(`--dangerously-skip-permissions`), where it can only reach one mounted
directory. Each mounted directory gets its own state, and each repository gets
its own memory, shared by all of its worktrees.

Inside the image: `git`, `glab`, Maven 3.9 + JDK 21, Node.js 22 + Claude Code,
and the Docker CLI (it talks to the host daemon, so Testcontainers works).

## Requirements

- Docker. On a Mac, turn on *Settings → Advanced → "Allow the default Docker
  socket to be used"*, otherwise `/var/run/docker.sock` does not exist to mount.
- `jq` on the host.
- macOS for the spoken notifications. Everything else works anywhere.

## Install

```bash
git clone git@github.com:rolfchess/claude-box.git ~/code/claude-box
~/code/claude-box/claude-box ~/code/my-app     # first run builds the image (a few minutes)
```

The script reads `docker-compose.yml` next to itself, so put the checkout on
your `PATH` or make an alias. A symlink from somewhere else does not work.

The rules and hooks in `suggestions/` are optional. Install them once on the
host and every box gets them — see
[Shared defaults](#shared-defaults-rules-and-hooks).

## Usage

```bash
claude-box                       # mount the current directory
claude-box ~/code/my-app         # mount a specific directory
```

| Flag | What it does |
| ---- | ------------ |
| `--shell` | Open a bash shell instead of Claude |
| `--name NAME` | Name the box, for the container name and the spoken notifications |
| `--workspace` | Mount the project at `/workspace` instead of its host path |
| `--no-share` | Do not overlay any host `~/.claude` config (clean slate) |
| `--no-share-settings` | Do not merge the host `settings.json`, do not overlay `CLAUDE.md` |
| `--no-shared-auth` | Keep the Claude login per directory |
| `--no-notify` | Do not speak notifications |
| `--rebuild` | Rebuild the image, after editing the `Dockerfile` |
| `--list` | List the boxes on this daemon and exit |
| `--help` | Print the options |

| Variable | Default | What it does |
| -------- | ------- | ------------ |
| `CLAUDE_BOX_HOME` | `~/.claude-box` | Where the per-project state lives |
| `CLAUDE_BOX_BLOCK` | `git commit,git push` | Blocked commands |
| `CLAUDE_BOX_BLOCK_PATHS` | `.m2/repository` | Blocked paths |
| `CLAUDE_BOX_CONFIG_DIRS` | `dticket` | Directories under `~/.config` to mount |
| `CLAUDE_BOX_CONTAINER_NAME` | from the directory name | Container name |
| `CLAUDE_BOX_KEEP` | `0` | `1` leaves the container up on exit |
| `CLAUDE_BOX_NOTIFY` | `1` | `""` turns off the spoken notifications |
| `CLAUDE_BOX_SHARED_AUTH` | `1` | `0` keeps the login per directory |
| `CLAUDE_BOX_CREDENTIALS` | `~/.claude-box/credentials.json` | The shared login file |
| `CLAUDE_BOX_CLAUDE_JSON` | `~/.claude-box/claude.json` | The shared onboarding state |
| `CLAUDE_BOX_SHARE_SETTINGS` | `1` | `0` skips the settings merge |
| `CLAUDE_BOX_NO_GIT_COMMONDIR` | `0` | `1` skips the worktree git mount |

By default the project is mounted at its **real host path** inside the container
(`~/code/my-app` → `/Users/you/code/my-app`) so Testcontainers file mounts line
up — see [Testcontainers](#testcontainers). Claude starts in that directory.

### First-time setup inside the container

- **Claude login:** Claude prints an OAuth URL. Open it in your browser,
  approve, paste the code back. The login is shared across all boxes, so you do
  this once — see [Shared login](#shared-login).
- **GitLab:** nothing to do. The box uses your host login — see
  [GitLab login](#gitlab-login).
- Your host `~/.gitconfig` is mounted read-only, so commits get the right author.
  The image sets `safe.directory = *` in the system git config, so git does not
  refuse a mounted repository owned by another uid.

### Names, `--list` and shutdown

The container is named `claude-box-<name-or-directory>` (`claude-box-my-app`)
instead of the random name compose would pick, so `docker ps` and `docker exec`
stay readable with several boxes up. A `-2`, `-3`, … suffix is added if the name
is taken. Every box also has a `claude-box=1` label, so `claude-box --list` (or
`docker ps --filter label=claude-box`) finds them all, even a renamed one.

The container is stopped and removed when the script exits, including when you
close the terminal window. `docker compose run --rm` on its own leaves the box
running in that case. Set `CLAUDE_BOX_KEEP=1` to leave it up.

### Spoken notifications

On macOS the box speaks a line through `say` when Claude finishes a turn or needs
your input, so you can leave it running. It says the `--name` value, or the
directory name. The 60-second "waiting for your input" notification is left out:
it is not actionable and it misfires while background agents are still running.

### Git worktrees

In a worktree, `.git` is a file pointing at the parent repository, outside the
mounted directory, so git in the box cannot find its repository. `claude-box`
bind-mounts that one parent `.git` directory at its real host path. Nothing else
from the parent repository is mounted, and the guardrails below still apply.

## State and memory

State lives on the host under `~/.claude-box/`:

```
~/.claude-box/
  projects/
    my-app-1a2b3c4d/
      claude/   <- Claude's home (settings, transcripts, credentials)
      glab/     <- GitLab CLI config, only with --no-share-glab
  memory/
    my-app-3bb5210e/  <- memory, one store per repository
```

The `<hash>` comes from the full path, so two directories with the same name do
not collide, and re-mounting a directory always reuses its state. The Maven
cache (`~/.m2`) is one Docker volume shared by all projects.

Memory is the one thing that is not per directory. Claude keeps it in
`~/.claude/projects/<working directory>/memory`, so the store follows the path
the project is mounted at: every worktree, and `--workspace`, would start with an
empty one. `claude-box` binds one directory per repository onto that path
instead, so every worktree of a project reads and writes the same memory. What
Claude learns about the project is there on the next branch.

That is the right scope for what memory holds. Your preferences, the project's
goals and its constraints belong to the project, not to the branch you are on.

The repository is found with `git rev-parse --git-common-dir`, which gives the
same answer from the main checkout and from every worktree. Outside a git
repository the store is per directory.

| Variable / flag | Effect |
| --------------- | ------ |
| `CLAUDE_BOX_MEMORY=<dir>` | Use this directory as the store. |
| `--no-shared-memory` | Keep memory per directory (`CLAUDE_BOX_SHARED_MEMORY=0`). |

A store left from before memory moved out of the per-directory Claude home is
moved into the shared one on the first run, if the shared one is still empty.

## GitLab login

`glab` reads its configuration from `$GLAB_CONFIG_DIR`, else `$XDG_CONFIG_HOME/glab-cli`,
else `~/.config/glab-cli`. On macOS it uses `~/Library/Application Support/glab-cli`
when `~/.config/glab-cli` does not exist. The script finds the host directory the
same way and mounts it at `~/.config/glab-cli` in the box, so `glab` in the box is
logged in as you and needs no setup.

The token itself is often not in that directory. `glab auth login` puts it in the
operating system keyring whenever there is one, which is the default on macOS,
and the container has no keyring. So when the config file has no token, the
script asks the host's own `glab` for it and passes it in as `GITLAB_TOKEN`:

```bash
glab auth status --show-token
```

That runs in the project directory, so a project on a self-managed instance gets
that instance's token and not the one for whatever host is the default. Set
`GITLAB_TOKEN` yourself and that value is used instead, with no keyring lookup.
`GITLAB_HOST` is passed through when you set it.

The token is passed with `docker run -e`, so it is in the host's process list
while the `docker` command runs, and in `docker inspect` for the life of the
container. Both are on your own machine, where the token is readable anyway.

| Flag / variable | Effect |
| --------------- | ------ |
| `--no-share-glab` | Use a login of its own, under `~/.claude-box/projects/<key>/glab`. Run `glab auth login` in the box once (`CLAUDE_BOX_SHARE_GLAB=0`). |
| `GITLAB_TOKEN=...` | Use this token, whatever the host config says. |

## Host config sharing

Your host `~/.claude` is **not** used as the container's home. That would break
Claude, which needs to *write* credentials, memory and runtime state, and it
would undo the per-directory isolation. Instead the static parts are overlaid
**read-only** on top of the per-project home:

| Shared by default (read-only) | Not shared |
| ----------------------------- | ---------- |
| `skills/`, `commands/`, `agents/`, `rules/`, `scripts/`, `output-styles/`, `CLAUDE.md` | everything else |

`projects/`, `todos/` and the rest stay **writable and per-project**; memory is
writable and per-repository (see above).
Use `--no-share` for a completely clean slate.

Your `settings.json` is not mounted. It is merged into the generated box
settings so the guardrails survive. The merged file sits at the **user** level,
so a project's own `.claude/settings.json` and `.claude/settings.local.json`
still win and keep their model, permissions and hooks. Hooks from every level
all run, and permissions are a union where `deny` beats `allow`. So a hook or
`deny` rule you set once on the host applies in every box, and the box
guardrails are merged last so they always win. `--no-share-settings` skips the
merge and the `CLAUDE.md` overlay.

One host setting is left out: `blockReadsOutsideWorkingDirectories`, at the top
level or under `permissions`. On the host it keeps Claude inside the project. In
the box, Claude can only reach the project and the mounts, so the setting would
only stop it from using the container's own files. A project's own
`.claude/settings.json` can still set it.

### Shared login

The state key comes from the directory path, so every worktree of a repository is
a separate project, which would mean logging in again in each one. So the login
is shared: one credentials file at `~/.claude-box/credentials.json` is
bind-mounted into every box at `~/.claude/.credentials.json`, on top of the
per-project home. The onboarding state (`~/.claude.json`) is shared the same way,
so the theme and login prompts do not come back either.

This survives a token refresh. Claude writes the file in place on login, and on
refresh its atomic rename fails with `EBUSY` against the bind-mount and falls
back to an in-place copy, so the host file stays in sync.

`--no-shared-auth` keeps both files per directory instead.

### App config (`~/.config/...`)

So tools find their config natively, `~/.config/dticket` is mounted (if it exists
on the host) at the same path in the box, read-write. No flag needed. Mount more
with `CLAUDE_BOX_CONFIG_DIRS="dticket othertool"`. This is unaffected by
`--no-share`, which only governs `~/.claude`. Add `:ro` to the line in
`claude-box` to make a mount read-only.

## Blocking specific commands

The box runs with `--dangerously-skip-permissions`, but `deny` rules **and**
`PreToolUse` hooks are still enforced in bypass mode. So you get no permission
prompts while specific commands stay blocked. There are two lists, both written
into the per-project Claude home and enforced by a `PreToolUse` hook
(`hooks/block-cmds.sh`) that reads the real command and blocks it with `exit 2`,
which catches compound commands and aliases. Deny rules are a visible second
layer.

```bash
# Commands: each entry is a list of words that must appear in order
# ("git commit" matches "git … commit").
CLAUDE_BOX_BLOCK="git commit,git push" claude-box ~/code/app
CLAUDE_BOX_BLOCK="" claude-box ~/code/app          # off

# Paths: any command whose text mentions one of these is blocked. This stops
# Claude decompiling jars or reading classes in the Maven repo, while `mvn`
# itself still works, because Maven never writes that path into the command.
CLAUDE_BOX_BLOCK_PATHS=".m2/repository,/secrets" claude-box ~/code/app
CLAUDE_BOX_BLOCK_PATHS="" claude-box ~/code/app    # off
```

The box rewrites its `settings.json` on each launch, so do not hand-edit it.
Edit the two variables instead.

> **Note:** `glab` and `git` reach the real remotes over the network with your
> token. The sandbox contains the *filesystem*, not network actions. `git push`
> is blocked by default. Add `glab api --method POST` style calls to
> `CLAUDE_BOX_BLOCK` if you want those stopped too.

## Testcontainers

Testcontainers works: the box bind-mounts the host Docker socket
(`/var/run/docker.sock`), so `mvn test` can start containers. They are
**siblings** on the host daemon, not children of the box, which is why three
things matter.

1. **Networking is already set up.** The compose file sets
   `TESTCONTAINERS_HOST_OVERRIDE=host.docker.internal` and adds a `host-gateway`
   host entry, so Testcontainers reaches the ports your test containers expose.
   `getMappedPort()` and `getHost()` work as usual.
2. **File mounts work by default.** When a test bind-mounts a file
   (`MountableFile`, `withFileSystemBind`, `withClasspathResourceMapping`), the
   **host daemon** resolves the source path, not the box. The project is mounted
   at its real host path, so any path under it resolves the same on both sides.
   Under `--workspace` that no longer holds — nothing under `/workspace` exists
   on the host — so use `withCopyFileToContainer` / `withCopyToContainer`, which
   copy through the Docker API. Mounts of files *outside* the project tree still
   have to exist on the host either way.
3. **Ryuk** cleans up leftover containers over the mounted socket. Set
   `TESTCONTAINERS_RYUK_DISABLED=true` in `docker-compose.yml` only if you hit
   problems.

Mounting the Docker socket gives the container root-level control of your host's
Docker. That is the deliberate trade-off for Testcontainers. Remove the
`/var/run/docker.sock` volume from `docker-compose.yml` if you do not need it.

## Shared defaults: rules and hooks

`suggestions/` holds a starting set of rules — how to write, and what a commit
message may say — and the hooks that enforce them. The directory mirrors
`~/.claude`, so you can copy the files by hand, or:

```bash
./install-defaults.sh              # install into ~/.claude
./install-defaults.sh --dry-run    # show what would change, write nothing
```

It needs `jq` on the host to merge the settings and `python3` to run the hooks.
It merges, never replaces: your existing `deny`/`ask` entries and hooks are kept,
a hook is added only if its command is not registered already, and a hook from an
earlier install has its matcher brought up to date instead of duplicated. A
timestamped backup of `settings.json` is written first. Running it twice changes
nothing the second time, and an `allowlist.txt` you have edited is left alone.

Installing on the host is enough for every box: `claude-box` mounts
`~/.claude/rules` and `~/.claude/scripts` read-only and merges
`~/.claude/settings.json`. Every hook is registered as
`$HOME/.claude/scripts/...`, which resolves on the host and inside a box alike,
and each does nothing when its file is absent — so `--no-share` still gives a
clean slate.

| Script (`scripts/`) | Runs | What it reads |
| ------------------- | ---- | ------------- |
| `writing-style/check-forbidden-words.py` | before `Write`, `Edit`, `MultiEdit`, `NotebookEdit` | The text the call would write. A banned word blocks the write, and Claude rewords before anything reaches disk |
| `writing-style/scan-changed-files.py` | after every `Bash` call, and when Claude or a subagent stops | The added lines in `git diff`. The write has happened, so it names the file and line and Claude fixes it afterwards |
| `bash/check-bash-command.py` | before every `Bash` call | The command. It runs the two checks below in one process |
| `git/commit-message.py` | called by the above | The message in a commit, a pull request or a merge request. A line that credits Claude blocks the call |
| `writing-style/review-notes.py` | called by the above | The note text inside a `glab` or `post-draft.py` command, before it reaches GitLab |
| `writing-style/inject-rules.py` | on your message, after a tool batch, before a file edit, before compaction | Nothing. It prints the rules again at the end of the context, without the word list a hook already enforces. It refuses an edit when the last print is five or more batches old. The refusal contains the rules, and Claude makes the edit again |
| `writing-style/check-prose-style.py` | when the turn ends | The changed documentation lines and code comments, judged by a small model. Off by default |

`settings.json` registers the hooks, denies reads of `.env` and secrets, and asks
before `git commit` and `git push`. `rules/writing-style.md` and
`rules/git-commits.md` are the guidance itself, loaded as global instructions.

The words to avoid are in `scripts/writing-style/words.txt`, and nowhere else.
Each line gives the word, the regex that matches every form of it, and the plain
replacement. The hook builds its regexes from that file and names the
replacement when it blocks, so the next attempt does not have to guess.
`render-words.py` writes the list into the "Words to avoid" section of
`rules/writing-style.md`, which `install-defaults.sh` does for you. Add a word to
`words.txt`, run the installer, and both the hook and the rules are up to date.

A group marked `hook-only` is blocked but left out of the rules file. Latin,
idioms and jargon nouns are marked that way: a model hardly ever writes them, so
a reminder in every session costs more than the rare block does.

What each hook sees and misses, the allowlist, and the settings for the
model-backed check are in [`suggestions/README.md`](suggestions/README.md).

## Files

| File | Purpose |
| ---- | ------- |
| `claude-box` | Start script: build, run, mounts, memory routing, guardrails |
| `docker-compose.yml` | Service, volumes, Testcontainers env |
| `Dockerfile` | Image: git, glab, Maven/JDK 21, Node, Claude Code |
| `entrypoint.sh` | Fixes socket permissions, drops root → the `claude` user |
| `install-defaults.sh` | Installs `suggestions/` into your `~/.claude` |
| `suggestions/` | Shared rules and hooks, laid out like `~/.claude` |
