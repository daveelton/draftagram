"""The draft page: a class diagram (Graphviz, drawn in the browser by viz.js) beside the
code diffs, linked both ways: clicking a class or member shows its diff and code.

Colours (dark theme, all in Palette): green added, red removed (struck through), amber
changed, by the draft. Blue marks what the user's own uncommitted diff changed. Grey text is a member Claude didn't read;
a dashed grey box is a ghost, one relation outside the scope. Violet is anything Claude wrote
(notes.py): the overview, file and line notes, and links the code can't show. Each process in
the notes has its own colour, for its group on the diagram and its files.
"""
import html
import json
import os
import re
import subprocess

from api_diff import ADDED, CHANGED, REMOVED
from api_model import ACCESS

class Palette:
    """Every colour on the page, diagram and HTML alike: a dark theme."""
    BG, PANEL, RAISED = "#1e1f22", "#26282c", "#303238"
    TEXT, DIM, FAINT, BORDER = "#d7dae0", "#9aa0aa", "#6b7079", "#3a3d43"
    NODE, HEADER, EDGE = "#2b2d31", "#3a404c", "#8a919c"
    ADDED, REMOVED, CHANGED, CURRENT = "#1f5130", "#62292b", "#5e4b16", "#243a60"
    UNREAD, UNREAD_TEXT, LINK = "#e0943a", "#868b95", "#6ea8ff"
    GHOST_FILL, GHOST_LINE = "#25272b", "#6b7079"
    EDGE_ADDED, EDGE_REMOVED = "#3fbf5f", "#e05555"
    DIFF_ADD, DIFF_DEL, HUNK_BG, HUNK_TEXT = "#1d3a26", "#432326", "#23293a", "#7f9cd6"
    WARN_BG = "#3d2f17"
    NOTE, NOTE_BG = "#a594f9", "#2a2640"
    PROCESSES = ["#3fb8af", "#e06c9f", "#c8a165", "#8fa3b8", "#b5b35c"]


P = Palette
DRAFT_FILL = {ADDED: P.ADDED, REMOVED: P.REMOVED, CHANGED: P.CHANGED}
CURRENT_FILL = P.CURRENT
SHOW_ALL_UP_TO = 8
# A file whose diff is longer than this starts folded, and so does any file over
# FOLD_IN_BIG_CHANGE_ROWS when the whole diff is over BIG_CHANGE_ROWS.
FOLD_FILE_ROWS = 600
BIG_CHANGE_ROWS = 3000
FOLD_IN_BIG_CHANGE_ROWS = 150
# Members are grouped under C++ access headings rather than marked with UML's + and -,
# which beside the green and red fills read as added and removed.
ACCESS_HEADINGS = [(mark, word) for word, mark in ACCESS.items()] + [(" ", None)]


def esc(s):
    return html.escape(s, quote=True)


def note_html(text):
    """Claude's note text as HTML: escaped, with `backticks` as code."""
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", esc(str(text)))


def process_colour(index):
    return P.PROCESSES[index % len(P.PROCESSES)]


def rgba(colour, alpha):
    n = int(colour[1:], 16)
    return f"rgba({n >> 16}, {(n >> 8) & 255}, {n & 255}, {alpha})"


class DiffPanel:
    """One diff rendered as HTML rows, with an anchor per line so the diagram can jump to it.

    The whole-function version of a diff is a second panel made with `like` set to the first:
    it shares the first's file numbers and folding, so an anchor in one is found in the other
    by its prefix alone (P-3-n12 and PW-3-n12)."""

    def __init__(self, patch, prefix, notes, describes, like=None):
        """`describes` is True for the draft's diffs, which Claude's file and line notes are about."""
        self.patch = patch
        self.prefix = prefix
        self.notes = notes
        self.describes = describes
        if like:
            self.index, self.folded = like.index, like.folded
            return
        self.index = {f.path: i for i, f in enumerate(patch.files)}
        sizes = {f.path: sum(len(rows) for _, rows in f.hunks) for f in patch.files}
        big = sum(sizes.values()) > BIG_CHANGE_ROWS
        self.folded = {path for path, n in sizes.items()
                       if n > FOLD_FILE_ROWS or (big and n > FOLD_IN_BIG_CHANGE_ROWS)}

    def anchor(self, root, new_spans, old_spans):
        """The first changed row inside the given spans ([(path, first, last)]), else the
        first row of any kind inside them."""
        best = None
        for side, spans in (("n", new_spans), ("o", old_spans)):
            for path, a, b in spans:
                rel = os.path.relpath(path, root)
                f = next((f for f in self.patch.files if (f.new_path if side == "n" else
                                                          f.old_path) == rel), None)
                if f is None:
                    continue
                for _, rows in f.hunks:
                    for kind, o, n, _ in rows:
                        line = n if side == "n" else o
                        if a <= line <= b and (kind == ("+" if side == "n" else "-")):
                            return f"{self.prefix}-{self.index[f.path]}-{side}{line}"
                        if best is None and a <= line <= b and kind == " ":
                            best = f"{self.prefix}-{self.index[f.path]}-{side}{line}"
        return best

    def html(self):
        if not self.patch.files:
            return '<p class="empty">No changes.</p>'
        out = []
        for f in self.patch.files:
            i = self.index[f.path]
            label = f.path if f.old_path == f.new_path else \
                f"{f.old_path or '(new)'} → {f.new_path or '(deleted)'}"
            rows = sum(len(r) for _, r in f.hunks)
            size = f' <span class="rows">{rows} lines, folded</span>' if f.path in self.folded else ""
            state = "" if f.path in self.folded else " open"
            proc = self.notes.process_of(f.path)
            chip = (f'<span class="proc" style="color:{process_colour(proc)}">'
                    f'{esc(self.notes.processes[proc]["name"])}</span>') if proc >= 0 else ""
            about = self.notes.file_note(f.path) if self.describes else {}
            role = f'<span class="role">{esc(about["role"])}</span>' if about.get("role") else ""
            note = f'<div class="file-note">{note_html(about["note"])}</div>' if about.get("note") else ""
            line_notes = self.notes.line_notes(f.path) if self.describes else {}
            out.append(f'<details class="file"{state}><summary class="fname">{esc(label)}{chip}{role}{size}</summary>'
                       f'{note}<table>')
            for header, rows in f.hunks:
                out.append(f'<tr class="hunk"><td></td><td></td><td>{esc(header)}</td></tr>')
                for kind, o, n, text in rows:
                    cls = {"+": "add", "-": "del", " ": "ctx"}[kind]
                    rid = f"{self.prefix}-{i}-o{o}" if kind == "-" else f"{self.prefix}-{i}-n{n}"
                    on = "" if kind == "+" else o
                    nn = "" if kind == "-" else n
                    out.append(f'<tr class="{cls}" id="{rid}"><td class="ln">{on}</td>'
                               f'<td class="ln">{nn}</td><td class="src">{kind}{esc(text)}</td></tr>')
                    for line_note in line_notes.get(n, []) if kind != "-" else []:
                        out.append(f'<tr class="note"><td></td><td></td><td><div class="line-note">'
                                   f'{note_html(line_note)}</div></td></tr>')
            out.append("</table></details>")
        return "\n".join(out)


