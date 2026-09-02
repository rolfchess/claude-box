#!/usr/bin/env bash
#
# install-defaults.sh — copy the shared rules and hooks in suggestions/ into your
# own ~/.claude, so your host Claude and every claude-box use them.
#
#   ./install-defaults.sh            # install into ~/.claude
#   ./install-defaults.sh --dry-run  # show what would change, write nothing
#
# What it installs:
#   rules/writing-style.md                        -> ~/.claude/rules/
#   scripts/writing-style/*                       -> ~/.claude/scripts/
#   settings.json (hooks + deny/ask rules)        -> merged into ~/.claude/settings.json
#
# The hooks do two jobs. Three of them enforce the word list: one blocks a write
# that uses a banned word, one reports words that reached a file another way, and
# one blocks a merge request note that uses one.
# The rest keep the rules where the model reads them: they print the rules
# again on every user turn, every fifteenth tool batch, and in the compact
# instructions. A fourth script sends the changed documentation and the changed
# comments in code to a small model to check what a regex cannot; it is
# off until you put
# "env": { "CLAUDE_WRITING_STYLE_LLM": "1" } in your settings.json. A shell
# export reaches a host session only, never a box.
#
# claude-box mounts ~/.claude/rules and ~/.claude/scripts read-only into every
# box and merges ~/.claude/settings.json into the box settings, so installing on
# the host is all that is needed. A project's own .claude/settings.json still
# wins over these, and the box guardrails are merged last.
#
# Your settings.json is merged, never replaced: deny/ask entries are added to
# what you already have, and a hook group is added only if its command is not
# registered yet. A group installed by an earlier run has its matcher brought up
# to date, so a widened matcher reaches you without a duplicate hook. A
# timestamped backup is written first. Running it twice changes nothing the
# second time. An existing allowlist.txt is left alone.
#
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/suggestions"
DEST="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
DRY_RUN=0
[ "${1-}" = "--dry-run" ] && DRY_RUN=1

command -v jq >/dev/null 2>&1 || { echo "install-defaults: jq is required" >&2; exit 1; }
command -v python3 >/dev/null 2>&1 || echo "install-defaults: warning: python3 not found, the hooks will do nothing" >&2
[ -d "$SRC" ] || { echo "install-defaults: $SRC not found" >&2; exit 1; }

say() { echo ">> $*"; }
run() { if [ "$DRY_RUN" = "1" ]; then echo "   would: $*"; else "$@"; fi; }

# --- rules + script -------------------------------------------------------
run mkdir -p "$DEST/rules" "$DEST/scripts/writing-style"

for f in "$SRC"/rules/*.md; do
    say "rules/$(basename "$f")"
    run cp "$f" "$DEST/rules/"
done

for f in "$SRC"/scripts/writing-style/*.py; do
    say "scripts/writing-style/$(basename "$f")"
    run cp "$f" "$DEST/scripts/writing-style/"
    run chmod +x "$DEST/scripts/writing-style/$(basename "$f")"
done

if [ -f "$DEST/scripts/writing-style/allowlist.txt" ]; then
    say "scripts/writing-style/allowlist.txt exists, keeping yours"
else
    say "scripts/writing-style/allowlist.txt"
    run cp "$SRC/scripts/writing-style/allowlist.txt" "$DEST/scripts/writing-style/"
fi

# --- settings.json --------------------------------------------------------
SETTINGS="$DEST/settings.json"
CREATED=0
if [ -f "$SETTINGS" ]; then
    BASE="$(jq . "$SETTINGS")"
else
    BASE='{}'
    if [ "$DRY_RUN" = "0" ]; then
        printf '{}' > "$SETTINGS"
        CREATED=1
    fi
fi

# Add each deny/ask entry that is missing. For every hook event in the suggested
# settings, add a group only if none of the groups already registered for that
# event runs the same command; when one does, refresh its matcher instead so an
# earlier install picks up a widened one.
MERGED="$(printf '%s' "$BASE" | jq --slurpfile add "$SRC/settings.json" '
    def cmds: [(.hooks // [])[]?.command];
    def shares($c): ((cmds - (cmds - $c)) | length) > 0;

    ($add[0]) as $new
    | .permissions = (.permissions // {})
    | .permissions.deny = ((.permissions.deny // []) + ($new.permissions.deny // []) | unique)
    | .permissions.ask  = ((.permissions.ask  // []) + ($new.permissions.ask  // []) | unique)
    | .hooks = (.hooks // {})
    | reduce ($new.hooks // {} | to_entries[]) as $event (.;
        .hooks[$event.key] = (
            reduce $event.value[] as $group ((.hooks[$event.key] // []);
                ($group | cmds) as $c
                | if any(.[]; shares($c))
                  then map(if shares($c) and ($group | has("matcher"))
                           then .matcher = $group.matcher
                           else . end)
                  else . + [$group]
                  end)))
')"

if [ "$MERGED" = "$BASE" ]; then
    say "settings.json already up to date"
elif [ "$DRY_RUN" = "1" ]; then
    say "settings.json would change:"
    diff <(printf '%s\n' "$BASE") <(printf '%s\n' "$MERGED") || true
elif [ "$CREATED" = "1" ]; then
    say "settings.json created"
    printf '%s\n' "$MERGED" > "$SETTINGS"
else
    BACKUP="$SETTINGS.bak-$(date +%Y%m%d%H%M%S)"
    say "settings.json merged (backup: $(basename "$BACKUP"))"
    cp "$SETTINGS" "$BACKUP"
    printf '%s\n' "$MERGED" > "$SETTINGS"
fi

say "Done. Restart Claude Code (and any running box) to pick this up."

if [ "$(jq -r '.env.CLAUDE_WRITING_STYLE_LLM // ""' "$SETTINGS" 2>/dev/null)" = "1" ]; then
    say "The model-backed prose check is on."
else
    say "The model-backed prose check is off. Switch it on by adding this to $SETTINGS:"
    say '  "env": { "CLAUDE_WRITING_STYLE_LLM": "1" }'
fi
