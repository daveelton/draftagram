"""What the diagram shows: the three API states, the two diffs between them, and which
classes are in scope.

Scope starts as the classes Claude read in its session (by line range, so a glance at one
method brings in that class, not the whole file), plus the classes either diff changes,
plus classes the user expanded on the page. Classes one relation away from the scope are
ghosts: drawn greyed, clickable to expand.
"""
import hashlib
import json
import os
import subprocess

from api_diff import ApiDiff
from api_model import ApiModel, CompileDatabase, HEADER_EXTS, SOURCE_EXTS
from patch import Patch
from reads import ReadLog

CPP_EXTS = SOURCE_EXTS + HEADER_EXTS
MAX_GHOSTS = 24
DIFF_CONTEXT = 8    # lines shown around each change; accept still applies with git's usual 3


class UnitCache:
    """Parsed units, keyed by the unit and the text of every override it includes: a unit
    whose included files didn't change between states or renders isn't parsed again."""

    def __init__(self):
        self.entries = {}   # {(unit source, override key): (model, included files)}
        self.includes = {}  # {unit source: files it included last time}

    def get(self, db, unit, project_files, overrides):
        src = unit[0]
        seen = self.includes.get(src)
        relevant = sorted((p, overrides[p]) for p in overrides
                          if seen is None or p in seen or p in unit[2])
        key = (src, tuple(unit[2]), hashlib.sha1(repr(relevant).encode()).hexdigest())
        if key not in self.entries:
            self.entries[key] = ApiModel.parse_unit(db, unit, project_files, overrides)
            self.includes[src] = self.entries[key][1]
        return self.entries[key][0]


class DraftView:
    def __init__(self, draft, cache=None):
        self.p = draft
        self.root = draft.root
        self.cache = cache or UnitCache()
        self.db = CompileDatabase(self.root)

    def stamp(self):
        """Changes whenever the page would: the draft, the user's diff, the scope or what
        Claude has read."""
        h = hashlib.sha1()
        h.update(self.p.draft_patch().encode())
        h.update(self.p.current_patch().encode())
        h.update(repr(self.p._scope()).encode())
        h.update(json.dumps(self.p.notes.data, sort_keys=True).encode())
        for t in ReadLog(self.p.meta["session"]).transcripts():
            h.update(f"{t}:{os.path.getsize(t)}".encode())
        return h.hexdigest()[:16]

    def project_files(self):
        out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard"], cwd=self.root,
                             capture_output=True, text=True).stdout.split("\n")
        files = {os.path.join(self.root, f) for f in out if f.endswith(CPP_EXTS)}
        return files

    def build(self):
        p = self.p
        self.current = Patch(p.current_patch(DIFF_CONTEXT))
        self.drafted = Patch(p.draft_patch(context=DIFF_CONTEXT))
        self.current_whole = Patch(p.current_patch(DIFF_CONTEXT, whole=True))
        self.drafted_whole = Patch(p.draft_patch(context=DIFF_CONTEXT, whole=True))
        head_state, work_state, prop_state = p.state_files()
        project = self.project_files() | {f for f in prop_state if f.endswith(CPP_EXTS)}

        reads = ReadLog(p.meta["session"]).load()
        self.reads = {f: r for f, r in reads.within(self.root, p.to_checkout_path).items()
                      if f.endswith(CPP_EXTS)}
        files = set(self.reads) | {f for f in self.current.paths(self.root) |
                                   self.drafted.paths(self.root) if f.endswith(CPP_EXTS)}
        files |= {e["file"] for e in p.expanded}
        files = {f for f in files if f in project}

        units = self.db.translation_units(files)

        def model(overrides):
            absent = {f for f, t in overrides.items() if t is None}
            parts = [self.cache.get(self.db, u, project, overrides) for u in units]
            return ApiModel.merge(project, parts, absent)

        self.head = model(head_state)
        self.work = model(work_state)
        self.prop = model(prop_state)
        self.current_diff = ApiDiff(self.head, self.work, self.current.ranges(self.root, "old"),
                                    self.current.ranges(self.root, "new"))
        self.drafted_diff = ApiDiff(self.work, self.prop, self.drafted.ranges(self.root, "old"),
                                     self.drafted.ranges(self.root, "new"))
        self.diagnostics = sorted(set(self.prop.diagnostics + self.work.diagnostics))
        self._choose_scope()
        return self

    # ---------- scope ----------

    def drawn(self, name):
        """The class as drawn: the draft's, or the working tree's if the draft removes it."""
        return self.prop.classes.get(name) or self.work.classes.get(name)

    def _choose_scope(self):
        expanded = {e["name"] for e in self.p.expanded}
        names = set(self.prop.classes) | set(self.work.classes)
        self.read_classes = {n for n in names if self.drawn(n).touches(self.reads)}
        self.changed_classes = set(self.drafted_diff.classes)
        self.scope = self.read_classes | self.changed_classes | (expanded & names)
        self.expanded = expanded & names

        def weight(n):
            c = self.drawn(n)
            return 0 if any(n in self.drawn(s).bases or n in self.drawn(s).owns
                            for s in self.scope if self.drawn(s)) else 1 if c else 2

        near = {}       # {class in scope: its neighbours}
        for n in self.scope:
            c = self.drawn(n)
            # A class in scope only because Claude read part of it leads to the neighbours
            # of the parts read; a changed or added class, to all of its neighbours.
            partial = n not in self.changed_classes and n not in self.expanded
            near[n] = c.related_via(self.reads) if partial else c.related()
        whole = self.scope - (self.read_classes - self.changed_classes - self.expanded)
        for n in names - self.scope:      # classes that use a changed or added class
            for used in self.drawn(n).related() & whole:
                near[used].add(n)
        candidates = (set().union(*near.values()) - self.scope) & names

        # Ghosts are taken from each class's neighbours in turn, its bases first, so one
        # class with many neighbours can't fill every place. Changed classes choose first.
        def turn(n):
            return (n not in self.changed_classes, n not in self.expanded, n)

        queues = [sorted(near[n] & candidates, key=lambda g: (g not in self.drawn(n).bases, weight(g), g))
                  for n in sorted(self.scope, key=turn)]
        chosen = []
        while len(chosen) < MAX_GHOSTS and any(queues):
            for q in queues:
                while q and q[0] in chosen:
                    q.pop(0)
                if q and len(chosen) < MAX_GHOSTS:
                    chosen.append(q.pop(0))
        self.ghosts = sorted(chosen, key=lambda n: (weight(n), n))
        self.hidden_ghosts = len(candidates) - len(chosen)
        # Classes the user's own diff changed that aren't in view: offered, not drawn.
        self.offstage = sorted(set(self.current_diff.classes) - self.scope - set(self.ghosts))

    def unread_in_scope(self):
        """Classes the user expanded that Claude hasn't read: what to read next."""
        return sorted(n for n in self.expanded if n not in self.read_classes)