# Future ideas

Not built yet. When one is built, describe it in README.md and delete it here.

## Ideas

- **Ask Claude from the page.** A box on a class, member, file or diff line sends a question
  to the Claude session that made the draft, and the answer appears beside what was asked
  about. It can travel the same way as the Fix and Leave answers, through
  `draft answers --wait`.
- **Echo the chat on the page.** Show the session's messages since the draft started. The
  tool already reads the session's transcript for the diagram.
- **Show the page beside the chat,** in the Claude desktop app's browser pane instead of a
  separate browser.

## Known issues

- **The chat can retell the draft.** The page is meant to be the one account of a draft.
  `draft show` and `draft notes` print the one line Claude is to say, and the skill tells
  Claude to say only that, but it depends on Claude following it. A question that changes
  nothing is still answered in the chat, away from what it is about.
- **The wait can fail on a git lock straight after `draft show`,** and the page then doesn't
  open. `draft answers --wait` exits with "Unable to create … index.lock"; running it again
  works. The likely cause is the page server and the wait both running `git add -A` on the
  draft's own index at the same moment. Candidate fixes: retry in `git()` (`draft_tree.py`)
  when the error names `index.lock`, or have only the server stage the tree. Reproduce it
  first in a large repository, with an in-place draft.
