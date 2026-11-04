"""The draft page: a class diagram (Graphviz, drawn in the browser by viz.js) beside the
code diffs, linked both ways: clicking a class or member shows its diff and code.

Colours (dark theme, all in Palette): green added, red removed (struck through), amber
changed, by the draft. Blue marks what the user's own uncommitted diff changed. Grey text is a member Claude didn't read;
a dashed grey box is a ghost, one relation outside the scope.
"""
import html
import json
import os

from api_diff import ADDED, CHANGED, REMOVED

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


P = Palette
DRAFT_FILL = {ADDED: P.ADDED, REMOVED: P.REMOVED, CHANGED: P.CHANGED}
CURRENT_FILL = P.CURRENT
SHOW_ALL_UP_TO = 8


def esc(s):
    return html.escape(s, quote=True)


class DiffPanel:
    """One diff rendered as HTML rows, with an anchor per line so the diagram can jump to it."""

    def __init__(self, patch, prefix):
        self.patch = patch
        self.prefix = prefix
        self.index = {f.path: i for i, f in enumerate(patch.files)}

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
        for i, f in enumerate(self.patch.files):
            label = f.path if f.old_path == f.new_path else \
                f"{f.old_path or '(new)'} → {f.new_path or '(deleted)'}"
            out.append(f'<div class="file"><div class="fname">{esc(label)}</div><table>')
            for header, rows in f.hunks:
                out.append(f'<tr class="hunk"><td></td><td></td><td>{esc(header)}</td></tr>')
                for kind, o, n, text in rows:
                    cls = {"+": "add", "-": "del", " ": "ctx"}[kind]
                    rid = f"{self.prefix}-{i}-o{o}" if kind == "-" else f"{self.prefix}-{i}-n{n}"
                    on = "" if kind == "+" else o
                    nn = "" if kind == "-" else n
                    out.append(f'<tr class="{cls}" id="{rid}"><td class="ln">{on}</td>'
                               f'<td class="ln">{nn}</td><td class="src">{kind}{esc(text)}</td></tr>')
            out.append("</table></div>")
        return "\n".join(out)


