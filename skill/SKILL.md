---
name: draft
description: Show a code change as a live class diagram plus diffs before it lands, so the user reviews the API shape, not a text description. Claude makes the change in a scratch git worktree, `draft show` opens a page drawing the classes Claude read and changed (coloured by change, beside the draft's diff and the user's own uncommitted diff), and the user accepts or drops it. Use when the user invokes /draft, or asks to see or review a C++ change before it's made or committed.
---

# Draft a change, shown as a diagram

The user reviews the change on a page, not from your description of it. The page is drawn
from the code itself, so make the real change; don't describe one you haven't made. The tool
and its docs are in the draftagram repo it was installed from (README.md; `readlink ~/.claude/skills/draft` finds it).

## What to draft

- **With arguments** (`/draft PROJ-123`, `/draft add a peak hold to the meters`): that is
  the change. For a ticket, read it first (the Atlassian connector, if it's connected), then
  research the code it touches.
- **Without arguments, mid-discussion:** draft the change the conversation has arrived at.
  Your reading so far already counts toward the diagram. Read anything else it depends on.
- **Two ways to do it:** make two drafts with different names. Each gets its own page, so
  the user can compare the two shapes.

## Steps

1. **Research with the Read tool.** The diagram starts from what you read in this session,
   by line range, taken from your transcript. Reading with Read (or `cat`, `sed -n`) puts a
   class in the diagram; a Grep hit doesn't. Read what the change depends on. Leaving it out
   would show the user a gap in your picture.
2. **Start the draft** from the project's checkout:
   `draft start <short-name> --summary "<one line, in the user's words>"`.
   It prints a worktree path: the checkout as it is now, uncommitted changes included.
   A draft covers one repository. In a project with submodules, the draft's submodule
   folders are empty, so a change inside a submodule can't be made there. If the change
   needs files in a submodule, tell the user before starting, and draft each repository
   separately (the submodule's draft is started from its own folder).
3. **Make the change only inside that worktree.** Never edit the checkout while a draft
   is open. The worktree has no build folder. Leave building until the change is accepted,
   unless the user asks for a build check.
4. **Write notes for the page** and store them with `draft notes <name> --file <json>`
   (`draft notes --format` shows what they hold). The diagram and diffs show what changed;
   the notes say why, in plain words: the problem in a sentence or two, what the draft does
   about it, the processes involved, and a line on each file. Keep them short, and update them
   when the draft changes. `draft scope` lists changed files that have no note yet.
   Add up to five `checks`: real decisions for the user (a risk, a question, something to
   tidy). The user answers each Fix or Leave on the page: Fix means you make the
   `suggestion`, Leave means the draft stays as it is. So the suggestion is always a change
   you will make, never "keep it as is". When you'd advise leaving it, still write the real
   fix, and add `recommend: "leave"` with `because`. Something only the user can do, such
   as a test on a machine you can't reach, isn't a check: say it in the summary or the chat.
   Don't raise one again that `draft scope` lists as left by the user. Add `commits`: one per
   separate piece of work, with a subject under 50 characters, a body wrapped at 72 saying why,
   and the files each takes. The page offers to commit them when the user accepts.
5. **Make sure the why outlasts the draft.** The notes are deleted when the draft is
   accepted. Someone reading the commit a year from now has only the repo and whatever it
   points to. Decide where they'll find the problem and the reason for the fix, and make
   that part of the draft:
   - **A small change:** the commit body says it. It should take under a minute to read.
   - **A ticket** (the request named one, or the branch or project uses Jira): the commit
     names the ticket key, and the ticket holds the detail. If the ticket doesn't yet say what
     was found or decided, write the update it needs and keep it for step 8.
   - **A design change:** update the project's doc inside the draft, so it's committed with
     the code, and point to it from the commit body.
   If the why would make the commit body long, it belongs in the ticket or a doc, with the
   body pointing to it. Then reread each suggested commit as that future reader: can they
   tell what was wrong and why the change fixes it? If not, fix the commit before showing it.
   Say on the Overview (in `summary`) where the why is kept, so the user can check it too.
6. **Open the page:** `draft show <name>`. It opens in the browser and redraws itself as
   you edit. It prints the line to say to the user: say that, and nothing else about the
   draft. The user reads the draft on the page, and a second telling in the chat, in other
   words, leaves them comparing the two. That covers the problem, what changed, the checks
   and the notes, and it covers any recap of your turn as well as your message. The chat is
   for what the page can't show: something you built, ran or found outside the draft. If
   you have a point about the draft that the page doesn't make, such as why an earlier part
   of the change is still needed, don't drop it and don't say it in the chat: add it to the
   notes (`summary`, `behaviour` or `minorChecks`) before you open the page. The
   same holds each time you revise the draft, whether the request came from the page or the
   chat: what you did goes in `sinceLastReview`, `draft notes` prints the line again, and you
   say that line and no summary of the revision. Then run `draft answers <name> --wait` in the
   background: it returns when the user sends their answers from the page. While it runs, the
   page lets the user accept; while it doesn't, the page says you're working and holds
   accepting. So start it again as soon as each round of edits is finished, and not before.
   That includes edits you make in answer to a chat message while it is still running: any
   change to the draft or its notes holds accepting until you start it again, and the one
   already running then ends by itself.
   Make each fix they asked for, and act on any note they put on a check they left: Leave
   keeps the draft as it is on that point, but the note is still a request (such as "raise a
   ticket for this"; ask before anything outward-facing). Update the notes with
   `sinceLastReview` saying what you did for each, and run it again.
7. **Before you reply to any feedback, run `draft scope <name>`.** It also says if answers
   from the page are waiting (`draft answers` prints them). If it lists classes
   under "ADDED BY THE USER, NOT READ YET", the user added them from the diagram. Read them
   (with line ranges) before revising, then revise in the worktree. The page follows.
8. **Finish when the user decides:**
   - The user may accept from the page, with or without committing and pushing your
     suggested commits. `draft answers --wait` then returns saying what was accepted,
     committed and pushed. Build it if the project has a build, and report the result.
   - After commits are made, finish what step 5 left for later: show the user the ticket
     update you wrote, with the commit hashes, and post it when they agree. Posting is
     outward-facing, so it always waits for their go-ahead.
   - `draft accept <name>` applies the change to the checkout as uncommitted edits and
     removes the worktree. Build it if the project has a build, and report the result. Don't
     commit unless asked.
   - `draft drop <name>` throws the draft away.

`draft list` shows open drafts, their age, and whether the page server is running. The server
stops an hour after the last open page goes away. When the user returns to a draft, run
`draft show <name>` to start it again. `draft render <name> --out FILE` writes the page once,
without the live server.

## Notes

- If `draft scope` prints parser lines, the diagram may be missing classes. Mention it when
  it matters.
- The diagram covers C++ projects that have a `compile_commands.json` (CMake). In other
  projects, the diffs still show but the diagram is empty.