CHECK_TAGS = {"risk": "Risk", "tidy": "Tidy", "question": "Question"}
SUBJECT_MAX = 50


class DraftPage:
    def __init__(self, view, stamp, token):
        """`token` is the page server's, which the page sends with each POST; None for a page
        written by `draft render`, which can't send anything."""
        self.v = view
        self.stamp = stamp
        self.token = token
        self.selectable = {}    # {id: what the side panel shows when it's clicked}
        self.notes = view.p.notes
        self.prop_panel = DiffPanel(view.drafted, "P", self.notes, True)
        self.cur_panel = DiffPanel(view.current, "C", self.notes, False)
        self.prop_whole = DiffPanel(view.drafted_whole, "PW", self.notes, True, like=self.prop_panel)
        self.cur_whole = DiffPanel(view.current_whole, "CW", self.notes, False, like=self.cur_panel)
        self.node_ids = {}

    # ---------- diagram ----------

    def _node(self, name):
        return self.node_ids.setdefault(name, f"n{len(self.node_ids)}")

    def _select_id(self, payload):
        sid = f"s{len(self.selectable)}"
        self.selectable[sid] = payload
        return sid

    def _source(self, path, first, last):
        text = self.prop_texts.get(path)
        if text is None:
            try:
                text = open(path, errors="replace").read()
            except OSError:
                return ""
        lines = text.splitlines()[first - 1:min(last, first + 79)]
        return "\n".join(lines)

    def _spans(self, cls_name, key=None):
        """Declaration and body spans of a class or member, in the draft and the working
        tree (the draft's are new-side lines, the working tree's old-side)."""
        out = {}
        for side, model in (("new", self.v.prop), ("old", self.v.work)):
            c = model.classes.get(cls_name)
            if c is None:
                out[side] = []
            elif key is None:
                out[side] = [(c.file, *c.lines)]
            else:
                m = c.members.get(key)
                out[side] = [] if m is None else [(m.file, *m.lines)] + list(m.impl)
        return out

    def _payload(self, title, cls_name, key=None):
        spans = self._spans(cls_name, key)
        pd = self.v.drafted_diff
        status = pd.member_status(cls_name, key) if key else pd.class_status(cls_name)
        cur = self.v.current_diff
        cstatus = cur.member_status(cls_name, key) if key else cur.class_status(cls_name)
        code = [{"where": f"{os.path.relpath(f, self.v.root)}:{a}", "text": self._source(f, a, b)}
                for f, a, b in (spans["new"] or spans["old"])]
        return {"title": title, "status": status, "current": cstatus, "code": code,
                "prop": self.prop_panel.anchor(self.v.root, spans["new"], spans["old"]) if status else None,
                "cur": self.cur_panel.anchor(self.v.root, spans["old"], []) if cstatus else None}

    def _class_label(self, name):
        v = self.v
        c = v.drawn(name)
        pstat = v.drafted_diff.class_status(name)
        cstat = v.current_diff.class_status(name)
        read = name in v.read_classes
        head_fill = DRAFT_FILL.get(pstat) if pstat in (ADDED, REMOVED) else \
            (P.CHANGED if pstat else (CURRENT_FILL if cstat else P.HEADER))
        title = esc(name)
        if pstat == REMOVED:
            title = f"<S>{title}</S>"
        note = "" if read or pstat else f'<BR/><FONT POINT-SIZE="9" COLOR="{P.UNREAD}">not read by Claude</FONT>'
        sid = self._select_id(self._payload(name, name))
        rows = [f'<TR><TD HREF="#sel:{sid}" BGCOLOR="{head_fill}" CELLPADDING="5">'
                f'<B>{title}</B>{note}</TD></TR>']
        work = v.work.classes.get(name)
        bases = self._bases(c, work if name in v.prop.classes else None)
        if bases:
            rows.append(f'<TR><TD ALIGN="LEFT"><FONT POINT-SIZE="9" COLOR="{P.DIM}">: '
                        f'{bases}</FONT></TD></TR>')
        members = dict(c.members)
        if work and name in v.prop.classes:   # members the draft removes, drawn struck out
            for k, m in work.members.items():
                members.setdefault(k, m)
        shown, hidden = [], 0
        full = name in v.p.full or name in v.expanded or len(members) <= SHOW_ALL_UP_TO
        for key, m in members.items():
            ps = v.drafted_diff.member_status(name, key)
            cs = v.current_diff.member_status(name, key)
            mread = m.touches(v.reads)
            if full or ps or cs or mread:
                shown.append((key, m, ps, cs, mread))
            else:
                hidden += 1
        for mark, heading in ACCESS_HEADINGS:
            group = [row for row in shown if row[1].access == mark]
            if group and heading:
                rows.append(f'<TR><TD ALIGN="LEFT"><FONT POINT-SIZE="8.5" COLOR="{P.DIM}">'
                            f'{heading}:</FONT></TD></TR>')
            for key, m, ps, cs, mread in group:
                text = esc(m.label)
                if ps == REMOVED:
                    text = f"<S>{text}</S>"
                # Every member has the same markup, so Graphviz places them all alike.
                colour = P.UNREAD_TEXT if not mread and not ps else P.TEXT
                text = f'<FONT COLOR="{colour}">{text}</FONT>'
                fill = DRAFT_FILL.get(ps) or (CURRENT_FILL if cs else P.NODE)
                msid = self._select_id(self._payload(f"{name}::{m.label}", name, key))
                rows.append(f'<TR><TD ALIGN="LEFT" HREF="#sel:{msid}" BGCOLOR="{fill}">'
                            f'<FONT FACE="Courier" POINT-SIZE="10">&#160;&#160;{text}</FONT></TD></TR>')
        if hidden:
            rows.append(f'<TR><TD ALIGN="LEFT" HREF="#full:{esc(name)}"><FONT POINT-SIZE="9" '
                        f'COLOR="{P.LINK}">… {hidden} more (show all)</FONT></TD></TR>')
        border = P.UNREAD if not read and not pstat else P.FAINT
        return (f'<<TABLE BORDER="1" COLOR="{border}" CELLBORDER="0" CELLSPACING="0" '
                f'BGCOLOR="{P.NODE}">{"".join(rows)}</TABLE>>')

    @staticmethod
    def _bases(c, work):
        """The class's bases as label text, those the draft adds in green and removes struck
        out in red. Bases are listed as well as drawn because a library base (juce::Timer) has
        no box to draw an edge to. `work` is the class in the working tree, or None when the
        draft adds or removes the whole class."""
        was = work.bases if work else c.bases
        out = []
        for b in dict.fromkeys(c.bases + was):
            if b not in was:
                out.append(f'<FONT COLOR="{P.EDGE_ADDED}">{esc(b)}</FONT>')
            elif b not in c.bases:
                out.append(f'<FONT COLOR="{P.EDGE_REMOVED}"><S>{esc(b)}</S></FONT>')
            else:
                out.append(esc(b))
        return ", ".join(out)

    def dot(self):
        v = self.v
        lines = ['digraph api {', 'rankdir=BT; nodesep=0.35; ranksep=0.55;',
                 'bgcolor="transparent";',
                 f'node [shape=plaintext, fontname="Helvetica", fontsize=11, fontcolor="{P.TEXT}"];',
                 f'edge [fontname="Helvetica", fontsize=9, color="{P.EDGE}"];']
        groups = {}     # {process index, or -1 for none: node lines}

        def add(name, line):
            path = os.path.relpath(v.drawn(name).file, v.root)
            groups.setdefault(self.notes.process_of(path), []).append(line)

        for name in sorted(v.scope):
            add(name, f'{self._node(name)} [label={self._class_label(name)}];')
        for name in v.ghosts:
            c = v.drawn(name)
            where = f"{os.path.relpath(c.file, v.root)}:{c.lines[0]}"
            add(name, f'{self._node(name)} [shape=box, style="dashed,rounded,filled", '
                      f'fillcolor="{P.GHOST_FILL}", color="{P.GHOST_LINE}", fontcolor="{P.DIM}", '
                      f'label="{esc(name)}", tooltip="Add {esc(name)} ({esc(where)}) to the diagram", '
                      f'href="#expand:{esc(name)}"];')
        # Each process in Claude's notes is a box round its classes, in the process's colour.
        # Graphviz reads alpha only as #RRGGBBAA, not rgba().
        for index, group in sorted(groups.items()):
            if index < 0:
                lines += group
                continue
            colour = process_colour(index)
            lines += [f"subgraph cluster_p{index} {{",
                      f'label=<<B>{esc(self.notes.processes[index]["name"])}</B>>; labeljust="l"; '
                      f'fontname="Helvetica"; fontsize=12; fontcolor="{colour}"; color="{colour}"; '
                      f'style="rounded,filled"; fillcolor="{colour}14"; penwidth=1.2; margin=14;',
                      *group, "}"]
        drawn = set(v.scope) | set(v.ghosts)
        for name in sorted(v.scope | set(v.ghosts)):
            new = v.prop.classes.get(name)
            old = v.work.classes.get(name)
            for kind in ("bases", "owns", "refers", "uses"):
                now = set(getattr(new, kind)) if new else set()
                was = set(getattr(old, kind)) if old else set()
                for other in sorted((now | was) & drawn):
                    if name in v.ghosts and other in v.ghosts:
                        continue
                    lines.append(self._edge(name, other, kind,
                                            ADDED if other not in was else
                                            REMOVED if other not in now else None))
        for link in self.notes.get("links", []):    # connections the code can't show
            a, b = link.get("from"), link.get("to")
            if a in drawn and b in drawn:
                lines.append(f'{self._node(a)} -> {self._node(b)} [color="{P.NOTE}", fontcolor="{P.NOTE}", '
                             f'style=dashed, penwidth=1.6, arrowhead=vee, '
                             f'label="  {esc(link.get("label", ""))}  "];')
        lines.append("}")
        return "\n".join(lines)

    def _edge(self, a, b, kind, status):
        style = {"bases": 'arrowhead=onormal',
                 "owns": 'arrowhead=diamond',     # drawn owned -> owner, diamond at the owner
                 "refers": 'style=dashed, arrowhead=vee',
                 "uses": f'style=dotted, arrowhead=open, color="{P.FAINT}"'}[kind]
        src, dst = self._node(a), self._node(b)
        if kind == "owns":
            src, dst = dst, src
        if status == ADDED:
            style += f', color="{P.EDGE_ADDED}", penwidth=2'
        elif status == REMOVED:
            style += f', color="{P.EDGE_REMOVED}", penwidth=2, style=dashed'
        return f"{src} -> {dst} [{style}];"

    # ---------- Claude's notes ----------

    def _overview(self):
        """The Overview tab: what the draft is for, in Claude's words, then how it does it."""
        n = self.notes
        if not n:
            return ('<p class="empty">No notes from Claude yet. Everything else on the page is read '
                    'from the code; Claude adds what the draft fixes or builds with <code>draft notes</code>.</p>')
        out = []
        if n.get("sinceLastReview"):
            out.append('<div class="since"><div class="label">Since your last review</div><ul class="notes">'
                       + "".join(f'<li><b>{note_html(i.get("title", ""))}</b>: {note_html(i.get("done", ""))}</li>'
                                 for i in n.get("sinceLastReview")) + "</ul></div>")
        if n.get("problem"):
            out.append(f'<div class="problem"><div class="label">{"What is needed" if n.is_feature else "The problem"}'
                       f'</div><p>{note_html(n.get("problem"))}</p></div>')
        if n.get("summary"):
            out.append(f'<div class="label">{"How the draft does it" if n.is_feature else "How the draft fixes it"}</div>'
                       '<ul class="notes">' + "".join(f"<li>{note_html(i)}</li>" for i in n.get("summary")) + "</ul>")
        if n.get("behaviour"):
            rows = "".join(f'<tr><td>{note_html(b.get("case", ""))}</td><td class="before">{note_html(b.get("before", ""))}</td>'
                           f'<td class="after">{note_html(b.get("after", ""))}</td></tr>' for b in n.get("behaviour"))
            out.append('<div class="label">Before and after</div><table class="behaviour">'
                       f'<tr><th>Situation</th><th>Before</th><th>After</th></tr>{rows}</table>')
        if n.get("sequence"):
            out.append('<div class="label">What happens at run time</div><div id="sequence">Drawing…</div>')
        out.append(self._checks())
        return "\n".join(out)

    def _checks(self):
        """Claude's checks as cards the user answers Fix or Leave, then the lesser points and
        the checks left on earlier rounds, folded."""
        n, v = self.notes, self.v
        cards = []
        for c in n.checks:
            where = ""
            if c.get("file"):
                line = c.get("line") or 1
                path = os.path.join(v.root, c["file"])
                row = self.prop_panel.anchor(v.root, [(path, line, line)], []) or \
                    self.prop_panel.anchor(v.root, [(path, line - 5, line + 5)], [])
                text = esc(f'{c["file"].split("/")[-1]}:{line}' if c.get("line") else c["file"])
                where = (f'<a href="#" onclick="jump(\'{row}\', \'prop\'); return false">{text}</a>' if row
                         else f"<span>{text}</span>")
            detail = (f'<details><summary>Detail</summary><p>{note_html(c.get("detail", ""))}</p>{where}</details>'
                      if c.get("detail") or where else "")
            suggestion = (f'<div class="suggest"><span>Suggest:</span> {note_html(c["suggestion"])}</div>'
                          if c.get("suggestion") else "")
            cards.append(f'<div class="check" data-id="{esc(c["id"])}"><div class="check-head">'
                         f'<span class="tag">{esc(CHECK_TAGS.get(c.get("tag"), "Check"))}</span>'
                         f'<b>{note_html(c["title"])}</b></div>{suggestion}{detail}'
                         f'<div class="check-actions"><button data-d="fix">Fix</button><button data-d="leave">Leave</button>'
                         f'<input class="check-note" placeholder="Note (optional)"></div></div>')
        minor = n.get("minorChecks", [])
        left = v.p.left
        return ('<div class="label">Check before accepting</div>'
                + ("".join(cards) or '<p class="empty">Nothing to decide.</p>')
                + (f'<details class="minor"><summary>{len(minor)} minor point{"s" if len(minor) > 1 else ""}</summary><ul class="notes">'
                   + "".join(f"<li>{note_html(m)}</li>" for m in minor) + "</ul></details>" if minor else "")
                + (f'<details class="minor"><summary>Left on an earlier round ({len(left)})</summary><ul class="notes">'
                   + "".join(f'<li>{note_html(i["title"])}{" (" + note_html(i["note"]) + ")" if i.get("note") else ""}</li>'
                             for i in left) + "</ul></details>" if left else ""))

    def _sequence(self):
        """The sequence diagram's source, each `box <process>` group tinted in its process's colour."""
        def tint(m):
            names = [p["name"] for p in self.notes.processes]
            if m.group(2) not in names:
                return m.group(0)
            return f"{m.group(1)}box {rgba(process_colour(names.index(m.group(2))), 0.16)} {m.group(2)}"
        return re.sub(r"(?m)^(\s*)box\s+(.+?)\s*$", tint, self.notes.get("sequence", ""))

    def _key(self):
        """The colour key's entries for Claude's notes and its processes."""
        if not self.notes:
            return ""
        def where(p):
            return ", ".join(p.get("paths", [])) or p.get("note", "not in this project")
        procs = "".join(f'<span style="color:{process_colour(i)}; border-color:{process_colour(i)}" '
                        f'title="{esc(where(p))}">{esc(p["name"])}</span>'
                        for i, p in enumerate(self.notes.processes))
        return (f'<span class="notes-key">written by Claude, not read from the code</span>'
                + (f'<span class="sep">processes:</span>{procs}' if procs else ""))

    # ---------- accepting and committing ----------

    def _commits(self):
        """The commits to offer: Claude's, or one of every file the draft changes."""
        return self.notes.get("commits") or [{"subject": self.v.p.meta.get("summary", ""), "body": "",
                                              "files": [f.path for f in self.v.drafted.files]}]

    def _commit_bar(self):
        """The commit messages to edit, folded, and a line on what accepting will commit."""
        commits = self._commits()
        entries = "".join(
            f'<div class="commit"><input class="subject" value="{esc(c.get("subject", ""))}" maxlength="200">'
            f'<span class="count"></span><textarea class="body" rows="6" wrap="off">{esc(c.get("body", ""))}</textarea>'
            f'<div class="files">{esc("  ".join(c.get("files", [])))}</div></div>' for c in commits)
        subjects = " · ".join(esc(c.get("subject", "")) for c in commits)
        label = f"{len(commits)} commits" if len(commits) > 1 else "Commit message"
        mine = sorted({f for c in commits for f in c.get("files", [])} & {f.path for f in self.v.current.files})
        overlap = (f'<div class="overlap">These files also have your own uncommitted changes, which the '
                   f'commit will include: {esc(", ".join(mine))}</div>') if mine else ""
        return f'<details id="message"><summary>{label}: <b>{subjects}</b></summary>{entries}</details>{overlap}'

    def _branch(self):
        def git(*args):
            r = subprocess.run(["git", *args], cwd=self.v.root, capture_output=True, text=True)
            return r.stdout.strip() if r.returncode == 0 else ""
        branch = git("rev-parse", "--abbrev-ref", "HEAD")
        upstream = git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        return f"{branch} → {upstream}" if upstream else f"{branch}, no upstream: push will fail"

    # ---------- page ----------

    def html(self):
        v = self.v
        _, prop_state = v.p.state_files()
        self.prop_texts = {f: t for f, t in prop_state.items() if t is not None}
        dot = self.dot()
        meta = v.p.meta
        summary = v.drafted_diff.summary()
        current = v.current_diff.summary()
        unread = v.unread_in_scope()

        def items(lines):
            return "".join(f"<li>{esc(l)}</li>" for l in lines) or "<li class='empty'>none</li>"

        offstage = "".join(f'<a href="#expand:{esc(n)}" class="chip">{esc(n)}</a>' for n in v.offstage)
        warn = ""
        if unread:
            warn = ('<div class="warn">Added to the diagram but not read by Claude yet: '
                    f'{esc(", ".join(unread))}. Tell Claude to read them before it revises the draft.</div>')
        diags = "".join(f"<li>{esc(d)}</li>" for d in v.diagnostics)
        more = f" (+{v.hidden_ghosts} more not shown)" if v.hidden_ghosts else ""
        data = json.dumps(self.selectable)
        sequence = self._sequence()
        return PAGE.format(p=P,
            overview=self._overview(), key=self._key(), first_tab="overview" if self.notes else "prop",
            sequence=json.dumps(sequence or None), token=json.dumps(self.token),
            checks=json.dumps([c["id"] for c in self.notes.checks]),
            commit_bar=self._commit_bar(), branch=esc(self._branch()), subject_max=SUBJECT_MAX,
            commit_files=json.dumps([c.get("files", []) for c in self._commits()]),
            mermaid='<script src="https://cdn.jsdelivr.net/npm/mermaid@11/dist/mermaid.min.js"></script>' if sequence else "",
            name=esc(meta["name"]), headline=esc(meta.get("summary") or ""),
            summary=items(summary), current=items(current), warn=warn,
            offstage=f"<div class='off'>Your uncommitted change also touches: {offstage}</div>" if offstage else "",
            prop_diff=self.prop_panel.html(), cur_diff=self.cur_panel.html(),
            prop_whole=self.prop_whole.html(), cur_whole=self.cur_whole.html(),
            dot=json.dumps(dot), data=data, stamp=self.stamp, ghosts_more=more,
            diags=f"<details><summary>Parser warnings ({len(v.diagnostics)})</summary><ul>{diags}</ul></details>" if diags else "",
            scope_note=f"{len(v.scope)} classes in scope: {len(v.read_classes)} read by Claude, "
                       f"{len(v.changed_classes)} changed by the draft, {len(v.expanded)} added by you. "
                       f"{len(v.ghosts)} ghosts{more}.")


