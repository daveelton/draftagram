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
3. **Make the change only inside that worktree.** Never edit the checkout while a draft
   is open. The worktree has no build folder. Leave building until the change is accepted,
   unless the user asks for a build check.
4. **Open the page:** `draft show <name>`. It opens in the browser and redraws itself as
   you edit. Tell the user it's open, and give them the change in two or three product-level
   sentences. The page carries the detail.
5. **Before you reply to any feedback, run `draft scope <name>`.** If it lists classes
   under "ADDED BY THE USER, NOT READ YET", the user added them from the diagram. Read them
   (with line ranges) before revising, then revise in the worktree. The page follows.
6. **Finish when the user decides:**
   - `draft accept <name>` applies the change to the checkout as uncommitted edits and
     removes the worktree. Build it if the project has a build, and report the result. Don't
     commit unless asked.
   - `draft drop <name>` throws the draft away.

`draft list` shows open drafts, their age, and whether the page server is running. The server
stops 10 minutes after the last open page goes away. When the user returns to a draft, run
`draft show <name>` to start it again. `draft render <name> --out FILE` writes the page once,
without the live server.

## Notes

- If `draft scope` prints parser lines, the diagram may be missing classes. Mention it when
  it matters.
- The diagram covers C++ projects that have a `compile_commands.json` (CMake). In other
  projects, the diffs still show but the diagram is empty.