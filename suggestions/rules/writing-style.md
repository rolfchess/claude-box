# Writing Style

How to write, in chat and in docs (READMEs, comments, commit messages, plans, MR notes).

## Principles

- Clear, simple, short, correct. Not wordy.
- Plain English. Use common words.
- One idea per sentence. Short sentences over long ones.
- Say things directly. Do not hedge.
- Do not reach for synonyms for variety. Use the same plain word each time.
- No euphemisms. Name the thing plainly.
- No metaphors when a plain word works.
- No abbreviations. Write words in full: "for example" not "e.g.", "that is" not "i.e.", "Product Owner" not "PO".
- Write complete sentences. Do not drop the subject or verb. "This is natural to do when X" not "Natural to do when X"; "It is reversible" not "Reversible".

## Comments and KDoc

- A comment or KDoc on a function or type says **what** it does — its purpose or contract — in its own terms. Not how it is implemented, and not why a caller uses it.
- Keep the description inward: describe the thing itself, not how callers or other systems use it. A repository helper's KDoc defines the concept; it does not describe the Twikey flow that calls it.
- A caller's reasoning belongs in a comment at the call site, not on the shared function it calls.
- Do not add comments for self-explanatory code (see also CLAUDE.md, Code Style).

<!-- The section below is written by scripts/writing-style/render-words.py from
     scripts/writing-style/words.txt. Edit words.txt, not this section, and run
     install-defaults.sh. -->
<!-- words-to-avoid:start -->
## Words to avoid, and what to use instead

Use the plain word on the right. A hook blocks a write that uses one of these and names the replacement, so a blocked write tells you what to write instead.

- Any fancy or figurative verb where a plain one exists. A column, order, row or object does not "carry", "pin", "hold onto", "own" or "travel with" a value -- it "has" or "stores" one.

### A verb used of a thing that cannot do it

- "carry", "carries" -> "has", "stores", "holds". A column stores a value; it does not carry it.
- "pinned", "pinning" -> "set", "stored", "fixed". Write "set the provider on the order", not "pin the provider onto the order".
- "surfaces", "surfacing" -> "appears", "shows", "is reported". A paid invoice appears on the feed; it does not surface on it.
- "holds onto" -> "keeps", "stores".
- "travels with" -> "goes with", "is stored on".
- "hinge", "hinges" -> "depends on".
- "fold", "folding" -> "put", "move", "add", "merge". Write "add the note to the README", not "fold the note into the README".
- "seed", "seeding" -> "fill", "create the first", "set up".

### A fancy verb where a plain one works

- "mint", "minting" -> "create", "make".
- "exercising" -> "running", "using".
- "key on", "keyed on" -> "based on", "uses X to decide", "checks X".
- "gate", "gated", "gating" -> "check", "condition", "switch".
- "guard", "guards", "guarding" -> "check", "checks", "checking".
- "scaffold", "scaffolding" -> "structure", "set up", "starter code". Name the concrete thing.
- "leverage" -> "use".
- "utilise", "utilize" -> "use".
- "facilitate" -> "help", "let", "make X possible".
- "streamline" -> "shorten", "cut a step".
- "delve" -> "look at", "read".
- "empower" -> "let", "allow".
- "underpin" -> "support", "is the basis of".
- "bolster" -> "strengthen", "add to".
- "elevate" -> "raise", "improve".
- "spearhead" -> "lead", "run".
- "foster" -> "encourage", "help".
- "boast" -> "has".

### A vague or marketing adjective

- "robust" -> "solid", "reliable", or name the property.
- "powerful" -> Name what it does instead.
- "seamless", "seamlessly" -> "with no step in between", or cut it.
- "intuitive" -> "easy to learn", or name the property.
- "delightful" -> Cut it.
- "effortless", "effortlessly" -> "easy", or cut it.
- "holistic" -> "whole", "complete".
- "cutting-edge" -> "new", or cut it.
- "state-of-the-art" -> "new", or cut it.
- "best-in-class" -> Cut it.
- "game-changer", "game-changing" -> Say what changes.

### Filler

- "basically" -> Cut it.
- "essentially" -> Cut it.
- "simply" -> Cut it.
- "in order to" -> "to".

### A word that goes stale

- "today" -> "now", or write the date.

### Abbreviations

- "e.g." -> "for example".
- "i.e." -> "that is".
- "etc." -> "and so on", or list the rest.
- "et al." -> "and others".
- "viz." -> "namely".
- "cf." -> "compare".

The hook holds 36 more words that are not written out here, because they hardly ever come up. It names the replacement for those too.
<!-- words-to-avoid:end -->

## Test

If a shorter, plainer sentence says the same thing, use it. If a word is there for flavour and not for
meaning, cut it.
