"""One draft: Claude's change, kept apart from the user's own until they accept it.

A draft is made in one of two places, chosen when it starts (its *mode*):

- **worktree:** a scratch git worktree, so the user's checkout is untouched until they
  accept. Its branch has one commit on top of HEAD, the *snapshot*: the user's uncommitted
  changes (tracked and untracked) when the draft started. Claude edits the worktree.
- **in-place:** the checkout itself, so the user can see the draft in their IDE and build
  and test it. The snapshot is a commit made without touching the checkout or the user's
  staging, kept alive by the ref refs/draft/<name>. Claude edits the checkout, and dropping
  the draft puts the checkout back to the snapshot.

Either way three states can be compared:

    HEAD  ->  snapshot (the user's own diff)  ->  draft (Claude's change)

In a worktree draft the user's diff is read live from the checkout, which they may go on
editing. In place it is fixed at the snapshot, and anything edited in the checkout after
that, by Claude or the user, is the draft.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import time

from notes import Notes
from reads import ReadLog

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")
WORKTREE, IN_PLACE = "worktree", "in-place"


def git(cwd, *args, check=True, input=None, env=None):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, input=input, env=env)
    if check and r.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed:\n{r.stderr.strip()}")
    return r.stdout


class Draft:
    def __init__(self, root, name):
        if not SLUG.match(name):
            raise SystemExit(f"draft name '{name}': use lower-case letters, digits and dashes")
        self.root = root
        self.name = name
        self.dir = os.path.join(root, "temp", "draft", name)
        self.scratch = os.path.join(self.dir, "tree")   # a worktree draft's worktree
        self.branch = f"draft/{name}"                   # and its branch
        self.index_path = os.path.join(self.dir, "index")   # an in-place draft's own git index
        self.ref = f"refs/draft/{name}"                      # and what keeps its snapshot
        self._mode = None
        self.meta_path = os.path.join(self.dir, "meta.json")
        self.scope_path = os.path.join(self.dir, "scope.json")
        self.notes_path = os.path.join(self.dir, "notes.json")
        self.answers_path = os.path.join(self.dir, "answers.json")
        self.waiting_path = os.path.join(self.dir, "waiting.json")
        # What happened when the page accepted the draft. Beside the draft's folder, not in it,
        # since accepting removes the folder; `draft answers --wait` reports it to Claude.
        self.outcome_path = os.path.join(root, "temp", "draft", f"{name}.outcome.json")
        # The change an in-place draft held when it was dropped, kept so a drop can be undone.
        self.dropped_path = os.path.join(root, "temp", "draft", f"{name}.dropped.patch")

    # ---------- where the draft is made ----------

    @property
    def mode(self):
        if self._mode is None:
            self._mode = self.meta.get("mode", WORKTREE)
        return self._mode

    @property
    def in_place(self):
        return self.mode == IN_PLACE

    @property
    def tree(self):
        """The folder Claude edits: the checkout itself, or the draft's worktree."""
        return self.root if self.in_place else self.scratch

    def _tree_git(self, *args, **kw):
        """git in the draft's tree. In place, it runs with the draft's own index, so staging
        the draft to diff it leaves what the user has staged alone."""
        env = {**os.environ, "GIT_INDEX_FILE": self.index_path} if self.in_place else None
        return git(self.tree, *args, env=env, **kw)

    def _changed(self, rev):
        """[(status, path)] of what differs between `rev` and the draft's tree as it is now."""
        self._tree_git("add", "-A")
        out = self._tree_git("diff", "--cached", "--name-status", "--no-renames", rev)
        return [tuple(l.split("\t", 1)) for l in out.splitlines() if "\t" in l]

    # ---------- finding drafts ----------

    @staticmethod
    def project_root(path="."):
        r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=path,
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit("not inside a git repository")
        top = r.stdout.strip()
        # Inside a draft's worktree, the project is the checkout that holds it.
        m = re.match(r"^(.*)/temp/draft/[^/]+/tree$", top)
        return m.group(1) if m else top

    @classmethod
    def all(cls, root):
        d = os.path.join(root, "temp", "draft")
        return [cls(root, n) for n in sorted(os.listdir(d))
                if os.path.exists(os.path.join(d, n, "meta.json"))] if os.path.isdir(d) else []

    @classmethod
    def find(cls, root, name=None):
        if name:
            p = cls(root, name)
            if not p.exists():
                raise SystemExit(f"no draft '{name}' (draft list)")
            return p
        props = cls.all(root)
        if len(props) != 1:
            raise SystemExit("name the draft: " + (", ".join(p.name for p in props) or "none yet"))
        return props[0]

    def exists(self):
        return os.path.exists(self.meta_path)

    @property
    def meta(self):
        return json.load(open(self.meta_path))

    def hand_over(self, url):
        """What Claude says in the chat when the page opens. The page explains the draft, so
        the chat only sends the user there, in the page's own words (its headline), and a
        second telling in the chat would give the user two versions to compare."""
        headline = self.meta.get("summary", "").strip().rstrip(".")
        headline = f" {headline}." if headline else ""
        where = ('"Since your last review" on its Overview says what changed'
                 if self.notes.data.get("sinceLastReview") else "Its Overview explains the change")
        checkout = ("The draft is in your checkout, so you can see it in the IDE and build and "
                    "test it; dropping it puts the checkout back as it was."
                    if self.in_place else "Nothing in the checkout has changed.")
        return (f"Say this to the user, and nothing else about the draft:\n"
                f'  The draft "{self.name}" is open at {url}.{headline} {where}, so read on '
                f"there. "
                f"{checkout}\n"
                f"The page tells the user about the draft; don't retell the problem, the change, "
                f"the checks or the notes in the chat or in a recap. Add only what the page can't "
                f"show, such as something you built or ran outside the draft.")

    # ---------- lifecycle ----------

    @staticmethod
    def checkout_changes(root):
        """The checkout's uncommitted changes as [(git's status letters, path)]; "??" is a
        file git doesn't track yet."""
        out = git(root, "status", "--porcelain", "--untracked-files=all")
        return [(l[:2], l[3:]) for l in out.splitlines() if not l[3:].startswith("temp/")]

    def in_the_way(self, mode):
        """Why a draft can't start in `mode` (None: not chosen yet) beside the drafts already
        open, or None if it can. An in-place draft needs the checkout to itself: a second
        draft would take the first one's edits as part of the user's own changes."""
        others = [d for d in Draft.all(self.root) if d.name != self.name]
        held = [d.name for d in others if d.in_place]
        if held:
            return (f"draft '{held[0]}' is open in place, in the checkout itself: "
                    f"accept or drop it before starting another")
        if mode == IN_PLACE and others:
            return ("a draft in place needs the checkout to itself, and these are open: "
                    + ", ".join(d.name for d in others) + ". Finish them, or use --worktree")
        return None

    def start(self, session_id, mode, summary=""):
        """Start the draft from HEAD plus the user's uncommitted changes, in a worktree or in
        the checkout itself."""
        if self.exists():
            raise SystemExit(f"draft '{self.name}' already exists: {self.tree}")
        blocked = self.in_the_way(mode)
        if blocked:
            raise SystemExit(blocked)
        self._mode = mode
        self._ensure_temp_ignored()
        for left_over in (self.outcome_path, self.dropped_path):    # from an earlier draft of the same name
            if os.path.exists(left_over):
                os.remove(left_over)
        head = git(self.root, "rev-parse", "HEAD").strip()
        os.makedirs(self.dir, exist_ok=True)
        snapshot = self._snapshot_checkout(head) if self.in_place else self._make_worktree(head)
        json.dump({"name": self.name, "mode": mode, "head": head, "snapshot": snapshot,
                   "session": session_id, "summary": summary, "started": time.time()},
                  open(self.meta_path, "w"), indent=2)
        json.dump({"expanded": []}, open(self.scope_path, "w"))

    SNAPSHOT_MESSAGE = "Snapshot of the working tree the draft starts from"
    AS_DRAFT = ("-c", "user.name=draft", "-c", "user.email=draft@localhost")

    def _make_worktree(self, head):
        """A worktree at HEAD with the user's uncommitted changes committed on top."""
        git(self.root, "worktree", "add", "-q", "-b", self.branch, self.scratch, head)
        patch = git(self.root, "diff", "HEAD", "--binary")
        if patch.strip():
            git(self.scratch, "apply", "--whitespace=nowarn", input=patch)
        for f in git(self.root, "ls-files", "--others", "--exclude-standard", "-z").split("\0"):
            if f:
                dst = os.path.join(self.scratch, f)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(os.path.join(self.root, f), dst)
        git(self.scratch, "add", "-A")
        git(self.scratch, *self.AS_DRAFT, "commit", "-q", "--allow-empty", "--no-verify", "-m",
            self.SNAPSHOT_MESSAGE)
        return git(self.scratch, "rev-parse", "HEAD").strip()

    def _snapshot_checkout(self, head):
        """Commit the checkout as it is, without changing it or what the user has staged."""
        # The draft's index starts as a copy of the user's. That brings git's record of which
        # files are unchanged, so staging the whole tree doesn't read every file again.
        user_index = os.path.join(self.root, git(self.root, "rev-parse", "--git-path", "index").strip())
        if os.path.exists(user_index):
            shutil.copy2(user_index, self.index_path)
        else:
            self._tree_git("read-tree", head)
        self._tree_git("add", "-A")
        tree = self._tree_git("write-tree").strip()
        snapshot = git(self.root, *self.AS_DRAFT, "commit-tree", tree, "-p", head, "-m",
                       self.SNAPSHOT_MESSAGE).strip()
        git(self.root, "update-ref", self.ref, snapshot)    # or git's clean-up could remove it
        return snapshot

    def accept(self):
        """Leave the draft in the user's checkout as uncommitted changes, and close it. A
        worktree draft is applied to the checkout first; one made in place is already there."""
        patch = self.draft_patch(binary=True)
        if not patch.strip():
            raise SystemExit("the draft has no changes")
        if not self.in_place:
            r = subprocess.run(["git", "apply", "--check", "--whitespace=nowarn"], cwd=self.root,
                               input=patch, capture_output=True, text=True)
            if r.returncode != 0:
                raise SystemExit("the checkout has changed under the draft; it doesn't apply:\n"
                                 + r.stderr.strip())
            git(self.root, "apply", "--whitespace=nowarn", input=patch)
        files = self._tree_git("diff", "--cached", "--name-only", self.meta["snapshot"]).split()
        self._remove()
        return files

    def commit(self, commits, push):
        """Commit each of `commits` ([{"subject", "body", "files"}]) in the checkout, only the
        files it lists, then push if asked. Returns (ok, ["hash subject"] made, git's output)."""
        log, made = [], []

        def run(*args, input=None):
            r = subprocess.run(["git", *args], cwd=self.root, capture_output=True, text=True, input=input)
            log.append(f"$ git {' '.join(args)}\n{(r.stdout + r.stderr).strip()}".strip())
            return r.returncode == 0

        for c in commits:
            body = c.get("body", "").strip()
            message = c["subject"].strip() + (f"\n\n{body}" if body else "") + "\n"
            if not (run("add", "--all", "--", *c["files"]) and
                    run("commit", "-q", "-F", "-", "--", *c["files"], input=message)):
                return False, made, "\n".join(log)
            made.append(git(self.root, "log", "-1", "--format=%h %s").strip())
        if push and not run("push"):
            return False, made, "\n".join(log)
        return True, made, "\n".join(log)

    # What happened when the page accepted the draft. The page server writes it twice: marked
    # "accepting" before the draft is removed, so `draft answers --wait` never sees a draft
    # gone with nothing said about it, then "done" once git has finished.

    def save_outcome(self, outcome):
        # Written by the page server and read by `draft answers`, in separate processes: write
        # a whole new file and rename it over, so neither reads one half-written.
        tmp = self.outcome_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(outcome, f, indent=2)
        os.replace(tmp, self.outcome_path)

    def read_outcome(self):
        try:
            return json.load(open(self.outcome_path))
        except (OSError, ValueError):
            return None

    def take_outcome(self):
        outcome = self.read_outcome()
        if outcome is not None:
            os.remove(self.outcome_path)
        return outcome

    def discard_outcome(self):
        if os.path.exists(self.outcome_path):
            os.remove(self.outcome_path)

    def drop(self):
        """Throw the draft away. In place, that puts the checkout back as it was when the
        draft started, and the change taken out is kept as a patch: returns its path, or None
        if there was nothing to keep."""
        kept = self._restore_checkout() if self.exists() and self.in_place else None
        self._remove()
        return kept

    def _restore_checkout(self):
        """Put the checkout back to the snapshot. The user may have edited it too while the
        draft was open, and those edits go with the draft, so the whole change is saved first."""
        patch = self.draft_patch(binary=True)
        if not patch.strip():
            return None
        with open(self.dropped_path, "w") as f:
            f.write(patch)
        snapshot = self.meta["snapshot"]
        changed = self._changed(snapshot)
        back = [f for status, f in changed if status != "A"]
        if back:
            self._tree_git("checkout", snapshot, "--", *back)
        for status, f in changed:
            if status == "A":       # a file the draft added
                os.remove(os.path.join(self.root, f))
                folder = os.path.dirname(f)
                while folder and not os.listdir(os.path.join(self.root, folder)):
                    os.rmdir(os.path.join(self.root, folder))
                    folder = os.path.dirname(folder)
        return self.dropped_path

    def _remove(self):
        """Remove the draft's records, whichever mode it was in. The checkout isn't touched."""
        git(self.root, "worktree", "remove", "--force", self.scratch, check=False)
        git(self.root, "branch", "-D", self.branch, check=False)
        git(self.root, "update-ref", "-d", self.ref, check=False)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _ensure_temp_ignored(self):
        r = subprocess.run(["git", "check-ignore", "-q", "temp/draft"], cwd=self.root)
        if r.returncode != 0:
            with open(os.path.join(self.root, ".gitignore"), "a") as f:
                f.write("\ntemp/\n")

    # ---------- the three states ----------

    def draft_patch(self, binary=False, context=3, whole=False):
        """The draft's diff. `whole` shows the whole function around each change."""
        self._tree_git("add", "-A")
        args = ["diff", "--cached", f"-U{context}", self.meta["snapshot"]]
        args += ["--binary"] if binary else []
        args += ["--function-context"] if whole else []
        return self._tree_git(*args)

    def current_patch(self, context=3, whole=False):
        """The user's uncommitted diff, untracked files included as additions. For a draft
        made in place, it is their diff as it was when the draft started."""
        extra = ["--function-context"] if whole else []
        if self.in_place:
            return git(self.root, "diff", f"-U{context}", *extra, self.meta["head"], self.meta["snapshot"])
        out = git(self.root, "diff", f"-U{context}", *extra, "HEAD")
        for f in git(self.root, "ls-files", "--others", "--exclude-standard", "-z").split("\0"):
            if f and not f.startswith("temp/"):
                r = subprocess.run(["git", "diff", "--no-index", f"-U{context}", *extra, "/dev/null", f],
                                   cwd=self.root, capture_output=True, text=True)
                out += r.stdout
        return out

    def commit_patch(self, context=3):
        """What a commit of the draft's files would hold: the diff from the last commit to the
        draft's tree, the user's own uncommitted changes and the draft's together."""
        self._tree_git("add", "-A")
        return self._tree_git("diff", "--cached", f"-U{context}", self.meta["head"])

    def state_files(self):
        """Overrides for the three states (HEAD, the user's own changes, the draft), each as
        {path in the checkout: text, or None if the file doesn't exist in that state}. A file
        with no override is read from the checkout. Paths are always the checkout's, so all
        states parse with the same compile flags."""
        head, snap = self.meta["head"], self.meta["snapshot"]

        def show(rev, f):
            r = subprocess.run(["git", "show", f"{rev}:{f}"], cwd=self.root, capture_output=True,
                               text=True, errors="replace")
            return r.stdout if r.returncode == 0 else None

        def before(changes, rev):
            """The files of `changes` as they were at `rev`."""
            return {os.path.join(self.root, f): None if status == "A" else show(rev, f)
                    for status, f in changes}

        drafted = self._changed(snap)
        draft_state = {}
        for status, f in drafted:
            p = os.path.join(self.tree, f)
            draft_state[os.path.join(self.root, f)] = \
                None if status == "D" else open(p, errors="replace").read()

        if self.in_place:
            # The checkout holds the draft, so the user's own state is the snapshot.
            return before(self._changed(head), head), before(drafted, snap), draft_state

        out = git(self.root, "diff", "--name-status", "--no-renames", "HEAD")
        head_state = before([l.split("\t", 1) for l in out.splitlines() if "\t" in l], head)
        for f in git(self.root, "ls-files", "--others", "--exclude-standard", "-z").split("\0"):
            if f:
                head_state[os.path.join(self.root, f)] = None
        return head_state, {}, draft_state

    def to_checkout_path(self, path):
        """A worktree path as the same file in the checkout."""
        prefix = self.scratch + os.sep
        return os.path.join(self.root, path[len(prefix):]) if path.startswith(prefix) else path

    # ---------- Claude's notes ----------

    @property
    def notes(self):
        return Notes.load(self.notes_path)

    def set_notes(self, data):
        json.dump(data, open(self.notes_path, "w"), indent=2)

    # ---------- the user's answers to Claude's checks ----------
    # answers.json: {"answers": {check id: {"decision": "fix" | "leave", "note"}}, the round
    # being answered; "sent": [answers sent to Claude and not taken yet]; "left": [checks the
    # user chose to leave on earlier rounds, which Claude shouldn't raise again]}

    def _answers(self):
        try:
            return json.load(open(self.answers_path))
        except (OSError, ValueError):
            return {}

    def _save_answers(self, data):
        # The page server writes this and `draft answers` takes from it, in separate processes:
        # write a whole new file and rename it over, so neither reads one half-written.
        tmp = self.answers_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, self.answers_path)

    @property
    def answers(self):
        return self._answers().get("answers", {})

    @property
    def sent(self):
        return self._answers().get("sent", [])

    @property
    def left(self):
        return self._answers().get("left", [])

    def save_answer(self, check_id, answer):
        """Record one answer as the user clicks, so a reload of the page keeps it. None clears it."""
        data = self._answers()
        answers = data.setdefault("answers", {})
        if answer:
            answers[check_id] = {"decision": answer["decision"], "note": answer.get("note", "")}
        else:
            answers.pop(check_id, None)
        self._save_answers(data)

    @staticmethod
    def answer_round(checks, answers):
        """Every check with the user's decision on it, as Claude is told them."""
        out = []
        for c in checks:
            a = answers.get(c["id"]) or {}
            out.append({"id": c["id"], "title": c.get("title", ""), "suggestion": c.get("suggestion", ""),
                        "decision": a.get("decision", "not answered"), "note": a.get("note", "")})
        return out

    def send_answers(self, checks, answers):
        """Hand this round's answers to Claude: every check with its decision, and the ones
        left remembered for later rounds."""
        data = self._answers()
        sent = data.setdefault("sent", [])
        left = data.setdefault("left", [])
        for item in self.answer_round(checks, answers):
            sent.append(item)
            if item["decision"] == "leave" and all(l["id"] != item["id"] for l in left):
                left.append({"id": item["id"], "title": item["title"], "note": item["note"]})
        data["answers"] = {}
        self._save_answers(data)

    def take_sent(self):
        """The answers sent and not taken yet; taking them clears them."""
        data = self._answers()
        sent = data.pop("sent", [])
        if sent:
            self._save_answers(data)
        return sent

    # ---------- whether Claude is listening ----------
    # `draft answers --wait` leaves waiting.json while it runs, with its process id and the
    # draft as it was when the wait started. Claude runs it whenever it has finished a round, so
    # while no live wait is running Claude is working on the draft (or its session has ended),
    # and the page holds back accepting. Claude also edits the draft in answer to chat messages,
    # with the wait still running: a draft that no longer matches the one the wait started on
    # is being worked on too, until Claude starts a new wait.

    def draft_stamp(self):
        """Changes whenever the draft does: its diff or Claude's notes. Not the user's own diff."""
        h = hashlib.sha1(self.draft_patch().encode())
        h.update(json.dumps(self.notes.data, sort_keys=True).encode())
        return h.hexdigest()[:16]

    def mark_waiting(self):
        with open(self.waiting_path, "w") as f:
            json.dump({"pid": os.getpid(), "since": time.time(), "draft": self.draft_stamp()}, f)

    def wait_replaced(self):
        """True for a `draft answers --wait` when a later one has taken over from it."""
        try:
            return json.load(open(self.waiting_path)).get("pid") != os.getpid()
        except (OSError, ValueError):
            return False

    def clear_waiting(self):
        try:
            if json.load(open(self.waiting_path)).get("pid") == os.getpid():
                os.remove(self.waiting_path)
        except (OSError, ValueError):
            pass

    def listening(self):
        """True while a `draft answers --wait` for this draft is running, and the draft is
        still as it was when that wait started."""
        try:
            waiting = json.load(open(self.waiting_path))
            os.kill(waiting["pid"], 0)
        except (OSError, ValueError, KeyError):
            return False
        return waiting.get("draft") == self.draft_stamp()

    def claude_idle_seconds(self):
        """Seconds since Claude's session transcript was last written, or None if it isn't found."""
        times = [os.path.getmtime(t) for t in ReadLog(self.meta["session"]).transcripts()
                 if os.path.exists(t)]
        return time.time() - max(times) if times else None

    # ---------- what the user has added to the diagram ----------
    # scope.json: {"expanded": [{"name", "file"}], "full": [class names drawn with every member]}

    def _scope(self):
        try:
            return json.load(open(self.scope_path))
        except (OSError, ValueError):
            return {}

    def _save_scope(self, scope):
        json.dump(scope, open(self.scope_path, "w"), indent=2)

    @property
    def expanded(self):
        return self._scope().get("expanded", [])

    @property
    def full(self):
        return set(self._scope().get("full", []))

    def expand(self, class_name, file):
        scope = self._scope()
        names = scope.setdefault("expanded", [])
        if all(e["name"] != class_name for e in names):
            names.append({"name": class_name, "file": file})
            self._save_scope(scope)

    def show_all_members(self, class_name):
        scope = self._scope()
        full = scope.setdefault("full", [])
        if class_name not in full:
            full.append(class_name)
            self._save_scope(scope)
