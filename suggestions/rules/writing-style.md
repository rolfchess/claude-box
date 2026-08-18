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

## Words to avoid, and what to use instead

- "appetite" (for effort or work) -> "time", "worth it", "want to do X". Appetite is for food, not code.
- "minting" -> "creating", "making".
- "exercising" (code paths, options) -> "running", "using".
- "keyed on" / "key on" -> "based on", "uses X to decide", "checks X".
- "carry" / "carries" (a value, provider, flag on a row or object) -> "has", "stores", "holds". A column stores a value; it does not carry it.
- "pin" / "pinned" (a value onto a row or object) -> "set", "store", "fix". Write "set the provider on the order", not "pin the provider onto the order".
- "surface" / "surfaces" (a value on a feed, response or object) -> "appears", "shows", "is reported". A paid invoice "appears on" the feed; it does not "surface on" it.
- "fold" / "folding" (one thing into another) -> "put", "move", "add", "merge". Write "add the note to the README", not "fold the note into the README".
- "scaffolding" / "scaffold" -> "structure", "set up", "starter code". Name the concrete thing.
- "escape hatch" (a way to override or opt out) -> "a way to override it", "an exception". No euphemisms.
- Any fancy or figurative verb where a plain one exists. A column, order, row or object does not "carry", "pin", "hold onto", "own" or "travel with" a value — it "has" or "stores" one.

## Test

If a shorter, plainer sentence says the same thing, use it. If a word is there for flavour and not for
meaning, cut it.