PAGE = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Draft: {name}</title>
<style>
body {{ margin:0; font:13px -apple-system, Helvetica, sans-serif; color:{p.TEXT}; background:{p.BG}; display:flex; flex-direction:column; height:100vh; color-scheme:dark; }}
a {{ color:{p.LINK}; }}
header {{ padding:8px 14px; border-bottom:1px solid {p.BORDER}; background:{p.PANEL}; }}
header h1 {{ font-size:15px; margin:0 0 2px; }}
header .cols {{ display:flex; gap:28px; }}
header ul {{ margin:2px 0; padding-left:18px; font-family:Menlo, monospace; font-size:11.5px; }}
.legend span {{ display:inline-block; padding:1px 6px; margin-right:4px; border:1px solid {p.BORDER}; font-size:11px; }}
.warn {{ background:{p.WARN_BG}; border:1px solid {p.UNREAD}; padding:4px 8px; margin-top:4px; }}
.off {{ margin-top:4px; }} .chip {{ border:1px dashed {p.GHOST_LINE}; padding:0 5px; margin-right:4px; color:{p.DIM}; text-decoration:none; }}
main {{ flex:1; display:flex; min-height:0; }}
#diagram {{ flex:1.2; overflow:auto; border-right:1px solid {p.BORDER}; position:relative; cursor:grab; }}
#diagram.dragging {{ cursor:grabbing; user-select:none; }}
#diagram.full {{ position:fixed; inset:0; z-index:5; background:{p.BG}; border:none; }}
#diagram svg a {{ cursor:pointer; }}
#diagram svg text[font-family="Courier,monospace"] {{ font-family:Menlo, monospace; }}   /* same width as Courier, which Graphviz measures */
#zoom {{ position:sticky; top:6px; left:6px; z-index:2; display:inline-flex; gap:2px; align-items:center; cursor:default; }}
#zoom .hint {{ color:{p.FAINT}; font-size:11px; margin-left:8px; }}
#zoomed {{ min-width:40px; text-align:center; font-size:11px; color:{p.DIM}; }}
button {{ font-size:12px; color:{p.TEXT}; background:{p.RAISED}; border:1px solid {p.BORDER}; }}
#side {{ flex:1; display:flex; flex-direction:column; min-width:0; }}
.tabs {{ background:{p.PANEL}; }}
.tabs button {{ border:none; background:transparent; padding:6px 12px; cursor:pointer; color:{p.DIM}; }}
.tabs button.on {{ background:{p.BG}; color:{p.TEXT}; border-bottom:2px solid {p.LINK}; }}
.panel {{ flex:1; overflow:auto; display:none; padding:6px; }} .panel.on {{ display:block; }}
.file {{ margin-bottom:12px; }} .fname {{ font-weight:600; padding:3px 4px; background:{p.RAISED}; cursor:pointer; }}
.fname .rows {{ font-weight:normal; color:{p.DIM}; }}
.whole-diff {{ display:none; }} body.whole .whole-diff {{ display:block; }} body.whole .diff {{ display:none; }}
.tabs label {{ float:right; padding:6px 10px; color:{p.DIM}; font-size:12px; cursor:pointer; }}
table {{ border-collapse:collapse; width:100%; font:11.5px Menlo, monospace; }}
td.ln {{ color:{p.FAINT}; text-align:right; padding:0 4px; width:1%; white-space:nowrap; user-select:none; }}
td.src {{ white-space:pre; }}
tr.add {{ background:{p.DIFF_ADD}; }} tr.del {{ background:{p.DIFF_DEL}; }} tr.hunk td {{ color:{p.HUNK_TEXT}; background:{p.HUNK_BG}; }}
tr.hit {{ outline:2px solid {p.LINK}; }}
pre {{ font:11.5px Menlo, monospace; background:{p.PANEL}; padding:6px; overflow:auto; }}
.empty {{ color:{p.FAINT}; }}
.legend .sep {{ border:none; color:{p.DIM}; padding-left:10px; }}
.legend .notes-key {{ color:{p.NOTE}; border-color:{p.NOTE}; background:{p.NOTE_BG}; }}
#overview {{ padding:10px 14px; font-size:13.5px; line-height:1.45; }}
.label {{ font-size:11px; text-transform:uppercase; letter-spacing:.06em; color:{p.NOTE}; font-weight:600; margin:14px 0 4px; }}
.problem {{ border-left:3px solid {p.NOTE}; padding:0 0 0 12px; }} .problem .label {{ margin-top:0; }}
.problem p {{ margin:2px 0 0; font-size:14.5px; }}
ul.notes {{ margin:2px 0; padding-left:20px; }} ul.notes li {{ margin:3px 0; }}
table.behaviour {{ font:12.5px -apple-system, Helvetica, sans-serif; }}
table.behaviour th, table.behaviour td {{ text-align:left; vertical-align:top; padding:4px 8px; border-bottom:1px solid {p.BORDER}; }}
table.behaviour th {{ color:{p.DIM}; font-weight:600; }} td.before {{ color:{p.DIM}; }}
code {{ font:11.5px Menlo, monospace; background:{p.RAISED}; padding:0 3px; border-radius:3px; }}
#sequence svg {{ max-width:100%; height:auto; }}
.proc {{ font-size:11px; margin-left:10px; }}
.role {{ font-weight:normal; font-size:10.5px; color:{p.DIM}; margin-left:8px; text-transform:uppercase; letter-spacing:.04em; }}
.file-note {{ background:{p.NOTE_BG}; border-left:3px solid {p.NOTE}; padding:5px 10px; margin:4px 0 6px; font-size:12.5px; line-height:1.4; }}
.since {{ border-left:3px solid {p.NOTE}; padding:0 0 4px 12px; margin-bottom:12px; }} .since .label {{ margin-top:0; }}
.check {{ border:1px solid {p.BORDER}; border-left:3px solid {p.NOTE}; background:{p.PANEL}; border-radius:4px; padding:8px 10px; margin:6px 0; }}
.check.left {{ opacity:.55; }}
.check-head {{ display:flex; gap:8px; align-items:baseline; }}
.check .tag {{ font-size:10px; text-transform:uppercase; letter-spacing:.05em; color:{p.NOTE}; border:1px solid {p.NOTE}; border-radius:3px; padding:0 4px; }}
.check .suggest {{ margin-top:4px; }} .check .suggest span {{ color:{p.DIM}; }}
.check details {{ margin-top:4px; color:{p.DIM}; }} .check details p {{ margin:4px 0; color:{p.TEXT}; }}
.check-actions {{ display:flex; gap:6px; margin-top:6px; align-items:center; }}
.check-actions button {{ padding:2px 12px; cursor:pointer; }}
.check-actions button.chosen {{ background:{p.NOTE}; color:{p.BG}; border-color:{p.NOTE}; font-weight:600; }}
.check-note {{ flex:1; font:12px -apple-system, Helvetica, sans-serif; color:{p.TEXT}; background:{p.BG}; border:1px solid {p.BORDER}; padding:2px 6px; }}
details.minor {{ margin-top:8px; color:{p.DIM}; }} details.minor ul {{ color:{p.TEXT}; }}
#actions {{ border-top:1px solid {p.BORDER}; background:{p.PANEL}; padding:8px 10px; max-height:55vh; overflow:auto; }}
#actions .row {{ display:flex; flex-wrap:wrap; gap:8px; align-items:center; margin-top:6px; }}
#actions button {{ padding:4px 10px; cursor:pointer; }}
#message summary {{ cursor:pointer; color:{p.DIM}; }} #message summary b {{ color:{p.TEXT}; font-weight:600; }}
.commit {{ display:grid; grid-template-columns:1fr auto; gap:4px 8px; margin:6px 0; padding:8px; background:{p.BG}; border:1px solid {p.BORDER}; }}
.commit input, .commit textarea {{ font:12px Menlo, monospace; color:{p.TEXT}; background:{p.PANEL}; border:1px solid {p.BORDER}; padding:3px 6px; }}
.commit textarea {{ grid-column:1 / -1; white-space:pre; overflow-x:auto; resize:vertical; }}
.commit .count {{ font-size:11px; color:{p.DIM}; align-self:center; }} .commit .count.long {{ color:{p.UNREAD}; }}
.commit .files {{ grid-column:1 / -1; font:11px Menlo, monospace; color:{p.DIM}; overflow-wrap:anywhere; }}
.overlap {{ color:{p.UNREAD}; font-size:12px; margin-top:4px; }}
#log {{ white-space:pre-wrap; font:11px Menlo, monospace; background:{p.BG}; padding:6px; margin:6px 0 0; max-height:200px; overflow:auto; }}
#actions button.primary {{ background:{p.LINK}; color:{p.BG}; border-color:{p.LINK}; font-weight:600; padding:4px 12px; cursor:pointer; }}
#actions button:disabled {{ opacity:.5; cursor:default; }}
#actions .status {{ color:{p.DIM}; font-size:12px; }}
.line-note {{ background:{p.NOTE_BG}; border-left:3px solid {p.NOTE}; padding:3px 8px; margin:2px 0 4px; font:12px -apple-system, Helvetica, sans-serif; white-space:normal; }}
</style></head><body>
<header>
 <h1>Draft: {name} <span style="font-weight:normal">{headline}</span></h1>
 <div class="legend"><span style="background:{p.ADDED}">draft adds</span><span style="background:{p.CHANGED}">changes</span><span style="background:{p.REMOVED}"><s>removes</s></span><span style="background:{p.CURRENT}">your uncommitted change</span><span style="color:{p.UNREAD_TEXT}">not read by Claude</span><span style="border-style:dashed;color:{p.DIM}">ghost: click to add</span>{key}</div>
 <div class="cols"><div><b>API changes in the draft</b><ul>{summary}</ul></div><div><b>Your uncommitted API changes</b><ul>{current}</ul></div></div>
 <div style="color:{p.DIM}">{scope_note}</div>
 {warn}{offstage}{diags}
