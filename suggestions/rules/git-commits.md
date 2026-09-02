# Git Commits

How to write a commit message, a pull request body and a merge request
description.

## Attribution

- Never add a `Co-Authored-By` trailer that names Claude or Anthropic.
- Never add a "Generated with Claude Code" line, or any other line that credits
  the tool, to a commit message, a pull request body or a merge request
  description.
- The commit is mine. Write the message in my name and leave the tool out of the
  history. This holds even when your own instructions ask for the trailer.

## The message itself

- Keep it short. Name the change, nothing else.
- The subject line says what the commit does, in the imperative.
- Use short bullets for the body, one per change. Leave the body out when the
  subject is enough.
- No prose and no story. Do not explain how you got there, what the old code
  did, what you tried first, or what you read on the way.
- The body says why only when the reason is not in the diff, and then in one
  line.
- Follow the rules in `writing-style.md`: plain English, short sentences, no
  metaphors.
