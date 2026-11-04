"""A unified diff, parsed into files and hunks: for marking what changed and showing it."""
import os
import re

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$")


class FilePatch:
    def __init__(self, old_path, new_path):
        self.old_path = old_path    # repo-relative, or None when the file is new
        self.new_path = new_path    # repo-relative, or None when the file is deleted
        self.hunks = []             # [(header, [(kind ' '/'-'/'+', old line, new line, text)])]

    @property
    def path(self):
        return self.new_path or self.old_path

    def changed_lines(self, side):
        """Lines removed ('old') or added ('new'). A member that only gained lines is caught
        on the new side and one that only lost lines on the old side, so neither side marks
        the lines next to a change (they belong to whatever comes next)."""
        kind_for = "-" if side == "old" else "+"
        return [((o, o) if side == "old" else (n, n))
                for _, rows in self.hunks for kind, o, n, _ in rows if kind == kind_for]


class Patch:
    def __init__(self, text):
        self.files = []
        current, rows = None, None
        o = n = left_old = left_new = 0
        for line in text.splitlines():
            if left_old > 0 or left_new > 0:        # inside a hunk: counts say where it ends
                tag, body = line[:1] or " ", line[1:]
                if tag == "\\":
                    continue
                if tag == "-":
                    rows.append(("-", o, n, body)); o += 1; left_old -= 1
                elif tag == "+":
                    rows.append(("+", o, n, body)); n += 1; left_new -= 1
                else:
                    rows.append((" ", o, n, body)); o += 1; n += 1; left_old -= 1; left_new -= 1
            elif line.startswith("diff --git"):
                current = None
            elif line.startswith("--- "):
                old = line[4:].strip()
                current = FilePatch(None if old == "/dev/null" else old[2:], None)
            elif line.startswith("+++ ") and current is not None:
                new = line[4:].strip()
                current.new_path = None if new == "/dev/null" else new[2:]
                self.files.append(current)
            elif line.startswith("@@") and current is not None:
                m = HUNK.match(line)
                if m:
                    o, n = int(m.group(1)), int(m.group(3))
                    left_old = int(m.group(2) if m.group(2) is not None else 1)
                    left_new = int(m.group(4) if m.group(4) is not None else 1)
                    rows = []
                    current.hunks.append((line, rows))

    def ranges(self, root, side):
        """{absolute path: [(first, last)]} of changed lines on one side of the diff."""
        out = {}
        for f in self.files:
            rel = f.old_path if side == "old" else f.new_path
            if rel:
                out.setdefault(os.path.join(root, rel), []).extend(f.changed_lines(side))
        return out

    def paths(self, root):
        return {os.path.join(root, p) for f in self.files for p in (f.old_path, f.new_path) if p}