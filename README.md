# draftagram: the `draft` tool

See a change Claude drafts as a class diagram and code diffs, side by side, before you
accept it. The idea comes from Together's simultaneous round-trip engineering:
the diagram is never a separate model, it's drawn from the code each time. So what you
review is the code Claude actually wrote, drawn two ways, not Claude's description of it.

Run from Claude with the `/draft` skill (`skill/SKILL.md`), or by hand with `draft --help`.

## Install

For macOS with Claude Code. Claude can follow these steps as written.

1. **Requirements.** `uv` (`brew install uv`) builds the tool's Python environment, with its
   one dependency, the libclang bindings. Apple's Command Line Tools
   (`xcode-select --install`) or Xcode provide libclang itself. The diagram needs C++ projects
   with a CMake `compile_commands.json`: set `CMAKE_EXPORT_COMPILE_COMMANDS=ON`, or use CLion,
   whose `cmake-build-debug` folder usually already has one.
2. **Clone** this repo somewhere permanent, for example
   `git clone git@github.com:daveelton/draftagram.git ~/Development/draftagram`.
3. **Run the install script:** `~/Development/draftagram/install.sh`. It builds a Python
   environment for the tool in `.venv` inside the repo, writes the `draft` command to
   `~/.local/bin` (which must be on `PATH`), and links the skill into `~/.claude/skills`, so a
   `git pull` updates both. Run it again after moving the repo, or after a pull that adds a
   dependency.
4. **Check.** `draft --help` prints the commands. In a new Claude Code session, `/draft` is
   then available.

The `draft` command runs the environment's Python directly and doesn't go through `uv`. `uv`
opens its shared cache in `~/.cache/uv` each time it runs, which Claude Code's Bash sandbox
refuses, and allowing that write would let a sandboxed command change packages other projects
use. Installed this way, every `draft` command runs sandboxed except `draft show`, which
starts the page server on a local port and opens the browser: Claude runs that one command
outside the sandbox. The other commands never contact the server. They read the heartbeat it
keeps in the draft's `server.json`, because the sandbox refuses a command inside it both a
connection to a local port and a signal to a process outside.

Nothing else is changed: there's no hook, and Claude Code's settings aren't touched. Each
project gets a `temp/draft/` folder while a draft is open, and `draft start` adds `temp/` to
the project's `.gitignore` if it isn't there.

## Where a draft is made

`draft start` makes the draft in one of two places:

- **In a worktree** (`--worktree`): a scratch copy of your checkout, uncommitted changes
  included. Your checkout stays as it is until you accept. The copy has no build folder, so
  the draft can't be built or tested before then.
- **In place** (`--in-place`): in your checkout. You see the draft in your IDE as ordinary
  uncommitted edits, and can build and test it while you review it on the page. Accepting
  then only closes the draft, since the change is already there. Dropping puts every file
  back as it was when the draft started.

Run with neither flag, `draft start` starts nothing. It prints the checkout's uncommitted
changes and the question for Claude to ask you, and Claude runs it again with your answer.
The question recommends in place when the checkout is clean, since the IDE's uncommitted
diff is then exactly the draft. It recommends the worktree when you have uncommitted changes,
since in place they would sit mixed with the draft's in the IDE (the page still shows the two
apart). Say which you want in your request ("draft this in place") and there's no question.

What differs for a draft in place:

- **Anything edited in the checkout while it is open is part of the draft,** whoever edits
  it. "Your uncommitted change" on the page is fixed at what you had when the draft started.
- **Dropping it also takes out edits you made meanwhile.** So `draft drop` first writes the
  whole change it removes to `temp/draft/<name>.dropped.patch`, which `git apply` puts back.
- **A build can catch Claude part-way through an edit.** The page says when Claude is working.
- **It needs the checkout to itself.** No other draft can be open beside it, so comparing two
  ways to make a change takes two worktree drafts.
- Your staged files are left alone. The draft is diffed through a git index of its own
  (`temp/draft/<name>/index`), and the snapshot it starts from is kept by the ref
  `refs/draft/<name>`. Both go when the draft is accepted or dropped.

## What you see

`draft show` opens a page in the browser, served on localhost.

The page is where the draft is explained. When it opens, `draft show` gives Claude one line to
say in the chat: that the page is open, its headline, and whether the checkout has changed
(not for a worktree draft; for one in place, that the draft is there to build and test). Claude doesn't retell the draft there, so there is one account of it to read. The
chat is for what the page can't show, such as a build Claude ran.

