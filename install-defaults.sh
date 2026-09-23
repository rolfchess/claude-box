#!/usr/bin/env bash
#
# install-defaults.sh — copy the shared rules and hooks in suggestions/ into your
# own ~/.claude, so your host Claude and every claude-box use them.
#
#   ./install-defaults.sh            # install into ~/.claude
#   ./install-defaults.sh --dry-run  # show what would change, write nothing
#
# What it installs:
#   rules/*.md                                    -> ~/.claude/rules/
#   scripts/*/*.py, scripts/*/words.txt           -> ~/.claude/scripts/
#   settings.json (hooks + deny/ask rules)        -> merged into ~/.claude/settings.json
#
# The words to avoid are in scripts/writing-style/words.txt, and nowhere else.
# The hook builds its regexes from that file and names the replacement when it
# blocks a write. render-words.py writes the groups marked "teach" into the
# "Words to avoid" section of the installed writing-style.md. A group marked
# "hook-only" is blocked but left out, so a word a model hardly ever writes does
# not cost instruction space in every session. Edit words.txt and run this
# script again.
#
# The hooks do three jobs. Two enforce the word list: one blocks a write that
# uses a banned word, and one reports words that reached a file another way. One
# checks each Bash command: it blocks a commit or a request body that credits
# Claude, and a merge request note that uses a banned word. The last two keep
# the writing rules where the model reads them: one prints the rules again on
# every user turn, every fifteenth tool batch and in the compact instructions,
# and refuses a file edit, with the rules, when the last print is too old,
# and one sends the changed documentation and the changed comments in code to a
# small model to check what a regex cannot. That last one is off until you put
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
# to date, so a widened matcher reaches you without a duplicate hook. A group
# that runs a script this project no longer ships is removed, and so is the
# script. A timestamped backup is written first. Running it twice changes
# nothing the second time. An existing allowlist.txt is left alone.
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

# Scripts an earlier version of this project installed as hooks in their own
# right. Their work now happens elsewhere, so the hook group that runs them is
# removed from settings.json and the file itself is deleted. Matching is on the
# path, so a retired name must not be the name of a script still shipped.
RETIRED_SCRIPTS='[
    "writing-style/check-review-notes.py",
    "git/check-commit-message.py"
]'

# --- rules + scripts ------------------------------------------------------
run mkdir -p "$DEST/rules"

for f in "$SRC"/rules/*.md; do
    say "rules/$(basename "$f")"
    run cp "$f" "$DEST/rules/"
done

for dir in "$SRC"/scripts/*/; do
    group="$(basename "$dir")"
    run mkdir -p "$DEST/scripts/$group"
    for f in "$dir"*.py; do
        [ -e "$f" ] || continue
        say "scripts/$group/$(basename "$f")"
        run cp "$f" "$DEST/scripts/$group/"
        run chmod +x "$DEST/scripts/$group/$(basename "$f")"
    done
done

for f in "$SRC"/scripts/*/words.txt; do
    [ -e "$f" ] || continue
    group="$(basename "$(dirname "$f")")"
    say "scripts/$group/words.txt"
    run cp "$f" "$DEST/scripts/$group/"
done

# The word list is written into the rules the model reads, so the two cannot
# drift. --check first, because a words.txt the hook cannot read would silently
# stop blocking anything.
RENDER="$SRC/scripts/writing-style/render-words.py"
if [ -f "$RENDER" ]; then
    python3 "$RENDER" --check || {
        echo "install-defaults: fix suggestions/scripts/writing-style/words.txt first" >&2
        exit 1
    }
    if [ "$DRY_RUN" = "1" ]; then
        echo "   would: render the word list into $DEST/rules/writing-style.md"
    else
        python3 "$RENDER" --rules "$DEST/rules/writing-style.md" --in-place
    fi
fi

for retired in $(printf '%s' "$RETIRED_SCRIPTS" | jq -r '.[]'); do
    if [ -f "$DEST/scripts/$retired" ]; then
        say "scripts/$retired is no longer used, removing it"
        run rm -f "$DEST/scripts/$retired"
    fi
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
MERGED="$(printf '%s' "$BASE" | jq --slurpfile add "$SRC/settings.json" \
                                     --argjson retired "$RETIRED_SCRIPTS" '
    def cmds: [(.hooks // [])[]?.command];
    def shares($c): ((cmds - (cmds - $c)) | length) > 0;
    def runs_retired: any(cmds[]?; . as $c | any($retired[]; . as $r | $c | contains($r)));

    ($add[0]) as $new
    | .permissions = (.permissions // {})
    | .permissions.deny = ((.permissions.deny // []) + ($new.permissions.deny // []) | unique)
    | .permissions.ask  = ((.permissions.ask  // []) + ($new.permissions.ask  // []) | unique)
    | .hooks = (.hooks // {})
    | .hooks = (.hooks | with_entries(.value |= map(select(runs_retired | not))))
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
