# Future ideas

Ideas for draftagram that came out of using it, with the reasoning, so they can be picked up
later. When one is built, move what's lasting into README.md and delete it here.

## Ask Claude from the page

**The need.** While reviewing a draft, the questions that come up are about the code itself:
what a method is for, what the original code did, and why the draft changes it. Today the
reviewer has to switch to the terminal and describe what they're looking at in words, which
is exactly the gap the page was built to close.

**The idea.** An "Ask Claude" box on a class, member, file or diff line. The question goes to
the Claude session that made the draft along with what it's about (the class and member, or
the file and line in the draft's version). The answer appears on the page beside that thing.

**How it would fit.** It can use the same route as the Fix/Leave answers: the page saves the
question with the server, and `draft answers --wait`, which Claude already runs in the
background, returns with it. Claude answers in the notes (a new field, say `answers`, keyed
by what was asked about), and the page redraws with the reply. There is still only one Claude,
the session that ran `/draft`, so it answers with everything it read and decided while
drafting.

**To work out.** Whether answers that turn out to matter (a method's purpose, a decision)
should be offered for the commit body or the ticket, so the next reader gets them too (see
step 5 of the skill). How a question that changes Claude's mind becomes a new round of the
draft rather than only an answer.

## Known issue: the chat and the page both brief the user

**What happens.** When a draft opens, the user gets the change explained twice at the same
moment, in different words: once on the page's Overview, and once in the chat message that
announces the page. In the desktop app there is a third version, the recap in the next-step
box. Each is written separately, so the user has to read all of them to find out whether
they say the same thing.

Seen in a session on a large project (11 Nov 2026). The Overview
held the problem, four summary lines, one check and two minor notes. The chat message that
opened the page restated every one of them under its own headings ("What it changes", "What
to know before accepting"), reworded, about 250 words where step 6 of the skill asks for two
or three sentences.

**The second fork.** Questions and requests go back through the chat, so a review round
splits across two places. In the same session the request to "find the pack's copy" and the
final "I accept that, please commit" were both typed in the chat, and Claude's reply and the
page's `sinceLastReview` then described the same revision twice.

**The rule the fixes share.** Each piece of information has one home. The page owns
everything about the draft: the problem, what changed, the checks, what was revised. The
chat owns what is outside the draft: that the page is open, what was built, tested, committed
or pushed. A chat message about the draft's content is a pointer to the page, not a second
telling.

**Candidate fixes, smallest first.**

1. *The chat hands over and stops.* Built: `draft show` prints the line Claude is to say
   (`Draft.hand_over`), and step 6 of the skill tells Claude to say that and nothing else
   about the draft. It depends on Claude following it, so check the next few drafts: if the
   chat still retells the draft, the instruction needs to be firmer or the fix mechanical.
2. *A revision points back.* Built: when Claude revises the draft, for a Fix sent from the
   page or a request in the chat, the detail goes in `sinceLastReview`, `draft notes` prints
   the hand-over line again, and the skill tells Claude to say that and no summary. Seen
   missing in the same session: a Fix sent from the page was answered with a
   200-word account in the chat of what the page's "Since your last review" already said.
   A point about the draft that the page doesn't make goes in the notes, not the chat (in
   that session, "the folder fix is still needed" was said only in the chat).
   Not covered: a question that changes nothing is still answered in the chat, away from
   what it is about.
3. *Ask Claude from the page* (the idea above). Questions and answers stay on the page, so
   the user has no reason to leave it during a review. This is what closes the second fork.
4. *Echo the chat on the page.* The tool already reads the session's transcript for the
   diagram, so the page could show the session's messages since the draft started. With 3 it
   becomes a two-way conversation on the page. On its own it moves the second voice onto the
   page without removing it, so it only helps once 1 has made the chat messages short.
5. *Show the page beside the chat.* Open the page in the desktop app's browser pane, not an
   external browser. Nothing is said less often, but both are in one window, and switching to
   ask a question costs a glance, not a change of app.

**Not possible.** Embedding the app's own chat in the page: the page is a local web page and
has no way to host the app's conversation. Only an echo (4) or a separate route (3) can put
conversation there.

## Known issue: the wait fails on a git lock, and the page doesn't open

**What happens.** Straight after `draft show`, the background `draft answers <name> --wait`
exits with code 1 and this message, and the page doesn't open in the browser:

```
git add -A failed:
fatal: Unable to create '<project>/temp/draft/<name>/index.lock': File exists.

Another git process seems to be running in this repository, or the lock file may be stale
```

Seen in a session on a large project in place (13 Nov 2026), and
the user has seen it before. The lock file was gone two seconds later, and a second
`draft answers --wait` then ran normally. `draft list` reported "page server on port 59330,
page open" throughout, although the user had no page.

**What is known.** The lock is on the draft's own index (`Draft.index_path`, used through
`GIT_INDEX_FILE` for an in-place draft), not the project's. Two draft processes were running
at that moment: the page server started by `draft show`, and the wait. Both stage the tree
with `git add -A` to work out the draft's diff, so the likely cause is the two meeting on
that index. `git()` in `draft_tree.py` treats any git failure as fatal, so the one that
loses exits.

**What isn't known.** Why the page didn't open. The server was up and answering, so either
the browser was never asked to open it, or the server's first request hit the same lock and
the page was left in a state the browser showed as nothing. Nor is it known whether a
worktree draft can hit this: it has no index of its own, but two processes still run git in
the same worktree.

**Candidate fixes, smallest first.**

1. *Try again.* In `git()`, when stderr names `index.lock`, wait a moment and retry a few
   times before giving up. The lock is held for well under a second.
2. *One process stages.* Only the server runs `git add -A` on the draft's index; the wait
   asks the server for the stamp it needs, as it already asks it for the answers.
3. *Say what to do.* Until one of those is built, the wait's message could say that the lock
   is the draft's own and that running the command again is safe. Claude restarts it either
   way, but the user sees a failed background command with no explanation.

**To check when fixing.** Reproduce it first: start an in-place draft in a large repository,
where `git add -A` takes long enough to overlap (its index is 600 KB), and run
`draft show` and `draft answers --wait` back to back. Then find out what the page server did
with the request that failed, which is the part that explains the page not opening.
