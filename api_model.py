"""The API shape of a C++ project at one state, read with Apple's libclang.

A state is the project's files on disk with some files' contents swapped (`overrides`), so
HEAD, the working tree and a draft all parse with the same compile flags and paths.
"""
import copy
import json
import os
import re
import shlex
import subprocess
from dataclasses import dataclass, field

import clang.cindex as ci

LIBCLANG = "/Library/Developer/CommandLineTools/usr/lib/libclang.dylib"
if not os.path.exists(LIBCLANG):
    LIBCLANG = ("/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain"
                "/usr/lib/libclang.dylib")
ci.Config.set_library_file(LIBCLANG)

K = ci.CursorKind
CLASS_KINDS = {K.CLASS_DECL, K.STRUCT_DECL, K.CLASS_TEMPLATE, K.UNION_DECL}
METHOD_KINDS = {K.CXX_METHOD, K.CONSTRUCTOR, K.DESTRUCTOR, K.FUNCTION_TEMPLATE,
                K.CONVERSION_FUNCTION}
SOURCE_EXTS = (".cpp", ".cc", ".cxx", ".mm", ".m", ".c")
HEADER_EXTS = (".h", ".hpp", ".hh", ".hxx", ".inl")
MACRO_CALL = re.compile(r"^\s*[A-Z][A-Z0-9_]{3,}\s*\(")   # JUCE_DECLARE_..._DETECTOR (X)
ACCESS = {"public": "+", "protected": "#", "private": "-"}


@dataclass
class Member:
    key: str            # name plus parameter types: overloads are separate members
    label: str          # what the diagram shows
    kind: str           # "method" or "field"
    access: str
    file: str
    lines: tuple        # declaration's first and last line
    impl: list = field(default_factory=list)   # [(file, first, last)] of out-of-line bodies
    refs: set = field(default_factory=set)     # project classes named in its type or signature

    def touches(self, ranges):
        """True when any of `ranges` ({file: [(first, last)]}) overlaps this member's
        declaration or one of its bodies."""
        spans = [(self.file, *self.lines)] + list(self.impl)
        return any(a <= hi and lo <= b
                   for f, a, b in spans for lo, hi in ranges.get(f, ()))


@dataclass
class ApiClass:
    name: str           # qualified, e.g. ursa::Resonator
    kind: str
    file: str
    lines: tuple
    bases: list = field(default_factory=list)
    members: dict = field(default_factory=dict)
    owns: set = field(default_factory=set)     # project classes held by value
    refers: set = field(default_factory=set)   # held by pointer, reference or smart pointer
    uses: set = field(default_factory=set)     # appear in method signatures

    def related(self):
        return set(self.bases) | self.owns | self.refers | self.uses

    def related_via(self, ranges):
        """Relations seen through the parts of the class in `ranges` only: for a class Claude
        read a few lines of, the neighbours those lines lead to, not the whole class's."""
        out = set(self.bases) if any(a <= self.lines[0] + 2 and self.lines[0] <= b
                                     for a, b in ranges.get(self.file, ())) else set()
        for m in self.members.values():
            if m.touches(ranges):
                out |= m.refs
        return out & self.related()

    def touches(self, ranges):
        return any(a <= hi and lo <= b for lo, hi in ranges.get(self.file, ())
                   for a, b in [self.lines]) or any(m.touches(ranges)
                                                    for m in self.members.values())


