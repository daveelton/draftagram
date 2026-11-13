#!/usr/bin/env -S uv run --quiet --script
# /// script
# requires-python = ">=3.11"
# dependencies = ["libclang"]
# ///
"""draft: see a change Claude drafts as a class diagram and code diffs before it lands.

    draft start NAME --worktree | --in-place [--summary TEXT]
                                        start a draft from your checkout as it is: in a scratch
                                        worktree, or in the checkout itself so you can build
                                        and test it. With neither, it says what to ask the user
    draft show [NAME]                   open the live page (diagram + diffs) in the browser
    draft scope [NAME]                  what's in the diagram, as text (for Claude)
    draft notes [NAME] --file FILE      Claude's notes for the page (--format: what they hold)
    draft answers [NAME] [--wait]       the user's Fix/Leave answers sent from the page
    draft render [NAME] [--out FILE]    write the page once, without the live server
    draft accept [NAME]                 keep the draft in your checkout, uncommitted
    draft drop [NAME]                   throw the draft away (in place: put the checkout back)
    draft list

Docs: README.md beside this script (`cat "$(which draft)"` shows where that is).
"""
import argparse
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))

from notes import FORMAT, Notes   # noqa: E402
from page import DraftPage      # noqa: E402
from patch import Patch         # noqa: E402
from draft_tree import Draft, IN_PLACE, WORKTREE   # noqa: E402
from view import DraftView, UnitCache   # noqa: E402

# An open page polls every 2 seconds, about once a minute when Chrome throttles a background
# tab, and not at all once the browser freezes a tab left in the background, as it can during
# a long review in the IDE. So the server allows an hour without a request before it takes it
# that no page is open and exits. The draft stays; `draft show` starts the server again.
IDLE_EXIT_SECONDS = int(os.environ.get("DRAFT_IDLE_SECONDS", 60 * 60))
WAIT_POLL_SECONDS = 1
# The server rewrites server.json this often while it runs, and a file older than the stale
# time means the server has gone without removing it (a crash, a kill, a power cut).
BEAT_SECONDS = 5
BEAT_STALE_SECONDS = 20
# Claude not waiting for the page and its transcript untouched this long: its session has
# probably ended, and the page offers to accept anyway.
SESSION_GONE_SECONDS = 3 * 60


