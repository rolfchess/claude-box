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
./claude-box --workspace ~/x      # mount at /workspace instead of the host path
./claude-box --no-share ~/x       # clean slate, no host ~/.claude config
./claude-box --share-settings ~/x # also overlay host settings.json + CLAUDE.md
./claude-box --rebuild            # rebuild the image (after editing the Dockerfile)
```

First run builds the image (a few minutes). By default the project is mounted
at its **real host path** inside the container (e.g. `~/code/my-app` →
`/Users/you/code/my-app`) so Testcontainers file mounts line up — see below.
Claude starts in that directory.

### First-time setup inside the container

- **Claude login:** Claude will print an OAuth URL. Open it in your Mac
  browser, approve, paste the code back. Stored per-project, so you log in once
  per directory.
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

| Shared by default (read-only) | Not shared by default |
|-------------------------------|-----------------------|
| `skills/`, `commands/`, `agents/`, `rules/`, `scripts/`, `output-styles/` | `settings.json`, `CLAUDE.md` (use `--share-settings`) |

Credentials, memory, `projects/`, `todos/`, etc. stay **writable and
per-project**. Use `--no-share` for a completely clean slate.

### App config (`~/.config/...`)

So tools find their config natively, `~/.config/dticket` is mounted (if it
exists on the host) at the same path inside the box — read-write, so the app
behaves exactly as on the host. No flag needed. To mount more such dirs:

```bash
CLAUDE_BOX_CONFIG_DIRS="dticket othertool" ./claude-box ~/code/app
```

This is unaffected by `--no-share` (which only governs `~/.claude`). Make a
mount read-only by adding `:ro` to the relevant line in `claude-box`.

`--share-settings` does **not** bind-mount your `settings.json`; it merges it
into the generated box settings (see below) so the guardrail survives, and
overlays your `CLAUDE.md` read-only.

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

| File | Purpose |
|------|---------|
| `claude-box` | Start script (build + run + mount + memory routing) |
| `docker-compose.yml` | Service, volumes, Testcontainers env |
| `Dockerfile` | Image: git, glab, Maven/JDK 21, Node, Claude Code |
| `entrypoint.sh` | Fixes socket perms, drops root → `claude` user |
