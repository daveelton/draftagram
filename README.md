# draft

See a change Claude drafts as a class diagram and code diffs, side by side, before it
lands in the checkout. The idea comes from Together's simultaneous round-trip engineering:
the diagram is never a separate model, it's drawn from the code each time. So what you
review is the code Claude actually wrote, drawn two ways, not Claude's description of it.

Run from Claude with the `/draft` skill (`skill/SKILL.md`), or by hand with `draft --help`.

## What you see

`draft show` opens a page in the browser, served on localhost.

- **Diagram, on the left.** The draft's classes and members are coloured: green added, red
  removed (struck through), amber changed. A member counts as changed when its declaration
  differs or when the diff touches its declaration or any of its bodies, including bodies in
  the `.cpp`. Blue marks what your own uncommitted diff changed. A member in grey text is one
  Claude didn't read. A class with an orange border is one Claude hasn't read at all.
  Edges: hollow arrow for a base class, diamond at the owner for held by value, dashed for
  pointers, references and smart pointers (`OwnedArray<Foo>` counts), dotted for use in a
  signature. A relation the draft adds or removes is drawn green or red.
- **Side panel, on the right.** Tabs for the drafted change (the worktree against where the
  draft started), your uncommitted change (the checkout against HEAD), and the code of
  whatever you clicked. Clicking a class or member jumps to its first changed line.
- **Header.** A text summary of the API changes in each diff, and how the scope was chosen.

The page polls the server and redraws whenever the draft, your diff, the scope or Claude's
reading changes. As Claude edits the worktree, the page follows.

The server stops itself 10 minutes after the last open page goes away, and an open page then
says so. Run `draft show` to start it again. The draft itself stays until it's accepted or
dropped. It costs only disk: a worktree without a build folder. `draft list` shows each draft's
age and whether its server is running.

## Scope: what the diagram includes

- **What Claude read in the session.** `ReadLog` (`reads.py`) reads the session transcript
  (`~/.claude/projects/*/<session>.jsonl`, plus `subagents/`) for Read calls and shell reads
  (`cat`, `head`, `tail`, `sed -n`), with their line ranges. Line ranges are mapped to
  members, so reading 40 lines of a large class brings in those members and collapses the
  rest under "N more". Search hits don't count. No hook is needed. The session id comes from
  `CLAUDE_CODE_SESSION_ID` when the draft starts.
- **What either diff changes.**
- **What you add.** Ghosts (dashed grey boxes) are classes one relation away. Click one to
  add it. For a class Claude only partly read, the ghosts are the neighbours of the members
  it read, not of the whole class. Additions are stored in the draft's `scope.json`, and
  `draft scope` lists them as not yet read, so Claude reads them before revising. That
  keeps Claude's understanding in step with the diagram, not just the picture.
- Classes your own diff changed outside the scope are offered as chips in the header.

## How it works

```
 Draft (draft.py)      worktree temp/draft/<name>/tree on branch draft/<name>:
                              HEAD + a "snapshot" commit of your uncommitted changes,
                              then Claude's edits on top
        │ three states as file overrides: HEAD, working tree, draft
        ▼
 ApiModel (api_model.py)     Apple's libclang parses each unit with the project's
                              compile_commands.json flags, swapping in each state's file
                              contents (unsaved files), so the paths and flags are the same
                              for every state
        ▼
 ApiDiff (api_diff.py)       per class and member: added / removed / changed
 DraftView (view.py)      scope, ghosts; UnitCache re-parses only the units whose included
                              files differ between states or redraws
        ▼
 DraftPage (page.py)      Graphviz DOT with HTML-table nodes, drawn in the browser by
                              viz.js (from a CDN); diffs rendered with a line anchor per row
 DraftServer (draft.py) localhost page, /stamp for live reload, /expand and /full
```

Parsing details that matter:

- It uses Apple's libclang (Command Line Tools, or Xcode). The `libclang` pip wheel's own
  library is older than the macOS SDK and fails on libc++. The wheel is installed only for its
  Python bindings, by `uv` from the script header.
- `-arch` flags are dropped (two arches make two jobs, which libclang refuses), and the SDK
  is added with `-isysroot`.
- A header is seen through the `.cpp` with the same name. Otherwise a probe unit includes it
  after `JuceHeader.h`, using the flags of the nearest source file.
- Bodies are skipped to keep parsing fast (about 2 seconds per JUCE unit), and that leaves a
  method's extent at its signature. `ApiModel._body_end` matches braces in the text to find
  where the body ends.
- Members written by macros (JUCE's leak detector and its deleted copy constructor) are
  dropped: their name isn't on their line, or the line is an all-caps macro call.

## Limits

- Viewing only. Editing the diagram to change the code, the other half of round-trip, isn't
  built.
- Classes that use a scope class are found only in units already parsed for the scope. A
  user elsewhere in the project can be missing from the ghosts.
- The worktree isn't configured for building. Build after accepting.
- Templates appear under their template name. Free functions and enums aren't drawn.
- C++ only. Other files show in the diffs but not the diagram.
- `accept` uses `git apply`. If the checkout has changed in the same lines since the draft
  started, accept refuses and says why. Rebase by dropping and starting again.

## Open: leftover drafts

A draft's server stops by itself, but its worktree and `draft/<name>` branch stay until
someone accepts or drops it, so forgotten drafts pile up. The likely fix is a `SessionStart`
hook that runs `draft list` for the session's project. Text a SessionStart hook prints goes
into Claude's context, so Claude would open with "this project has 2 drafts, the oldest 9 days
old: keep or drop?" That keeps clear-up a question for the user, never an automatic delete.
To be settled after some real use: the age that counts as left over, and whether drafts from
the session being resumed should be mentioned.

## Checking it works

In a CMake project, after reading a class in a Claude session:

```
draft start demo && (edit a method body and add a member in the printed worktree)
draft scope demo      # lists the class under "Changed by the draft", with both edits
draft show demo       # amber method, green member; clicking them jumps to the diff
draft drop demo
```