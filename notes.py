"""Claude's notes on a draft: what it fixes or adds, and the context the code can't show,
written for the reviewer and drawn on the page beside what is read from the code.

Notes are optional; without them the page shows only the code. Claude writes them as JSON
and stores them with `draft notes NAME --file FILE`. FORMAT is what they may hold. Paths are
relative to the project root, as in the diffs.
"""
import json

FORMAT = """
{
  "kind":      "fix | feature",
  "problem":   "one or two sentences a technical manager would follow: what was wrong (for a
                fix) or what was needed (for a feature), and its effect",
  "summary":   ["what the draft does to fix it or build it, one short bullet each", ...],
  "processes": [{"name": "App", "paths": ["Source/"]}, {"name": "Sync server", "note": "..."}],
  "links":     [{"from": "Class", "to": "Class", "label": "an HTTP call, a message, ..."}],
  "sequence":  "mermaid sequenceDiagram source; group participants with box <process> ... end",
  "behaviour": [{"case": "...", "before": "...", "after": "..."}],
  "files":     {"path": {"role": "new | changed | ...", "note": "what changed in it, one line"}},
  "lineNotes": [{"file": "path", "line": 123, "note": "..."}],
  "checks":    [{"id": "short-slug", "tag": "risk | tidy | question",
                 "title": "what could go wrong, in plain words, one line",
                 "suggestion": "what pressing Fix makes Claude change in the draft, as an action, one line",
                 "recommend": "fix | leave, Claude's advice (optional)",
                 "because": "why, one line (with recommend)",
                 "detail": "the code-level explanation (folded on the page)",
                 "file": "path", "line": 123}],
  "minorChecks": ["lesser points, shown folded", ...],
  "sinceLastReview": [{"title": "a check the user asked to fix", "done": "what was changed"}],
  "commits":   [{"subject": "under 50 characters", "body": "why, wrapped at 72", "files": ["path", ...]}]
}
All fields are optional. `backticks` mark code. processes gives each separately running
process or server its own colour: a file belongs to the first whose path prefix matches, and
a process with no paths (a server elsewhere) appears only in the sequence diagram. links are
connections the code can't show, drawn on the diagram between classes that are in it.
lineNotes[].line is a line in the draft's version of the file.
checks are decisions for the user, answered Fix or Leave on the page: at most MAX_CHECKS.
Fix means Claude makes the suggestion; Leave means the draft stays as it is. So suggestion
is always a change Claude can make, never "keep it" or "leave as is": advice to leave it
goes in recommend and because. A step only the user can take, such as a test on a machine
Claude can't reach, isn't a check: say it in the summary, or ask in the chat.
Things they only need to know go in behaviour, lesser points in minorChecks. Don't raise a
check again that `draft scope` lists as left by the user.
commits are the commits the page offers to make once the draft is accepted, one per separate
piece of work, each listing the draft's files it takes. Without them the page offers one
commit of every file the draft changes.
"""

MAX_CHECKS = 5

FIELDS = {"kind": str, "problem": str, "summary": list, "processes": list, "links": list,
          "sequence": str, "behaviour": list, "files": dict, "lineNotes": list,
          "checks": list, "minorChecks": list, "sinceLastReview": list, "commits": list}


class Notes:
    def __init__(self, data=None):
        self.data = data or {}

    @classmethod
    def load(cls, path):
        try:
            return cls(json.load(open(path)))
        except (OSError, ValueError):
            return cls()

    @staticmethod
    def problems(data):
        """What is wrong with `data` as notes, as plain lines; empty when it's usable."""
        if not isinstance(data, dict):
            return ["the notes must be a JSON object"]
        out = []
        for key, value in data.items():
            if key not in FIELDS:
                out.append(f"unknown field '{key}' (draft notes --format)")
            elif not isinstance(value, FIELDS[key]):
                out.append(f"'{key}' should be a {FIELDS[key].__name__}")
        for p in data.get("processes", []) if isinstance(data.get("processes"), list) else []:
            if not isinstance(p, dict) or not isinstance(p.get("name"), str):
                out.append("each process needs a name")
        checks = data.get("checks", []) if isinstance(data.get("checks"), list) else []
        if len(checks) > MAX_CHECKS:
            out.append(f"{len(checks)} checks: keep to {MAX_CHECKS}, and move the rest to minorChecks")
        ids = [c.get("id") for c in checks if isinstance(c, dict)]
        if len(ids) != len(checks) or not all(isinstance(i, str) and i for i in ids) or len(set(ids)) != len(ids):
            out.append("each check needs its own id")
        if any(not isinstance(c, dict) or not c.get("title") for c in checks):
            out.append("each check needs a title")
        for c in checks:
            if isinstance(c, dict):
                out += Notes._check_problems(c)
        return out

    # A suggestion that starts like this is advice to leave the draft as it is, so Fix would
    # mean the same as Leave.
    NOT_A_FIX = ("keep", "leave", "no change", "nothing", "do nothing", "don't change", "as is")

    @staticmethod
    def _check_problems(c):
        name = c.get("id", "?")
        out = []
        suggestion = str(c.get("suggestion", "")).strip().lower()
        if not suggestion:
            out.append(f"check '{name}' needs a suggestion: the change Fix makes")
        elif suggestion.startswith(Notes.NOT_A_FIX):
            out.append(f"check '{name}': its suggestion reads as leaving it ('{c['suggestion'][:40]}...'). "
                       "Make it the change Fix makes, and put the advice in recommend: \"leave\" with because")
        if c.get("recommend") not in (None, "fix", "leave"):
            out.append(f"check '{name}': recommend is \"fix\" or \"leave\"")
        if c.get("recommend") and not c.get("because"):
            out.append(f"check '{name}': say why in because")
        return out

    def __bool__(self):
        return bool(self.data)

    def get(self, key, default=None):
        return self.data.get(key, default)

    @property
    def is_feature(self):
        return self.data.get("kind") == "feature"

    @property
    def processes(self):
        return self.data.get("processes", [])

    def process_of(self, path):
        """The index of the process a project-relative path belongs to, or -1."""
        for i, p in enumerate(self.processes):
            if any(path.startswith(prefix) for prefix in p.get("paths", [])):
                return i
        return -1

    @property
    def checks(self):
        return self.data.get("checks", [])

    def file_note(self, path):
        return self.data.get("files", {}).get(path, {})

    def line_notes(self, path):
        """{line in the draft's version: [notes]} for one file."""
        out = {}
        for n in self.data.get("lineNotes", []):
            if n.get("file") == path and isinstance(n.get("line"), int):
                out.setdefault(n["line"], []).append(n.get("note", ""))
        return out

    def unnoted(self, paths):
        """The paths the notes don't describe: what `draft scope` asks Claude to add."""
        files = self.data.get("files", {})
        return [p for p in paths if not files.get(p, {}).get("note")]