class DraftPage:
    def __init__(self, view, stamp):
        self.v = view
        self.stamp = stamp
        self.selectable = {}    # {id: what the side panel shows when it's clicked}
        self.prop_panel = DiffPanel(view.drafted, "P")
        self.cur_panel = DiffPanel(view.current, "C")
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
        if c.bases:
            rows.append(f'<TR><TD ALIGN="LEFT"><FONT POINT-SIZE="9" COLOR="{P.DIM}">: '
                        f'{esc(", ".join(c.bases))}</FONT></TD></TR>')
        members = dict(c.members)
        work = v.work.classes.get(name)
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
        for key, m, ps, cs, mread in shown:
            text = esc(f"{m.access} {m.label}")
            if ps == REMOVED:
                text = f"<S>{text}</S>"
            if not mread and not ps:
                text = f'<FONT COLOR="{P.UNREAD_TEXT}">{text}</FONT>'
            fill = DRAFT_FILL.get(ps) or (CURRENT_FILL if cs else P.NODE)
            msid = self._select_id(self._payload(f"{name}::{m.label}", name, key))
            rows.append(f'<TR><TD ALIGN="LEFT" HREF="#sel:{msid}" BGCOLOR="{fill}">'
                        f'<FONT FACE="Courier" POINT-SIZE="10">{text}</FONT></TD></TR>')
        if hidden:
            rows.append(f'<TR><TD ALIGN="LEFT" HREF="#full:{esc(name)}"><FONT POINT-SIZE="9" '
                        f'COLOR="{P.LINK}">… {hidden} more (show all)</FONT></TD></TR>')
        border = P.UNREAD if not read and not pstat else P.FAINT
        return (f'<<TABLE BORDER="1" COLOR="{border}" CELLBORDER="0" CELLSPACING="0" '
                f'BGCOLOR="{P.NODE}">{"".join(rows)}</TABLE>>')

    def dot(self):
        v = self.v
        lines = ['digraph api {', 'rankdir=BT; nodesep=0.35; ranksep=0.55;',
                 'bgcolor="transparent";',
                 f'node [shape=plaintext, fontname="Helvetica", fontsize=11, fontcolor="{P.TEXT}"];',
                 f'edge [fontname="Helvetica", fontsize=9, color="{P.EDGE}"];']
        for name in sorted(v.scope):
            lines.append(f'{self._node(name)} [label={self._class_label(name)}];')
        for name in v.ghosts:
            c = v.drawn(name)
            where = f"{os.path.relpath(c.file, v.root)}:{c.lines[0]}"
            lines.append(f'{self._node(name)} [shape=box, style="dashed,rounded,filled", '
                         f'fillcolor="{P.GHOST_FILL}", color="{P.GHOST_LINE}", fontcolor="{P.DIM}", '
                         f'label="{esc(name)}", tooltip="Add {esc(name)} ({esc(where)}) to the diagram", '
                         f'href="#expand:{esc(name)}"];')
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
        return PAGE.format(p=P,
            name=esc(meta["name"]), headline=esc(meta.get("summary") or ""),
            summary=items(summary), current=items(current), warn=warn,
            offstage=f"<div class='off'>Your uncommitted change also touches: {offstage}</div>" if offstage else "",
            prop_diff=self.prop_panel.html(), cur_diff=self.cur_panel.html(),
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
#diagram {{ flex:1.2; overflow:auto; border-right:1px solid {p.BORDER}; position:relative; }}
#diagram svg a {{ cursor:pointer; }}
#zoom {{ position:sticky; top:6px; left:6px; z-index:2; }}
button {{ font-size:12px; color:{p.TEXT}; background:{p.RAISED}; border:1px solid {p.BORDER}; }}
#side {{ flex:1; display:flex; flex-direction:column; min-width:0; }}
.tabs {{ background:{p.PANEL}; }}
.tabs button {{ border:none; background:transparent; padding:6px 12px; cursor:pointer; color:{p.DIM}; }}
.tabs button.on {{ background:{p.BG}; color:{p.TEXT}; border-bottom:2px solid {p.LINK}; }}
.panel {{ flex:1; overflow:auto; display:none; padding:6px; }} .panel.on {{ display:block; }}
.file {{ margin-bottom:12px; }} .fname {{ font-weight:600; padding:3px 4px; background:{p.RAISED}; }}
table {{ border-collapse:collapse; width:100%; font:11.5px Menlo, monospace; }}
td.ln {{ color:{p.FAINT}; text-align:right; padding:0 4px; width:1%; white-space:nowrap; user-select:none; }}
td.src {{ white-space:pre; }}
tr.add {{ background:{p.DIFF_ADD}; }} tr.del {{ background:{p.DIFF_DEL}; }} tr.hunk td {{ color:{p.HUNK_TEXT}; background:{p.HUNK_BG}; }}
tr.hit {{ outline:2px solid {p.LINK}; }}
pre {{ font:11.5px Menlo, monospace; background:{p.PANEL}; padding:6px; overflow:auto; }}
.empty {{ color:{p.FAINT}; }}
</style></head><body>
<header>
 <h1>Draft: {name} <span style="font-weight:normal">{headline}</span></h1>
 <div class="legend"><span style="background:{p.ADDED}">draft adds</span><span style="background:{p.CHANGED}">changes</span><span style="background:{p.REMOVED}"><s>removes</s></span><span style="background:{p.CURRENT}">your uncommitted change</span><span style="color:{p.UNREAD_TEXT}">not read by Claude</span><span style="border-style:dashed;color:{p.DIM}">ghost: click to add</span></div>
 <div class="cols"><div><b>API changes in the draft</b><ul>{summary}</ul></div><div><b>Your uncommitted API changes</b><ul>{current}</ul></div></div>
 <div style="color:{p.DIM}">{scope_note}</div>
 {warn}{offstage}{diags}
</header>
<main>
 <div id="diagram"><div id="zoom"><button onclick="zoom(1.25)">+</button><button onclick="zoom(0.8)">−</button><button onclick="zoom(0)">fit</button></div><div id="svg">Drawing…</div></div>
 <div id="side">
  <div class="tabs"><button data-t="prop" class="on">The draft</button><button data-t="cur">Your uncommitted change</button><button data-t="code">Code</button></div>
  <div id="prop" class="panel on">{prop_diff}</div>
  <div id="cur" class="panel">{cur_diff}</div>
  <div id="code" class="panel"><p class="empty">Click a class or member in the diagram.</p></div>
 </div>
</main>
<script src="https://cdn.jsdelivr.net/npm/@viz-js/viz@3.11.0/lib/viz-standalone.js"></script>
<script>
const DOT = {dot};
const SEL = {data};
const STAMP = "{stamp}";
let scale = 1, svg = null;
function tab(t) {{
  document.querySelectorAll('.tabs button').forEach(b => b.classList.toggle('on', b.dataset.t === t));
  document.querySelectorAll('.panel').forEach(p => p.classList.toggle('on', p.id === t));
}}
document.querySelectorAll('.tabs button').forEach(b => b.onclick = () => tab(b.dataset.t));
function zoom(f) {{
  if (!svg) return;
  const box = document.getElementById('diagram');
  scale = f === 0 ? Math.min(1, (box.clientWidth - 10) / svg.viewBox.baseVal.width) : scale * f;
  svg.setAttribute('width', svg.viewBox.baseVal.width * scale);
  svg.setAttribute('height', svg.viewBox.baseVal.height * scale);
}}
function esc(s) {{ return s.replace(/[&<>]/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;'}})[c]); }}
function jump(id, t) {{
  tab(t);
  document.querySelectorAll('tr.hit').forEach(r => r.classList.remove('hit'));
  const row = document.getElementById(id);
  if (row) {{ row.classList.add('hit'); row.scrollIntoView({{block:'center'}}); }}
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
async function post(path, body) {{
  await fetch(path, {{method:'POST', headers:{{'Content-Type':'application/json'}}, body:JSON.stringify(body)}});
  location.reload();
}}
document.getElementById('svg').addEventListener('click', onLink);
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
Viz.instance().then(viz => {{
  svg = viz.renderSVGElement(DOT);
  const host = document.getElementById('svg'); host.innerHTML = ''; host.appendChild(svg);
  zoom(0);
}}).catch(err => document.getElementById('svg').textContent = 'Graphviz failed: ' + err);
// Live: re-draw when the draft, your diff, the scope or Claude's reading changes.
if (location.protocol.startsWith('http')) setInterval(async () => {{
  try {{ const s = await (await fetch('/stamp')).text(); if (s && s !== STAMP) location.reload(); }} catch (e) {{}}
}}, 2000);
</script></body></html>
"""