class DraftServer:
    """Serves one draft's page on localhost. The page polls /stamp and reloads when the
    draft, the user's diff, the scope or Claude's reading changes; clicks on ghosts and
    "show all" POST back here and change the scope, and answers to Claude's checks are
    saved and sent from here.

    Every POST must carry the server's token, which only the page it serves knows, so
    another web page open in the browser can't post to it.

    While it runs, the server keeps a heartbeat in the draft's server.json: its process id and
    port, when it started, when a page last asked it for anything, and the time of the beat.
    It removes the file as it exits. Every other draft command learns whether the server is up
    by reading that file, never by signalling the process or connecting to its port. Claude
    Code's Bash sandbox refuses both of those to a command run inside it, and reading a file
    in the project is always allowed, so `draft answers --wait` and `draft list` work there."""

    def __init__(self, draft):
        self.p = draft
        self.cache = UnitCache()
        self.page, self.page_stamp = None, None
        self.last_seen = time.time()
        self.info_path = os.path.join(draft.dir, "server.json")
        self.token = secrets.token_urlsafe(24)

    @classmethod
    def _heartbeat(cls, draft):
        try:
            info = json.load(open(os.path.join(draft.dir, "server.json")))
            return info if "port" in info and "beat" in info else None
        except (OSError, ValueError):
            return None

    @classmethod
    def running(cls, draft):
        """The server's heartbeat (pid, port, started, seen, beat) while it is up, else None."""
        info = cls._heartbeat(draft)
        return info if info and time.time() - info["beat"] < BEAT_STALE_SECONDS else None

    @classmethod
    def stopped(cls, draft):
        """True when the server has gone. A missing heartbeat says so at once: the server
        removes it as it exits. An old one gets a second look after two beats, because a
        machine waking from sleep finds the last beat as old as the sleep was long."""
        if not cls._heartbeat(draft):
            return True
        if cls.running(draft):
            return False
        time.sleep(BEAT_SECONDS * 2)
        return not cls.running(draft)

    @classmethod
    def launch(cls, draft):
        """Start the server in the background unless it's already up; returns its URL."""
        info = cls.running(draft)
        if not info:
            log = open(os.path.join(draft.dir, "server.log"), "a")
            # This Python, not the script's own `uv run` line: an installed draft has its
            # environment in .venv and stays out of uv's cache (see install.sh).
            subprocess.Popen([sys.executable, os.path.realpath(__file__), "serve", draft.name],
                             cwd=draft.root, stdout=log, stderr=log, start_new_session=True)
            for _ in range(100):
                time.sleep(0.1)
                info = cls.running(draft)
                if info:
                    break
            else:
                raise SystemExit(f"server didn't start; see {log.name}")
        return cls.url(info)

    @staticmethod
    def url(info):
        return f"http://127.0.0.1:{info['port']}/"

    def render(self):
        view = DraftView(self.p, self.cache)
        stamp = view.stamp()
        if stamp != self.page_stamp:
            self.page = DraftPage(view.build(), stamp, self.token).html()
            self.page_stamp = stamp
        return self.page

    def accept(self, request):
        """Accept the draft from the page, then make the commits it asked for and push if asked.
        Returns (HTTP status, reply). Refused if the draft changed after the page was drawn, so
        what is accepted is what the user saw."""
        if not self.p.listening() and not request.get("anyway"):
            return 409, {"ok": False, "log": "Claude is working on the draft. Accept once it has "
                                              "finished, or accept anyway if its session has ended."}
        if request.get("stamp") != DraftView(self.p, self.cache).stamp():
            return 409, {"ok": False, "log": "The draft changed after this page was drawn. "
                                              "The page will redraw; check it and accept again."}
        commits = request.get("commits", []) if request.get("commit") else []
        drafted = {f.path for f in Patch(self.p.draft_patch()).files}
        stray = sorted({f for c in commits for f in c.get("files", [])} - drafted)
        if stray:
            return 400, {"ok": False, "log": "Not changed by the draft, so not committed from here: "
                                             + ", ".join(stray)}
        if any(not c.get("subject", "").strip() or not c.get("files") for c in commits):
            return 400, {"ok": False, "log": "Every commit needs a subject and at least one file."}
        # The answers go with the outcome: accepting removes the draft's folder, answers and
        # notes included, and answers of all Leave accept without being sent first.
        answers = {**self.p.answers, **(request.get("answers") or {})}
        answered = self.p.answer_round(self.p.notes.checks, answers)
        self.p.save_outcome({"state": "accepting", "answers": answered})
        try:
            files = self.p.accept()
        except SystemExit as e:     # Draft reports failures as SystemExit, for the command line
            self.p.discard_outcome()
            return 409, {"ok": False, "log": str(e)}
        ok, made, log = self.p.commit(commits, request.get("push")) if commits else (True, [], "")
        outcome = {"state": "done", "accepted": files, "commits": made,
                   "pushed": ok and bool(commits and request.get("push")), "ok": ok, "log": log,
                   "answers": answered}
        self.p.save_outcome(outcome)
        print(json.dumps(outcome, indent=2), flush=True)
        return 200, {"ok": ok, "log": log, "commits": made, "pushed": outcome["pushed"]}

    def claude_state(self):
        """For the page: is Claude waiting for the page, and how long since its session did
        anything. Not listening and long idle means the session has probably ended."""
        idle = self.p.claude_idle_seconds()
        return {"listening": self.p.listening(), "idle": None if idle is None else round(idle),
                "gone_after": SESSION_GONE_SECONDS}

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

            def _from_page(self):
                """True when the request is addressed to this server by its own name. A web page
                that has pointed a host name of its own at 127.0.0.1 (DNS rebinding) would
                otherwise be served this page, token included, and could accept and push."""
                port = server.httpd.server_address[1]
                if self.headers.get("Host") in (f"127.0.0.1:{port}", f"localhost:{port}"):
                    return True
                self._send(403, "wrong host")
                return False

            def do_GET(self):
                if not self._from_page():
                    return
                server.last_seen = time.time()
                if not server.p.exists():
                    self._send(410, "This draft was accepted or dropped.")
                    return
                try:
                    if self.path == "/stamp":
                        self._send(200, DraftView(server.p, server.cache).stamp())
                    elif self.path == "/answers":
                        self._send(200, json.dumps(server.p.answers), "application/json")
                    elif self.path == "/claude":
                        self._send(200, json.dumps(server.claude_state()), "application/json")
                    elif self.path == "/":
                        self._send(200, server.render(), "text/html")
                    else:
                        self._send(404, "not found")
                except Exception as e:     # show the failure in the page, keep serving
                    import traceback
                    self._send(500, "draft failed:\n" + traceback.format_exc())
                    print(e, file=sys.stderr, flush=True)

            def do_POST(self):
                if not self._from_page():
                    return
                body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
                if self.headers.get("X-Draft-Token") != server.token:
                    self._send(403, "wrong token")
                    return
                name = body.get("name", "")
                if self.path == "/accept":
                    code, reply = server.accept(body)
                    self._send(code, json.dumps(reply), "application/json")
                    return
                if self.path == "/answer":
                    server.p.save_answer(body["id"], body.get("answer"))
                elif self.path == "/send":
                    server.p.send_answers(server.p.notes.checks, body.get("answers", {}))
                elif self.path == "/expand":
                    view = DraftView(server.p, server.cache).build()
                    c = view.drawn(name)
                    if c:
                        server.p.expand(name, c.file)
                elif self.path == "/full":
                    server.p.show_all_members(name)
                self._send(200, "ok")

        httpd = self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        httpd.timeout = 15
        self.started = time.time()
        self._beat()
        # Its own thread: the loop below takes one request at a time, and a redraw of a large
        # draft can hold it for longer than the stale time.
        threading.Thread(target=self._keep_beating, daemon=True).start()
        signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))
        try:
            while server.p.exists() and time.time() - self.last_seen < IDLE_EXIT_SECONDS:
                httpd.handle_request()
        finally:
            self._remove_heartbeat()

    def _beat(self):
        """Write the heartbeat. Through a second file, so a reader never sees half of one."""
        beat = {"pid": os.getpid(), "port": self.httpd.server_address[1], "started": self.started,
                "seen": self.last_seen, "beat": time.time()}
        try:
            with open(self.info_path + ".new", "w") as f:
                json.dump(beat, f)
            os.replace(self.info_path + ".new", self.info_path)
        except OSError:
            pass    # the draft's folder has gone: the serve loop is about to end

    def _keep_beating(self):
        while True:
            time.sleep(BEAT_SECONDS)
            self._beat()

    def _remove_heartbeat(self):
        """Only this server's own: a later server for the same draft may have written since."""
        try:
            if json.load(open(self.info_path)).get("pid") == os.getpid():
                os.remove(self.info_path)
        except (OSError, ValueError):
            pass

    @classmethod
    def stop(cls, draft):
        info = cls.running(draft)
        if info:
            try:
                os.kill(info["pid"], signal.SIGTERM)
            except OSError:
                pass    # refused in a sandbox, or already gone. The server also exits by
                        # itself once the draft's folder is removed.


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
        where = s.add_mutually_exclusive_group()
        where.add_argument("--worktree", dest="mode", action="store_const", const=WORKTREE)
        where.add_argument("--in-place", dest="mode", action="store_const", const=IN_PLACE)
        for cmd in ("show", "scope", "accept", "drop", "serve"):
            sub.add_parser(cmd).add_argument("name", nargs="?")
        n = sub.add_parser("notes")
        n.add_argument("name", nargs="?")
        n.add_argument("--file")
        n.add_argument("--format", action="store_true")
        w = sub.add_parser("answers")
        w.add_argument("name", nargs="?")
        w.add_argument("--wait", action="store_true")
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
        if not a.mode:
            print(self._ask_where(p))
            sys.exit(2)
        p.start(a.session, a.mode, a.summary)
        if p.in_place:
            print(f"Draft '{p.name}' started in place. Make the change in the checkout itself:\n"
                  f"  {p.tree}\nThen: draft show {p.name}")
        else:
            print(f"Draft '{p.name}' started. Make the change in its worktree:\n  {p.tree}\n"
                  f"Then: draft show {p.name}")

    MAX_FILES_LISTED = 12

    def _ask_where(self, p):
        """What `draft start` says when it wasn't told where to make the draft: the question
        Claude is to put to the user, with the recommendation the checkout's state leads to."""
        if p.exists():
            raise SystemExit(f"draft '{p.name}' already exists: {p.tree}")
        blocked = p.in_the_way(None)
        if blocked:
            raise SystemExit(blocked)
        again = f"draft start {p.name} --worktree"
        if Draft.all(self.root):
            return ("Not started. Other drafts are open here, so this one can only be made in a "
                    f"worktree. There's nothing to ask the user: run `{again}`.")
        changes = Draft.checkout_changes(self.root)
        worktree = ('"In a separate worktree"  ->  --worktree\n'
                    "     The draft is made in a scratch copy and their source tree stays as it is "
                    "until they accept it. It can't be built or tested before then.")
        in_place = ('"In my source tree"  ->  --in-place\n'
                    "     The draft is made in their source tree, so they can see it in the IDE, and "
                    "build and test it, while they review it. Dropping it puts every file back as it "
                    "is now. Anything they edit while it's open becomes part of the draft.")
        if changes:
            n = len(changes)
            listed = "".join(f"\n  {status} {path}" for status, path in changes[:self.MAX_FILES_LISTED])
            more = f"\n  ... and {n - self.MAX_FILES_LISTED} more" if n > self.MAX_FILES_LISTED else ""
            state = (f"The source tree has uncommitted changes in {n} file{'s' if n > 1 else ''}:"
                     f"{listed}{more}\n"
                     "Say so in the question: how many files, and their names (the first few if "
                     "there are many).")
            first, second = worktree, in_place
            why = ("Recommended because their own uncommitted changes and the draft's would sit "
                   "mixed together in the IDE's diff if it were made in place. The draft's page "
                   "still shows the two apart.")
        else:
            state = "The source tree has no uncommitted changes. Say so in the question."
            first, second = in_place, worktree
            why = ("Recommended because the IDE's uncommitted diff is then exactly the draft, and "
                   "dropping it just returns the source tree to the last commit.")
        return ("Not started: the user hasn't said where to make this draft. Ask them with "
                "AskUserQuestion, then run `draft start` again with the flag for their answer "
                "(and the same name and summary).\n\n"
                f"{state}\n\n"
                "The two options, the recommended one first with \"(Recommended)\" after its label:\n"
                f"  1. {first}\n     {why}\n"
                f"  2. {second}")

    def cmd_show(self, a):
        p = Draft.find(self.root, a.name)
        url = DraftServer.launch(p)
        webbrowser.open(url)
        print(f"{url}\n\n{p.hand_over(url)}")

    def cmd_serve(self, a):
        DraftServer(Draft.find(self.root, a.name)).serve()

    def cmd_render(self, a):
        p = Draft.find(self.root, a.name)
        view = DraftView(p)
        stamp = view.stamp()
        html = DraftPage(view.build(), stamp, None).html()
        out = a.out or os.path.join(p.dir, "page.html")
        open(out, "w").write(html)
        print(out)

    def cmd_notes(self, a):
        if a.format:
            print(FORMAT.strip())
            return
        if not a.file:
            raise SystemExit("give --file FILE, or --format to see what notes hold")
        p = Draft.find(self.root, a.name)
        try:
            data = json.load(open(a.file))
        except (OSError, ValueError) as e:
            raise SystemExit(f"can't read {a.file}: {e}")
        problems = Notes.problems(data)
        if problems:
            raise SystemExit("notes not saved:\n  " + "\n  ".join(problems))
        p.set_notes(data)
        print(f"Notes saved for '{p.name}'; an open page redraws with them.")
        # A round of revisions ends here, usually without another `draft show`, so the line
        # that sends the user back to the page is given here too.
        info = DraftServer.running(p)
        if data.get("sinceLastReview") and info:
            print("\n" + p.hand_over(DraftServer.url(info)))

    def cmd_answers(self, a):
        """Print the answers the user sent from the page, and take them. With --wait, wait for
        some: Claude runs this in the background after `draft show`."""
        # A draft accepted from the page is gone, but what happened is kept beside its folder
        # until Claude takes it, so a named draft is looked up by that too.
        named = Draft(self.root, a.name) if a.name else None
        p = named if named and os.path.exists(named.outcome_path) else Draft.find(self.root, a.name)
        # While this waits and the draft stays as it is now, Claude is listening, and the page
        # lets the user accept.
        if a.wait and p.exists():
            p.mark_waiting()
        try:
            self._answers(p, a)
        finally:
            p.clear_waiting()

    def _answers(self, p, a):
        while True:
            outcome = p.read_outcome()
            if outcome and outcome.get("state") == "accepting":
                # The page is accepting now: committing and pushing take a few seconds.
                if not a.wait:
                    print(f"The user is accepting '{p.name}' from the page; run this again to see the result.")
                    return
                time.sleep(WAIT_POLL_SECONDS)
                continue
            if not p.exists():
                outcome = p.take_outcome()
                if not outcome:
                    print(f"The draft '{p.name}' is gone: it was dropped, or accepted with `draft accept`.")
                    return
                print(f"The user accepted '{p.name}' from the page: " + ", ".join(outcome["accepted"]))
                for c in outcome["commits"]:
                    print(f"  committed {c}")
                if outcome["pushed"]:
                    print("  and pushed")
                if not outcome["ok"]:
                    print("It stopped on a git failure:\n" + outcome["log"])
                answered = [s for s in outcome.get("answers", []) if s["decision"] != "not answered"]
                if answered:
                    print("Their answers to your checks, given with the accept:")
                    self._print_answers(answered)
                return
            if a.wait and p.wait_replaced():
                print(f"A later `draft answers {p.name} --wait` has taken over from this one.")
                return
            sent = p.take_sent()
            if sent:
                break
            if not a.wait:
                print("No answers waiting.")
                return
            if DraftServer.stopped(p):
                print(f"The page server stopped without answers. `draft show {p.name}` starts it again.")
                return
            time.sleep(WAIT_POLL_SECONDS)
        print("The user's answers to your checks:")
        self._print_answers(sent)
        where = "the checkout, where this draft is" if p.in_place else "the worktree"
        print(f"Make the fixes in {where}, then update the notes: a new set of checks if any, and "
              "sinceLastReview saying what you changed for each fix. Don't raise the left ones again.")
        if any(s["decision"] == "leave" and s.get("note") for s in sent):
            print("A note on a check the user left is a request: the draft stays as it is on that "
                  "point, but do what the note asks. It may be outside the draft, such as raising a "
                  "ticket: anything outward-facing waits for the user's go-ahead in the chat. Say in "
                  "sinceLastReview what you did about each note.")

    @staticmethod
    def _print_answers(answers):
        for s in answers:
            note = f"  (note: {s['note']})" if s.get("note") else ""
            print(f"  {s['decision'].upper():13} {s['title']}{note}")
            if s["decision"] == "fix" and s.get("suggestion"):
                print(f"  {'':13} you suggested: {s['suggestion']}")

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
        if p.sent:
            print("ANSWERS FROM THE PAGE, NOT TAKEN YET: run draft answers")
        for item in p.left:
            print(f"left by the user, don't raise again: {item['title']}" + (f" ({item['note']})" if item.get("note") else ""))
        if not p.notes:
            print("notes: none yet (draft notes --format)")
        else:
            missing = p.notes.unnoted(sorted(f.path for f in v.drafted.files))
            if missing:
                print("notes: no file note for " + ", ".join(missing))

    def cmd_accept(self, a):
        p = Draft.find(self.root, a.name)
        DraftServer.stop(p)
        in_place = p.in_place
        files = p.accept()
        print(f"Accepted '{p.name}': it was made in place, so it is already in {self.root} (uncommitted):"
              if in_place else f"Applied '{p.name}' to {self.root} (uncommitted):")
        for f in files:
            print("  " + f)

    def cmd_drop(self, a):
        p = Draft.find(self.root, a.name)
        DraftServer.stop(p)
        in_place = p.exists() and p.in_place
        kept = p.drop()
        print(f"Dropped '{p.name}'." + (" The checkout is back as it was when the draft started."
                                        if in_place else ""))
        if kept:
            print(f"The change taken out is kept in {kept}\n"
                  f"(`git apply` on it puts it back), until a draft of the same name starts.")

    @staticmethod
    def _server_state(live):
        if not live:
            return "server stopped"
        quiet = (time.time() - live["seen"]) / 60
        # An open page asks every 2 seconds, or about once a minute in a background tab.
        page = "page open" if quiet < 3 else f"no page open for {quiet:.0f} min"
        return f"page server on port {live['port']}, {page}"

    def cmd_list(self, a):
        drafts = Draft.all(self.root)
        for p in drafts:
            m = p.meta
            live = DraftServer.running(p)
            days = (time.time() - m.get("started", time.time())) / 86400
            age = f"{days:.0f} days old" if days >= 1 else "started today"
            state = self._server_state(live)
            place = "in place, " if p.in_place else ""
            print(f"{p.name}  {m.get('summary', '')}  ({place}{age}, {state})")
        if not drafts:
            print("no drafts")


if __name__ == "__main__":
    DraftCli().run(sys.argv[1:])