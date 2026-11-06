"""One draft: Claude's change, made in a scratch git worktree so the user's checkout is
untouched until they accept it.

The worktree's branch has one commit on top of HEAD, the *snapshot*: the user's uncommitted
changes (tracked and untracked) when the draft started. Claude edits the worktree after
that, so three states can be compared:

    HEAD  ->  working tree (the user's current diff)  ->  draft (snapshot -> worktree)
"""
import json
import os
import re
import shutil
import subprocess
import time

from notes import Notes

SLUG = re.compile(r"^[a-z0-9][a-z0-9-]*$")


def git(cwd, *args, check=True, input=None):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, input=input)
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
        self.tree = os.path.join(self.dir, "tree")
        self.branch = f"draft/{name}"
        self.meta_path = os.path.join(self.dir, "meta.json")
        self.scope_path = os.path.join(self.dir, "scope.json")
        self.notes_path = os.path.join(self.dir, "notes.json")
        self.answers_path = os.path.join(self.dir, "answers.json")
        # What happened when the page accepted the draft. Beside the draft's folder, not in it,
        # since accepting removes the folder; `draft answers --wait` reports it to Claude.
        self.outcome_path = os.path.join(root, "temp", "draft", f"{name}.outcome.json")

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

    # ---------- lifecycle ----------

    def start(self, session_id, summary=""):
        """Make the worktree at HEAD plus the user's uncommitted changes."""
        if self.exists():
            raise SystemExit(f"draft '{self.name}' already exists: {self.tree}")
        self._ensure_temp_ignored()
        if os.path.exists(self.outcome_path):
            os.remove(self.outcome_path)    # from an earlier draft of the same name
        head = git(self.root, "rev-parse", "HEAD").strip()
        os.makedirs(self.dir, exist_ok=True)
        git(self.root, "worktree", "add", "-q", "-b", self.branch, self.tree, head)
        patch = git(self.root, "diff", "HEAD", "--binary")
        if patch.strip():
            git(self.tree, "apply", "--whitespace=nowarn", input=patch)
        for f in git(self.root, "ls-files", "--others", "--exclude-standard", "-z").split("\0"):
            if f:
                dst = os.path.join(self.tree, f)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(os.path.join(self.root, f), dst)
        git(self.tree, "add", "-A")
        git(self.tree, "-c", "user.name=draft", "-c", "user.email=draft@localhost",
            "commit", "-q", "--allow-empty", "--no-verify", "-m",
            "Snapshot of the working tree the draft starts from")
        snapshot = git(self.tree, "rev-parse", "HEAD").strip()
        json.dump({"name": self.name, "head": head, "snapshot": snapshot,
                   "session": session_id, "summary": summary, "started": time.time()},
                  open(self.meta_path, "w"), indent=2)
        json.dump({"expanded": []}, open(self.scope_path, "w"))

    def accept(self):
        """Apply the draft to the user's checkout as uncommitted changes, then drop it."""
        patch = self.draft_patch(binary=True)
        if not patch.strip():
            raise SystemExit("the draft has no changes")
        r = subprocess.run(["git", "apply", "--check", "--whitespace=nowarn"], cwd=self.root,
                           input=patch, capture_output=True, text=True)
        if r.returncode != 0:
            raise SystemExit("the checkout has changed under the draft; it doesn't apply:\n"
                             + r.stderr.strip())
        git(self.root, "apply", "--whitespace=nowarn", input=patch)
        files = git(self.tree, "diff", "--cached", "--name-only", self.meta["snapshot"]).split()
        self.drop()
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
        git(self.root, "worktree", "remove", "--force", self.tree, check=False)
        git(self.root, "branch", "-D", self.branch, check=False)
        shutil.rmtree(self.dir, ignore_errors=True)

    def _ensure_temp_ignored(self):
        r = subprocess.run(["git", "check-ignore", "-q", "temp/draft"], cwd=self.root)
        if r.returncode != 0:
            with open(os.path.join(self.root, ".gitignore"), "a") as f:
                f.write("\ntemp/\n")

    # ---------- the three states ----------

    def draft_patch(self, binary=False, context=3, whole=False):
        """The draft's diff. `whole` shows the whole function around each change."""
        git(self.tree, "add", "-A")
        args = ["diff", "--cached", f"-U{context}", self.meta["snapshot"]]
        args += ["--binary"] if binary else []
        args += ["--function-context"] if whole else []
        return git(self.tree, *args)

    def current_patch(self, context=3, whole=False):
        """The user's uncommitted diff, untracked files included as additions."""
        extra = ["--function-context"] if whole else []
        out = git(self.root, "diff", f"-U{context}", *extra, "HEAD")
        for f in git(self.root, "ls-files", "--others", "--exclude-standard", "-z").split("\0"):
            if f and not f.startswith("temp/"):
                r = subprocess.run(["git", "diff", "--no-index", f"-U{context}", *extra, "/dev/null", f],
                                   cwd=self.root, capture_output=True, text=True)
                out += r.stdout
        return out

    def state_files(self):
        """Overrides for each state, as {path in the checkout: text, or None if the file
        doesn't exist in that state}. Paths are always the checkout's, so all states parse
        with the same compile flags."""
        head, snap = self.meta["head"], self.meta["snapshot"]

        def changed(cwd, *rev):
            out = git(cwd, "diff", "--name-status", "--no-renames", *rev)
            return [l.split("\t", 1) for l in out.splitlines() if "\t" in l]

        def show(rev, f):
            r = subprocess.run(["git", "show", f"{rev}:{f}"], cwd=self.root, capture_output=True,
                               text=True, errors="replace")
            return r.stdout if r.returncode == 0 else None

        head_state = {}
        for status, f in changed(self.root, "HEAD"):
            head_state[os.path.join(self.root, f)] = None if status == "A" else show(head, f)
        for f in git(self.root, "ls-files", "--others", "--exclude-standard", "-z").split("\0"):
            if f:
                head_state[os.path.join(self.root, f)] = None

        git(self.tree, "add", "-A")
        draft_state = {}
        for status, f in changed(self.tree, "--cached", snap):
            p = os.path.join(self.tree, f)
            draft_state[os.path.join(self.root, f)] = \
                None if status == "D" else open(p, errors="replace").read()
        return head_state, draft_state

    def to_checkout_path(self, path):
        """A worktree path as the same file in the checkout."""
        prefix = self.tree + os.sep
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
