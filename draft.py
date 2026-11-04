#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["libclang"]
# ///
"""draft: see a change Claude drafts as a class diagram and code diffs before it lands.

    draft start NAME [--summary TEXT]   worktree for the draft, from your checkout as it is
    draft show [NAME]                   open the live page (diagram + diffs) in the browser
    draft scope [NAME]                  what's in the diagram, as text (for Claude)
    draft render [NAME] [--out FILE]    write the page once, without the live server
    draft accept [NAME]                 apply the draft to your checkout, uncommitted
    draft drop [NAME]                   throw the draft away
    draft list

Docs: ~/mac-setup/draft/README.md
"""
import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from page import DraftPage      # noqa: E402
from draft_tree import Draft      # noqa: E402
from view import DraftView, UnitCache   # noqa: E402

IDLE_EXIT_SECONDS = 4 * 3600


class DraftServer:
    """Serves one draft's page on localhost. The page polls /stamp and reloads when the
    draft, the user's diff, the scope or Claude's reading changes; clicks on ghosts and
    "show all" POST back here and change the scope."""

    def __init__(self, draft):
        self.p = draft
        self.cache = UnitCache()
        self.page, self.page_stamp = None, None
        self.last_seen = time.time()
        self.info_path = os.path.join(draft.dir, "server.json")

    @classmethod
    def running(cls, draft):
        try:
            info = json.load(open(os.path.join(draft.dir, "server.json")))
            os.kill(info["pid"], 0)
            with socket.create_connection(("127.0.0.1", info["port"]), timeout=1):
                return info
        except (OSError, ValueError, KeyError):
            return None

    @classmethod
    def launch(cls, draft):
        """Start the server in the background unless it's already up; returns its URL."""
        info = cls.running(draft)
        if not info:
            log = open(os.path.join(draft.dir, "server.log"), "a")
            subprocess.Popen([os.path.realpath(__file__), "serve", draft.name],
                             cwd=draft.root, stdout=log, stderr=log, start_new_session=True)
            for _ in range(100):
                time.sleep(0.1)
                info = cls.running(draft)
                if info:
                    break
            else:
                raise SystemExit(f"server didn't start; see {log.name}")
        return f"http://127.0.0.1:{info['port']}/"

    def render(self):
        view = DraftView(self.p, self.cache)
        stamp = view.stamp()
        if stamp != self.page_stamp:
            self.page = DraftPage(view.build(), stamp).html()
            self.page_stamp = stamp
        return self.page

    def serve(self):
        server = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, kind="text/plain"):
                data = body.encode()
                self.send_response(code)
                self.send_header("Content-Type", kind + "; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                server.last_seen = time.time()
                if not server.p.exists():
                    self._send(410, "This draft was accepted or dropped.")
                    return
                try:
                    if self.path == "/stamp":
                        self._send(200, DraftView(server.p, server.cache).stamp())
                    elif self.path == "/":
                        self._send(200, server.render(), "text/html")
                    else:
                        self._send(404, "not found")
                except Exception as e:     # show the failure in the page, keep serving
                    import traceback
                    self._send(500, "draft failed:\n" + traceback.format_exc())
                    print(e, file=sys.stderr, flush=True)

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                name = body.get("name", "")
                if self.path == "/expand":
                    view = DraftView(server.p, server.cache).build()
                    c = view.drawn(name)
                    if c:
                        server.p.expand(name, c.file)
                elif self.path == "/full":
                    server.p.show_all_members(name)
                self._send(200, "ok")

        httpd = HTTPServer(("127.0.0.1", 0), Handler)
        httpd.timeout = 30
        json.dump({"pid": os.getpid(), "port": httpd.server_address[1]}, open(self.info_path, "w"))
        signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
        while server.p.exists() and time.time() - self.last_seen < IDLE_EXIT_SECONDS:
            httpd.handle_request()

    @classmethod
    def stop(cls, draft):
        info = cls.running(draft)
        if info:
            os.kill(info["pid"], signal.SIGTERM)


class DraftCli:
    def __init__(self):
        self.root = Draft.project_root(os.getcwd())

    def run(self, argv):
        ap = argparse.ArgumentParser(prog="draft", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
        sub = ap.add_subparsers(dest="cmd", required=True)
        s = sub.add_parser("start")
        s.add_argument("name")
        s.add_argument("--summary", default="")
        s.add_argument("--session", default=os.environ.get("CLAUDE_CODE_SESSION_ID"))
        for cmd in ("show", "scope", "accept", "drop", "serve"):
            sub.add_parser(cmd).add_argument("name", nargs="?")
        r = sub.add_parser("render")
        r.add_argument("name", nargs="?")
        r.add_argument("--out")
        sub.add_parser("list")
        args = ap.parse_args(argv)
        getattr(self, "cmd_" + args.cmd)(args)

    def cmd_start(self, a):
        if not a.session:
            raise SystemExit("no session id: run inside Claude Code, or pass --session")
        p = Draft(self.root, a.name)
        p.start(a.session, a.summary)
        print(f"Draft '{p.name}' started. Make the change in:\n  {p.tree}\n"
              f"Then: draft show {p.name}")

    def cmd_show(self, a):
        p = Draft.find(self.root, a.name)
        url = DraftServer.launch(p)
        webbrowser.open(url)
        print(url)

    def cmd_serve(self, a):
        DraftServer(Draft.find(self.root, a.name)).serve()

    def cmd_render(self, a):
        p = Draft.find(self.root, a.name)
        view = DraftView(p)
        stamp = view.stamp()
        html = DraftPage(view.build(), stamp).html()
        out = a.out or os.path.join(p.dir, "page.html")
        open(out, "w").write(html)
        print(out)

    def cmd_scope(self, a):
        p = Draft.find(self.root, a.name)
        v = DraftView(p).build()

        def where(n):
            c = v.drawn(n)
            return f"{os.path.relpath(c.file, self.root)}:{c.lines[0]}-{c.lines[1]}"

        def show(title, names):
            print(f"{title}:")
            for n in sorted(names):
                print(f"  {n}  ({where(n)})")
            if not names:
                print("  none")

        unread = v.unread_in_scope()
        if unread:
            show("ADDED BY THE USER, NOT READ YET: read these before revising the draft", unread)
        show("Changed by the draft", v.changed_classes)
        show("Read, unchanged", v.read_classes - v.changed_classes)
        show("Ghosts (one relation away, not in scope)", v.ghosts)
        for line in v.drafted_diff.summary():
            print("draft:", line)
        for d in v.diagnostics:
            print("parser:", d)

    def cmd_accept(self, a):
        p = Draft.find(self.root, a.name)
        DraftServer.stop(p)
        files = p.accept()
        print(f"Applied '{p.name}' to {self.root} (uncommitted):")
        for f in files:
            print("  " + f)

    def cmd_drop(self, a):
        p = Draft.find(self.root, a.name)
        DraftServer.stop(p)
        p.drop()
        print(f"Dropped '{p.name}'.")

    def cmd_list(self, a):
        for p in Draft.all(self.root):
            m = p.meta
            live = DraftServer.running(p)
            print(f"{p.name}  {m.get('summary', '')}" + (f"  [{live['port']}]" if live else ""))


if __name__ == "__main__":
    DraftCli().run(sys.argv[1:])