class CompileDatabase:
    """Compile flags for the project's files, from CMake's compile_commands.json."""

    def __init__(self, root):
        self.root = root
        self.path = self._find(root)
        self.entries = {}
        if self.path:
            for e in json.load(open(self.path)):
                f = os.path.normpath(os.path.join(e["directory"], e["file"]))
                self.entries[f] = e
        self.sdk = subprocess.run(["xcrun", "--show-sdk-path"], capture_output=True,
                                  text=True).stdout.strip()

    @staticmethod
    def _find(root):
        for d in ("cmake-build-debug", "build", "cmake-build-release", "."):
            p = os.path.join(root, d, "compile_commands.json")
            if os.path.exists(p):
                return p
        for d in sorted(os.listdir(root)):
            p = os.path.join(root, d, "compile_commands.json")
            if os.path.exists(p):
                return p
        return None

    def args_for(self, entry):
        """Clang arguments from an entry, minus what libclang can't take: the output, the
        input itself and a second `-arch` (two arches make two compile jobs)."""
        raw = entry.get("arguments") or shlex.split(entry["command"])
        out, skip = [], False
        for a in raw[1:]:
            if skip:
                skip = False
                continue
            if a in ("-o", "-c", "-arch", "-MF", "-MT", "-MQ"):
                skip = True
                continue
            if a.startswith("-fcolor") or a in ("-MD", "-MMD") or \
                    os.path.normpath(os.path.join(entry["directory"], a)) == \
                    os.path.normpath(os.path.join(entry["directory"], entry["file"])):
                continue
            if a.startswith("-I") and len(a) > 2 and not os.path.isabs(a[2:]):
                a = "-I" + os.path.normpath(os.path.join(entry["directory"], a[2:]))
            out.append(a)
        if self.sdk and "-isysroot" not in out:
            out += ["-isysroot", self.sdk]
        return out

    def translation_units(self, files):
        """[(source path, entry, extra includes)] to parse so every file in `files` is seen.
        A source file is its own unit. A header is seen through the source file of the same
        name if there is one, else through a probe unit that includes it, built with the
        flags of a source file from the nearest directory."""
        units, headers = {}, []
        for f in sorted(files):
            if f.endswith(SOURCE_EXTS) and f in self.entries:
                units.setdefault(f, [])
            elif f.endswith(HEADER_EXTS):
                stem = os.path.splitext(f)[0]
                twin = next((s for s in self.entries if os.path.splitext(s)[0] == stem), None)
                if twin:
                    units.setdefault(twin, [])
                else:
                    headers.append(f)
        result = [(src, self.entries[src], []) for src in units]
        if headers and self.entries:
            near = self._nearest(headers[0])
            result.append((os.path.join(self.root, "__draft_probe.cpp"), near, headers))
        return result

    def _nearest(self, path):
        d = os.path.dirname(path)
        while True:
            for s, e in self.entries.items():
                if os.path.dirname(s) == d and s.endswith(SOURCE_EXTS):
                    return e
            if d in (self.root, "/"):
                return next(iter(self.entries.values()))
            d = os.path.dirname(d)