</header>
<main>
 <div id="diagram"><div id="zoom"><button onclick="zoomBy(1 / ZOOM_STEP)" title="Zoom out">−</button><span id="zoomed"></span><button onclick="zoomBy(ZOOM_STEP)" title="Zoom in">+</button><button onclick="zoomFit()">fit</button><button onclick="zoomTo(1)">100%</button><button id="full" onclick="setFull(!diagram.classList.contains('full'))">full screen</button><span class="hint">pinch or ⌘-scroll to zoom, drag to move</span></div><div id="svg">Drawing…</div></div>
 <div id="side">
  <div class="tabs"><button data-t="overview">Overview</button><button data-t="prop">The draft</button><button data-t="cur">Your uncommitted change</button><button data-t="code">Code</button><label><input type="checkbox" id="whole"> whole functions</label></div>
  <div id="overview" class="panel">{overview}</div>
  <div id="prop" class="panel"><div class="diff">{prop_diff}</div><div class="whole-diff">{prop_whole}</div></div>
  <div id="cur" class="panel"><div class="diff">{cur_diff}</div><div class="whole-diff">{cur_whole}</div></div>
  <div id="code" class="panel"><p class="empty">Click a class or member in the diagram.</p></div>
  <div id="actions">{commit_bar}
   <div class="row"><button class="primary" id="send">Accept, commit, push</button><button id="commit">Accept and commit</button><button id="accept">Accept only</button><span class="status" id="status"></span></div>
   <div class="row"><span class="status" id="answered"></span><span class="status">{branch}</span></div>
   <pre id="log" hidden></pre>
  </div>
 </div>