- **Diagram, on the left.** The draft's classes and members are coloured: green added, red
  removed (struck through), amber changed. A member counts as changed when its declaration
  differs or when the diff touches its declaration or any of its bodies, including bodies in
  the `.cpp`. Members are listed under `public:`, `protected:` and `private:` headings, and
  a base class the draft adds or removes is listed in green or struck out in red. Blue marks
  what your own uncommitted diff changed. A member in grey text is one
  Claude didn't read. A class with an orange border is one Claude hasn't read at all.
  Edges: hollow arrow for a base class, diamond at the owner for held by value, dashed for
  pointers, references and smart pointers (`OwnedArray<Foo>` counts), dotted for use in a
  signature. A relation the draft adds or removes is drawn green or red.
- **Moving around the diagram.** Pinch or ⌘-scroll zooms around the pointer, dragging moves
  it, and "full screen" fills the window with it (Esc closes). It starts fitted to the panel's
  width, but never below 50% so the text stays readable, and keeps its zoom and position
  when the page redraws.
- **Side panel, on the right.** Tabs for the drafted change (the draft against where it
  started), your uncommitted change (the checkout against HEAD; for a draft in place, as it
  was when the draft started), and the code of
  whatever you clicked. Clicking a class or member jumps to its first changed line. Diffs show
  8 lines around each change, and "whole functions" shows the whole function instead. A file
  with a long diff starts folded (over 600 lines, or over 150 when the whole diff is over 3000).
- **Header.** A text summary of the API changes in each diff, and how the scope was chosen.
- **Claude's notes**, in violet wherever they appear, since they are Claude's words and not
  read from the code. The Overview tab opens with the problem the draft fixes (or what it
  adds) in a sentence or two, then the bullets saying how the draft does it, a before/after
  table and a sequence diagram. Each process the notes name gets its own colour, for a box
  round its classes on the diagram and a label on its files. The draft's diffs carry a note on
  each file and on lines that need one, and the diagram draws links the code can't show, such
  as an HTTP call. Claude stores them with `draft notes`; without them the page is as before.
- **Checks.** Up to five decisions Claude wants from you, as cards at the end of the Overview:
  each says what could go wrong and what Fix will change, with Fix and Leave buttons and an
  optional note. Fix always means Claude makes that change, and Leave that the draft stays as
  it is. Where Claude would leave it, the card says "Claude recommends Leave" and why, and
  outlines that button; `draft notes` refuses a suggestion that reads as leaving it. A note on
  a check you leave is a request ("raise a ticket for this"): Claude acts on it, though the
  draft stays as it is on that point. Answers are saved as you click, so the page's reloads
  keep them. Asking for a fix, or writing a note on a check you leave, turns the main button
  into "Send 1 fix and 1 note to Claude" and hides the accept buttons, so a request reaches
  Claude before anything is committed. `draft answers --wait`, which Claude runs in the
  background, prints them. Checks you leave are remembered for the draft, listed folded on
  later rounds, and Claude doesn't raise them again. The next round opens with what Claude
  did for each fix and note.
  Every POST to the page server carries a token that only the page it served knows, so another
  web page in the browser can't post to it. The server also answers only requests addressed to
  `127.0.0.1` or `localhost` by its own port. Without that, a web page could point a host name
  of its own at 127.0.0.1 (DNS rebinding), load this page with its token, and accept and push.
- **Accepting from the page.** The bar under the side panel holds the commits Claude suggests
  (folded; each subject and message can be edited) and three buttons: "Accept, commit, push",
  "Accept and commit" and "Accept only". With the messages folded, the first click on either
  commit button only opens them, so a commit is never made from a message you haven't seen;
  click again to go ahead. Drag the divider above the bar to make it taller or shorter: the
  room goes to the message bodies, and the height is kept as the page redraws. So are your
  edits to the messages, for as long as the tab is open. If Claude changes a suggestion you
  have edited, your edit stays, with a link to switch to Claude's new version. Each commit's
  files show as one line ("4 files in 3 folders") that opens into a tree, with files that also
  have your own uncommitted changes marked in blue. Asking for a fix, or a note on a check you
  leave, replaces the buttons with "Send … to Claude". While Claude is working on the draft,
  the accept buttons are held, with "Claude is working" under them, so a half-made change
  can't be accepted. Claude counts as working whenever its `draft answers --wait` isn't
  running, or the draft has changed since that wait started (Claude also edits in answer to
  chat messages, with the wait still running). The wait leaves `waiting.json` in the draft's
  folder, with its process id and a stamp of the draft's diff and notes, and the server
  refuses an accept unless that wait is alive and the stamp still matches. A new wait takes
  over from one still running, which then ends. Sending answers isn't held; they wait
  for Claude's next round. If Claude isn't waiting and Claude's session transcript has been
  quiet for 3 minutes, or can't be found, the session has probably ended, and the page offers
  "Accept anyway". Accepting is also refused if the draft changed after the page was drawn,
  so what is accepted is what you saw, and a commit takes only files the draft changed. Each commit takes
  its files as they are after accepting, so your own uncommitted changes in those files go in
  too: the bar lists any such files. Push uses the branch's upstream, shown top right of the
  page. The page server runs git itself, with your credentials: Claude only suggests the
  commits. What happened (the files accepted, the commits made, whether the push worked, and
  your answers to the checks) is written beside the draft's folder and reported to Claude by
  `draft answers --wait`, so it can finish the work the commits leave, such as updating the
  ticket. The record is started, marked as accepting, before the draft is removed, so the wait
  doesn't mistake an accept in progress for a draft that was dropped.