class ApiModel:
    """Every project class visible from the parsed units, keyed by qualified name."""

    def __init__(self, project_files):
        self.project_files = project_files   # set of absolute paths that are the project's
        self.classes = {}
        self.diagnostics = []
        self.texts = {}         # file contents in this state, for spotting macro-made members

    @classmethod
    def parse_unit(cls, db, unit, project_files, overrides):
        """Parse one unit with `overrides` ({path: text}) in place of what is on disk.
        Returns the model of what it declares, and the set of files it included."""
        src, entry, includes = unit
        model = cls(project_files)
        model.texts = {p: t.splitlines() for p, t in overrides.items() if t is not None}
        args = db.args_for(entry)
        unsaved = [(p, t) for p, t in overrides.items() if t is not None]
        if includes:
            probe = ["#include <JuceHeader.h>"] if any("JuceLibraryCode" in a for a in args) else []
            probe += [f'#include "{h}"' for h in includes]
            unsaved.append((src, "\n".join(probe) + "\n"))
        cwd = os.getcwd()
        os.chdir(entry["directory"])
        try:
            tu = ci.Index.create().parse(
                src, args=args, unsaved_files=unsaved,
                options=ci.TranslationUnit.PARSE_SKIP_FUNCTION_BODIES |
                ci.TranslationUnit.PARSE_INCOMPLETE)
        except ci.TranslationUnitLoadError:
            model.diagnostics.append(f"{os.path.relpath(src, db.root)}: failed to parse")
            return model, set()
        finally:
            os.chdir(cwd)
        errors = [d for d in tu.diagnostics if d.severity >= ci.Diagnostic.Error]
        if errors:
            model.diagnostics.append(
                f"{os.path.relpath(src, db.root)}: {len(errors)} errors, first: {errors[0]}")
        model._walk(tu.cursor, [])
        included = {os.path.normpath(i.include.name) for i in tu.get_includes()} | {src}
        return model, included

    @classmethod
    def merge(cls, project_files, parts, absent=()):
        """One model from per-unit models. Classes in `absent` files (files that don't exist
        in this state) are left out."""
        model = cls(project_files)
        for part in parts:
            model.diagnostics += part.diagnostics
            for name, c in part.classes.items():
                if c.file in absent:
                    continue
                have = model.classes.get(name)
                if have is None:
                    model.classes[name] = copy.deepcopy(c)   # parts are cached: keep them intact
                    continue
                for key, m in c.members.items():
                    if key in have.members:
                        for span in m.impl:
                            if span not in have.members[key].impl:
                                have.members[key].impl.append(span)
        model._link()
        return model

    def _in_project(self, cursor):
        f = cursor.location.file
        return f is not None and os.path.normpath(f.name) in self.project_files

    def _walk(self, cursor, scope):
        for c in cursor.get_children():
            if not self._in_project(c):
                continue
            if c.kind == K.NAMESPACE:
                self._walk(c, scope + [c.spelling])
            elif c.kind in CLASS_KINDS and c.is_definition() and c.spelling:
                self._add_class(c, scope)
            elif c.kind in METHOD_KINDS:
                self._add_body(c)     # declared outside its class: an out-of-line body

    def _qualified(self, cursor):
        parts, c = [], cursor
        while c is not None and c.kind != K.TRANSLATION_UNIT:
            if c.spelling:
                parts.append(c.spelling)
            c = c.semantic_parent
        return "::".join(reversed(parts))

    def _add_class(self, cursor, scope):
        name = self._qualified(cursor)
        if name in self.classes:
            return
        ext = cursor.extent
        cls = ApiClass(name, "struct" if cursor.kind == K.STRUCT_DECL else "class",
                       os.path.normpath(ext.start.file.name), (ext.start.line, ext.end.line))
        self.classes[name] = cls
        for c in cursor.get_children():
            if c.kind == K.CXX_BASE_SPECIFIER:
                decl = c.type.get_declaration()
                cls.bases.append(self._qualified(decl) if decl.kind != K.NO_DECL_FOUND
                                 else c.type.spelling)
            elif c.kind in CLASS_KINDS and c.is_definition() and c.spelling:
                self._add_class(c, scope + [cursor.spelling])
            elif c.kind in METHOD_KINDS or c.kind == K.FIELD_DECL or c.kind == K.VAR_DECL:
                self._add_member(cls, c)

    def _line(self, path, n):
        if path not in self.texts:
            try:
                self.texts[path] = open(path, errors="replace").read().splitlines()
            except OSError:
                self.texts[path] = []
        lines = self.texts[path]
        return lines[n - 1] if 0 < n <= len(lines) else ""

    def _body_end(self, path, first):
        """The line a method's body ends on. Parsing skips bodies, which leaves a method's
        extent at its signature, so match braces in the text from there: up to the first `;`
        means no body here."""
        depth, n = 0, first
        while True:
            line = self._line(path, n)
            if n > first + 5000 or (not line and n > len(self.texts.get(path, ()))):
                return first
            code = re.sub(r'"(\\.|[^"\\])*"|\'(\\.|[^\'\\])*\'', "", line.split("//")[0])
            for ch in code:
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        return n
                elif ch == ";" and depth == 0:
                    return first
            n += 1

    def _from_macro(self, c):
        """True for members a macro wrote (JUCE_DECLARE_NON_COPYABLE_WITH_LEAK_DETECTOR's copy
        constructor and leak detector): their name isn't in the source line they sit on."""
        loc = c.location
        if loc.file is None:
            return False
        line = self._line(os.path.normpath(loc.file.name), loc.line)
        return c.spelling not in line or bool(MACRO_CALL.match(line))

    def _add_member(self, cls, c):
        if c.is_implicit() if hasattr(c, "is_implicit") else False:
            return
        if self._from_macro(c):
            return
        access = ACCESS.get(c.access_specifier.name.lower(), " ")
        ext = c.extent
        if ext.start.file is None:  # a member with no source of its own, as _from_macro allows
            return
        f = os.path.normpath(ext.start.file.name)
        if c.kind in (K.FIELD_DECL, K.VAR_DECL):
            static = "static " if c.kind == K.VAR_DECL else ""
            m = Member(c.spelling, f"{static}{c.spelling} : {c.type.spelling}",
                       "field", access, f, (ext.start.line, ext.end.line))
            cls.members[c.spelling] = m
            self._note_type(cls, c.type, "field", member=m)
            return
        params = [a.type.spelling for a in c.get_arguments() or []]
        key = f"{c.spelling}({', '.join(params)})"
        ret = "" if c.kind in (K.CONSTRUCTOR, K.DESTRUCTOR) else f" : {c.result_type.spelling}"
        quals = (" const" if c.is_const_method() else "") + \
                (" = 0" if c.is_pure_virtual_method() else "")
        virtual = "virtual " if c.is_virtual_method() else ("static " if c.is_static_method() else "")
        m = Member(key, f"{virtual}{key}{quals}{ret}", "method", access, f,
                   (ext.start.line, max(ext.end.line, self._body_end(f, ext.start.line))))
        cls.members[key] = m
        for a in c.get_arguments() or []:
            self._note_type(cls, a.type, "use", member=m)
        if ret:
            self._note_type(cls, c.result_type, "use", member=m)

    def _add_body(self, c):
        """An out-of-line method body: attach its lines to the member it defines, so a change
        to the body marks that member."""
        parent = c.semantic_parent
        if parent is None or parent.kind not in CLASS_KINDS:
            return
        cls = self.classes.get(self._qualified(parent))
        if cls is None:
            return
        params = [a.type.spelling for a in c.get_arguments() or []]
        key = f"{c.spelling}({', '.join(params)})"
        m = cls.members.get(key)
        if m:
            ext = c.extent
            f = os.path.normpath(ext.start.file.name)
            m.impl.append((f, ext.start.line, max(ext.end.line, self._body_end(f, ext.start.line))))

    def _note_type(self, cls, t, how, indirect=False, member=None):
        """Record project classes named in a type, unwrapping pointers, references and
        template arguments (so std::unique_ptr<Foo> refers to Foo)."""
        t = t.get_canonical()
        if t.kind in (ci.TypeKind.POINTER, ci.TypeKind.LVALUEREFERENCE, ci.TypeKind.RVALUEREFERENCE):
            return self._note_type(cls, t.get_pointee(), how, True, member)
        if t.kind in (ci.TypeKind.CONSTANTARRAY, ci.TypeKind.INCOMPLETEARRAY):
            return self._note_type(cls, t.element_type, how, indirect, member)
        decl = t.get_declaration()
        if decl.kind in CLASS_KINDS or decl.kind == K.CLASS_TEMPLATE:
            if self._in_project(decl):
                name = self._qualified(decl)
                if name != cls.name:
                    if member is not None:
                        member.refs.add(name)
                    if how == "use":
                        cls.uses.add(name)
                    elif indirect:
                        cls.refers.add(name)
                    else:
                        cls.owns.add(name)
            for i in range(max(t.get_num_template_arguments(), 0)):
                self._note_type(cls, t.get_template_argument_type(i), how,
                                indirect or not self._in_project(decl), member)

    def _link(self):
        """Keep only relations to classes this model knows."""
        known = set(self.classes)
        for cls in self.classes.values():
            cls.owns &= known
            cls.refers &= known
            cls.uses &= known
            cls.uses -= cls.owns | cls.refers | set(cls.bases)
            cls.refers -= cls.owns