</main>
<script src="https://cdn.jsdelivr.net/npm/@viz-js/viz@3.11.0/lib/viz-standalone.js"></script>
{mermaid}
<script>
const DOT = {dot};
const SEQUENCE = {sequence};
const TOKEN = {token};       // null on a page written by `draft render`: it can't send anything
const CHECKS = {checks};
const COMMIT_FILES = {commit_files};
const SUBJECT_MAX = {subject_max};
const SEL = {data};
const STAMP = "{stamp}";
let scale = 1, svg = null, dragged = false;
// The open tab is kept across the reloads the draft's changes cause.
function tab(t) {{
  document.querySelectorAll('.tabs button').forEach(b => b.classList.toggle('on', b.dataset.t === t));
  document.querySelectorAll('.panel').forEach(p => p.classList.toggle('on', p.id === t));
  try {{ sessionStorage.setItem('tab', t); }} catch (e) {{}}
  drawSequence();
}}
document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => tab(b.dataset.t));
// Mermaid measures text as it draws, so the sequence diagram is drawn once its tab is showing.
let sequenceDrawn = false;
async function drawSequence() {{
  const box = document.getElementById('sequence');
  if (sequenceDrawn || !SEQUENCE || !box || !box.offsetParent) return;
  sequenceDrawn = true;
  if (!window.mermaid) {{ box.textContent = SEQUENCE; return; }}
  mermaid.initialize({{startOnLoad: false, theme: 'dark', securityLevel: 'strict'}});
  try {{ box.innerHTML = (await mermaid.render('sequence-svg', SEQUENCE)).svg; }}
  catch (e) {{ box.textContent = 'Sequence diagram failed: ' + e.message; }}
}}
// The diagram zooms and pans inside its own panel. The SVG's size is set from the zoom (not
// a CSS transform), so the panel's scrollbars and trackpad scrolling keep working.
const ZOOM_MIN = 0.2, ZOOM_MAX = 3, ZOOM_STEP = 1.25;
const START_MIN = 0.5;     // fitting the width never starts smaller than this, so text stays readable
const DRAG_PX = 4;         // a press that moves further than this is a drag, not a click
const diagram = document.getElementById('diagram');
function zoomTo(s, x = diagram.clientWidth / 2, y = diagram.clientHeight / 2) {{
  if (!svg) return;
  s = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, s));
  const px = (diagram.scrollLeft + x) / scale, py = (diagram.scrollTop + y) / scale;   // keep this point under (x, y)
  scale = s;
  svg.setAttribute('width', svg.viewBox.baseVal.width * scale);
  svg.setAttribute('height', svg.viewBox.baseVal.height * scale);
  diagram.scrollLeft = px * scale - x;
  diagram.scrollTop = py * scale - y;
  document.getElementById('zoomed').textContent = Math.round(scale * 100) + '%';
}}
function zoomBy(f) {{ zoomTo(scale * f); }}
function zoomFit() {{
  const vb = svg.viewBox.baseVal;
  zoomTo(Math.min(1, (diagram.clientWidth - 10) / vb.width, (diagram.clientHeight - 40) / vb.height));
  diagram.scrollTo(0, 0);
}}
function setFull(on) {{
  diagram.classList.toggle('full', on);
  document.getElementById('full').textContent = on ? 'close' : 'full screen';
  zoomTo(scale);
}}
document.addEventListener('keydown', e => {{ if (e.key === 'Escape' && diagram.classList.contains('full')) setFull(false); }});
diagram.addEventListener('wheel', e => {{
  if (!e.ctrlKey && !e.metaKey) return;      // a trackpad pinch arrives as a wheel event with ctrlKey set
  e.preventDefault();
  const r = diagram.getBoundingClientRect();
  zoomTo(scale * Math.exp(-e.deltaY * 0.01), e.clientX - r.left, e.clientY - r.top);
}}, {{passive:false}});
diagram.addEventListener('pointerdown', e => {{
  if (e.button !== 0 || e.target.closest('#zoom')) return;
  const x0 = e.clientX, y0 = e.clientY, left = diagram.scrollLeft, top = diagram.scrollTop;
  dragged = false;
  const move = m => {{
    if (Math.abs(m.clientX - x0) + Math.abs(m.clientY - y0) > DRAG_PX) {{ dragged = true; diagram.classList.add('dragging'); }}
    if (dragged) {{ diagram.scrollLeft = left - (m.clientX - x0); diagram.scrollTop = top - (m.clientY - y0); }}
  }};
  const up = () => {{
    diagram.classList.remove('dragging');
    removeEventListener('pointermove', move);
    removeEventListener('pointerup', up);
  }};
  addEventListener('pointermove', move);
  addEventListener('pointerup', up);
}});
// The page reloads whenever the draft changes; keep the zoom and position across reloads.
addEventListener('beforeunload', () => {{
  try {{
    sessionStorage.setItem('view', JSON.stringify({{scale, left: diagram.scrollLeft, top: diagram.scrollTop,
                                                    full: diagram.classList.contains('full')}}));
  }} catch (e) {{}}     // storage can be blocked; the page then starts fitted, as on a first visit
}});
function restoreView() {{
  let v = null;
  try {{ v = JSON.parse(sessionStorage.getItem('view')); }} catch (e) {{}}
  if (!v) {{
    zoomTo(Math.min(1, Math.max(START_MIN, (diagram.clientWidth - 10) / svg.viewBox.baseVal.width)));
    diagram.scrollTo(0, 0);
    return;
  }}
  if (v.full) setFull(true);
  zoomTo(v.scale);
  diagram.scrollLeft = v.left;
  diagram.scrollTop = v.top;
}}
function esc(s) {{ return s.replace(/[&<>]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;'}})[c]); }}
// "Whole functions" swaps each diff for its --function-context version, kept in step on reload.
const wholeBox = document.getElementById('whole');
function setWhole(on) {{
  document.body.classList.toggle('whole', on);
  try {{ sessionStorage.setItem('whole', on ? '1' : ''); }} catch (e) {{}}
}}
wholeBox.onchange = () => setWhole(wholeBox.checked);
try {{ wholeBox.checked = !!sessionStorage.getItem('whole'); }} catch (e) {{}}
setWhole(wholeBox.checked);
function jump(id, t) {{
  tab(t);
  document.querySelectorAll('tr.hit').forEach(r => r.classList.remove('hit'));
  const row = document.getElementById(wholeBox.checked ? id.replace(/^([PC])-/, '$1W-') : id);
  if (!row) return;
  row.closest('details').open = true;
  row.classList.add('hit');
  row.scrollIntoView({{block:'center'}});
}}
function select(sid) {{
  const s = SEL[sid];
  let h = '<h3 style="font:600 13px Menlo">' + esc(s.title) + '</h3>';
  h += '<p>' + (s.status ? 'Draft: <b>' + s.status + '</b>' : 'Draft leaves it unchanged') +
       (s.current ? ' · your uncommitted change: <b>' + s.current + '</b>' : '') + '</p>';
  if (s.prop) h += '<p><a href="#" onclick="jump(\\'' + s.prop + '\\',\\'prop\\');return false">Show in the draft</a></p>';
  if (s.cur) h += '<p><a href="#" onclick="jump(\\'' + s.cur + '\\',\\'cur\\');return false">Show in your uncommitted change</a></p>';
  for (const c of s.code) h += '<div class="fname">' + esc(c.where) + '</div><pre>' + esc(c.text) + '</pre>';
  document.getElementById('code').innerHTML = h;
  if (s.prop) jump(s.prop, 'prop'); else if (s.cur) jump(s.cur, 'cur'); else tab('code');
}}
function send(path, body) {{
  return fetch(path, {{method:'POST', headers:{{'Content-Type':'application/json', 'X-Draft-Token':TOKEN}}, body:JSON.stringify(body)}});
}}
async function post(path, body) {{
  await send(path, body);
  location.reload();
}}
// Answers to Claude's checks. Each is saved on the server as it's made, so the reloads the
// draft's changes cause don't lose them; "Send" hands the round to Claude (`draft answers`).
const answers = {{}};
const statusLine = document.getElementById('status'), sendButton = document.getElementById('send');
const answeredLine = document.getElementById('answered'), log = document.getElementById('log');
const acceptButtons = [document.getElementById('commit'), document.getElementById('accept')];
let sent = false, finished = false;
function cardOf(id) {{ return document.querySelector(`.check[data-id="${{CSS.escape(id)}}"]`); }}
function choose(id, d) {{
  if (answers[id] && answers[id].decision === d) delete answers[id];      // a second click undoes it
  else answers[id] = {{decision: d, note: cardOf(id).querySelector('.check-note').value}};
  send('/answer', {{id, answer: answers[id] || null}});
  showAnswers();
}}
function showAnswers() {{
  for (const id of CHECKS) {{
    const card = cardOf(id), d = answers[id] && answers[id].decision;
    card.querySelectorAll('button[data-d]').forEach(b => b.classList.toggle('chosen', b.dataset.d === d));
    card.classList.toggle('left', d === 'leave');
  }}
  // With a fix asked for, the main button sends the answers to Claude instead of accepting.
  const fixes = fixCount(), open = CHECKS.filter(id => !answers[id]).length;
  sendButton.textContent = fixes ? `Send ${{fixes}} fix${{fixes > 1 ? 'es' : ''}} to Claude` : 'Accept, commit, push';
  acceptButtons.forEach(b => b.hidden = fixes > 0);
  [sendButton, ...acceptButtons].forEach(b => b.disabled = !TOKEN || sent || finished);
  answeredLine.textContent = open ? `${{open}} check${{open > 1 ? 's' : ''}} not answered.` : '';
  if (!TOKEN) statusLine.textContent = 'Written by draft render: run draft show to accept or answer from the page.';
}}
function fixCount() {{ return CHECKS.filter(id => answers[id] && answers[id].decision === 'fix').length; }}
function readCommits() {{
  return [...document.querySelectorAll('.commit')].map((el, i) => ({{
    subject: el.querySelector('.subject').value, body: el.querySelector('.body').value, files: COMMIT_FILES[i]}}));
}}
document.querySelectorAll('.commit').forEach(el => {{
  const subject = el.querySelector('.subject'), count = el.querySelector('.count');
  const update = () => {{ count.textContent = `${{subject.value.length}} / ${{SUBJECT_MAX}}`;
                          count.classList.toggle('long', subject.value.length > SUBJECT_MAX); }};
  subject.oninput = update;
  update();
}});
async function accept(commit, push) {{
  finished = true;
  showAnswers();
  statusLine.textContent = commit ? (push ? 'Accepting, committing and pushing…' : 'Accepting and committing…') : 'Accepting…';
  const r = await send('/accept', {{stamp: STAMP, commit, push, commits: readCommits()}}).catch(e => null);
  const reply = r ? await r.json().catch(e => ({{ok: false, log: 'No reply from the page server.'}}))
                  : {{ok: false, log: 'The page server is not running.'}};
  finished = reply.ok || (r && r.status === 200);     // once accepted, the draft is gone whatever git did
  log.hidden = !reply.log;
  log.textContent = reply.log;
  statusLine.textContent = !finished ? 'Not accepted, see below.'
      : !reply.ok ? 'Accepted, but git failed, see below.'
      : commit ? (push ? `Accepted, committed and pushed: ${{reply.commits.join(', ')}}` : `Accepted and committed: ${{reply.commits.join(', ')}}`)
      : 'Accepted: the change is in your checkout, uncommitted.';
  showAnswers();
}}
document.getElementById('commit').onclick = () => accept(true, false);
document.getElementById('accept').onclick = () => accept(false, false);
document.querySelectorAll('.check').forEach(card => {{
  const id = card.dataset.id;
  card.querySelectorAll('button[data-d]').forEach(b => {{ b.disabled = !TOKEN; b.onclick = () => choose(id, b.dataset.d); }});
  const note = card.querySelector('.check-note');
  note.disabled = !TOKEN;
  note.onchange = () => {{ if (answers[id]) {{ answers[id].note = note.value; send('/answer', {{id, answer: answers[id]}}); }} }};
}});
sendButton.onclick = () => fixCount() ? sendAnswers() : accept(true, true);
async function sendAnswers() {{
  CHECKS.forEach(id => {{ if (answers[id]) answers[id].note = cardOf(id).querySelector('.check-note').value; }});
  sendButton.disabled = true;
  const r = await send('/send', {{answers}}).catch(e => null);
  sent = !!(r && r.ok);
  statusLine.textContent = sent ? 'Sent to Claude. The page redraws as Claude revises the draft.' : 'Sending failed.';
  showAnswers();
}}
if (TOKEN) fetch('/answers').then(r => r.json()).then(saved => {{
  for (const id of CHECKS) if (saved[id]) {{
    answers[id] = saved[id];
    cardOf(id).querySelector('.check-note').value = saved[id].note || '';
  }}
  showAnswers();
}});
showAnswers();
document.getElementById('svg').addEventListener('click', e => dragged ? e.preventDefault() : onLink(e));
document.querySelector('header').addEventListener('click', onLink);
function onLink(e) {{
  const a = e.target.closest('a'); if (!a) return;
  const href = a.getAttribute('href') || a.getAttribute('xlink:href') || '';
  const [kind, arg] = [href.slice(1, href.indexOf(':')), decodeURIComponent(href.slice(href.indexOf(':') + 1))];
  if (!['sel', 'expand', 'full'].includes(kind)) return;
  e.preventDefault();
  if (kind === 'sel') select(arg);
  if (kind === 'expand') post('/expand', {{name: arg}});
  if (kind === 'full') post('/full', {{name: arg}});
}}
let savedTab = null;
try {{ savedTab = sessionStorage.getItem('tab'); }} catch (e) {{}}
tab(savedTab && document.getElementById(savedTab) ? savedTab : '{first_tab}');
Viz.instance().then(viz => {{
  svg = viz.renderSVGElement(DOT);
  const host = document.getElementById('svg'); host.innerHTML = ''; host.appendChild(svg);
  restoreView();
}}).catch(err => document.getElementById('svg').textContent = 'Graphviz failed: ' + err);
// Live: re-draw when the draft, your diff, the scope or Claude's reading changes.
// The server stops 10 minutes after the last open page goes away; say so, and how to restart.
function stopped(text) {{
  let b = document.getElementById('stopped');
  if (!b) {{ b = document.createElement('div'); b.id = 'stopped'; b.className = 'warn'; document.querySelector('header').appendChild(b); }}
  b.textContent = text;
}}
if (location.protocol.startsWith('http')) setInterval(async () => {{
  if (finished) return;     // accepting from this page: its reply says what happened
  try {{
    const r = await fetch('/stamp');
    if (r.status === 410) return stopped('This draft was accepted or dropped.');
    const s = await r.text();
    if (s && s !== STAMP) location.reload();
    const b = document.getElementById('stopped'); if (b) b.remove();
  }} catch (e) {{
    stopped('The page server has stopped. Run `draft show {name}` in the project to start it again.');
  }}
}}, 2000);
</script></body></html>
"""