The page polls the server and redraws whenever the draft, your diff, the scope or Claude's
reading changes. As Claude edits the draft, the page follows.

The server stops itself an hour after the last open page goes away, and an open page then
says so. Run `draft show` to start it again. The draft itself stays until it's accepted or
dropped. A worktree draft costs only disk: a worktree without a build folder. `draft list`
shows each draft's age, whether it is in place, and whether its server is running.

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
  it read, not of the whole class. There are at most 24 ghosts, taken from each class in
  scope in turn (its bases first, changed classes choosing first), so one class with many
  neighbours can't fill every place. Additions are stored in the draft's `scope.json`, and
  `draft scope` lists them as not yet read, so Claude reads them before revising. That
  keeps Claude's understanding in step with the diagram, not just the picture.
- Classes your own diff changed outside the scope are offered as chips in the header.

## How it works

```
 Draft (draft_tree.py)       HEAD + a "snapshot" commit of your uncommitted changes, then
                              Claude's edits on top. Worktree: temp/draft/<name>/tree on
                              branch draft/<name>. In place: your checkout, with the
                              snapshot kept by refs/draft/<name>
        │ three states as file overrides: HEAD, working tree, draft
        ▼
 ApiModel (api_model.py)     Apple's libclang parses each unit with the project's
                              compile_commands.json flags, swapping in each state's file
                              contents (unsaved files), so the paths and flags are the same
                              for every state
        ▼
 ApiDiff (api_diff.py)       per class and member: added / removed / changed
 DraftView (view.py)         scope, ghosts; UnitCache re-parses only the units whose included
                              files differ between states or redraws
        ▼
 Notes (notes.py)            Claude's notes (JSON, `draft notes`): overview, processes,
                              file and line notes, links
 DraftPage (page.py)         Graphviz DOT with HTML-table nodes, drawn in the browser by
                              viz.js (from a CDN); diffs rendered with a line anchor per row
 DraftServer (draft.py)      localhost page, /stamp for live reload, /expand and /full
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
- A draft's worktree isn't configured for building. Build after accepting, or make the
  draft in place.
- Templates appear under their template name. Free functions and enums aren't drawn.
- C++ only. Other files show in the diffs but not the diagram.
- Accepting a worktree draft uses `git apply`. If the checkout has changed in the same lines
  since the draft started, accept refuses and says why. Rebase by dropping and starting again.
- One repository per draft. In place, a change inside a submodule shows only as a moved
  submodule pointer, not as a diff. `git worktree add` doesn't check out submodules, so in a
  worktree draft of the parent repo the submodules' folders are empty: a draft can't change files inside a
  submodule, and your uncommitted work inside one isn't copied into the draft. Classes in a
  submodule still appear in the diagram, read from your checkout. A change to a submodule
  needs its own draft, started from the submodule's folder; there the diagram is likely
  empty unless the submodule has its own `compile_commands.json`, and `draft start` adds
  `temp/` to the submodule's `.gitignore`. Drafts that span the parent and its submodules
  (a worktree per repo, submodule commits first, then the parent's pointer commit, and
  pushes in that order) aren't built.

Ideas not yet built, with their reasoning, are in `future-ideas.md`.

## Open: leftover drafts

A draft's server stops by itself, but the draft (its worktree and `draft/<name>` branch, or
for one in place its hold on the checkout) stays until someone accepts or drops it, so forgotten drafts pile up. The likely fix is a `SessionStart`
hook that runs `draft list` for the session's project. Text a SessionStart hook prints goes
into Claude's context, so Claude would open with "this project has 2 drafts, the oldest 9 days
old: keep or drop?" That keeps clear-up a question for the user, never an automatic delete.
To be settled after some real use: the age that counts as left over, and whether drafts from
the session being resumed should be mentioned.

## Checking it works

In a CMake project, after reading a class in a Claude session:

```
draft start demo --worktree && (edit a method body and add a member in the printed worktree)
draft scope demo      # lists the class under "Changed by the draft", with both edits
draft show demo       # amber method, green member; clicking them jumps to the diff
draft drop demo
```

The same with `--in-place`, editing the checkout: `draft scope` and the page show the same,
and after `draft drop demo`, `git status` is as it was before `draft start`. `draft start
demo` with neither flag prints the question and exits with status 2.
## Licence

MIT, copyright UrsaDSP / Perambulum Ltd. See `LICENSE`.
