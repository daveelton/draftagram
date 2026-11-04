"""What Claude read in a session, from its transcript: the starting scope of the diagram.

Claude Code writes each session to ~/.claude/projects/<project>/<session>.jsonl, and each
subagent's to <session>/subagents/*.jsonl. Every Read call there carries its file path and
line range. Shell reads (cat, head, tail, sed -n) count too, as whole files or their ranges.
Search hits (Grep, Glob, grep) don't: a hit isn't something Claude studied.
"""
import glob
import json
import os
import re
import shlex

WHOLE_FILE = 10 ** 9
SED_RANGE = re.compile(r"^'?(\d+)(?:,(\d+))?p'?$")


class ReadLog:
    def __init__(self, session_id):
        self.session_id = session_id
        self.ranges = {}    # {absolute path: [(first, last)]}, 1-based and inclusive

    def transcripts(self):
        base = os.path.expanduser("~/.claude/projects")
        main = glob.glob(os.path.join(base, "*", f"{self.session_id}.jsonl"))
        subs = glob.glob(os.path.join(base, "*", self.session_id, "subagents", "*.jsonl"))
        return main + subs

    def load(self):
        for path in self.transcripts():
            for line in open(path, errors="replace"):
                if '"tool_use"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                msg = entry.get("message")
                if not isinstance(msg, dict) or not isinstance(msg.get("content"), list):
                    continue
                cwd = entry.get("cwd") or "/"
                for c in msg["content"]:
                    if isinstance(c, dict) and c.get("type") == "tool_use":
                        self._note_tool(c.get("name"), c.get("input") or {}, cwd)
        return self

    def _add(self, path, first, last):
        self.ranges.setdefault(os.path.normpath(path), []).append((first, last))

    def _note_tool(self, name, args, cwd):
        if name == "Read" and args.get("file_path"):
            first = int(args.get("offset") or 1)
            limit = args.get("limit")
            self._add(args["file_path"], first, first + int(limit) - 1 if limit else WHOLE_FILE)
        elif name == "Bash" and args.get("command"):
            self._note_shell(args["command"], cwd)

    def _note_shell(self, command, cwd):
        for part in re.split(r"&&|\|\||;|\|", command):
            try:
                words = shlex.split(part)
            except ValueError:
                continue
            if not words or words[0] not in ("cat", "head", "tail", "sed"):
                continue
            first, last = 1, WHOLE_FILE
            if words[0] == "sed":
                if "-n" not in words:
                    continue
                spec = next((w for w in words[1:] if SED_RANGE.match(w)), None)
                if spec:
                    a, b = SED_RANGE.match(spec).groups()
                    first, last = int(a), int(b or a)
            for w in words[1:]:
                if w.startswith("-") or SED_RANGE.match(w):
                    continue
                p = w if os.path.isabs(w) else os.path.join(cwd, w)
                if os.path.isfile(p):
                    self._add(p, first, last)

    def within(self, root, translate=lambda p: p):
        """The reads of files under `root`, with paths passed through `translate` (a
        draft's worktree paths become the checkout's)."""
        out = {}
        for p, spans in self.ranges.items():
            p = translate(p)
            if p.startswith(root + os.sep):
                out.setdefault(p, []).extend(spans)
        return out