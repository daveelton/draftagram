"""How the API changed between two states: per class and per member, added, removed or
changed. A member is changed when its declaration reads differently, or when the diff
touches its declaration or one of its bodies (so a body-only edit still shows)."""

ADDED, REMOVED, CHANGED = "added", "removed", "changed"


class ApiDiff:
    def __init__(self, old, new, old_ranges, new_ranges):
        self.old, self.new = old, new
        self.classes = {}   # {class name: status}
        self.members = {}   # {(class name, member key): status}
        for name, c in new.classes.items():
            before = old.classes.get(name)
            if before is None:
                self.classes[name] = ADDED
                for key in c.members:
                    self.members[(name, key)] = ADDED
                continue
            for key, m in c.members.items():
                was = before.members.get(key)
                if was is None:
                    self.members[(name, key)] = ADDED
                elif was.label != m.label or m.touches(new_ranges) or was.touches(old_ranges):
                    self.members[(name, key)] = CHANGED
            for key in before.members:
                if key not in c.members:
                    self.members[(name, key)] = REMOVED
            if before.bases != c.bases or any(k[0] == name for k in self.members) or \
                    c.touches(new_ranges):
                self.classes[name] = CHANGED
        for name, c in old.classes.items():
            if name not in new.classes:
                self.classes[name] = REMOVED
                for key in c.members:
                    self.members[(name, key)] = REMOVED

    def class_status(self, name):
        return self.classes.get(name)

    def member_status(self, name, key):
        return self.members.get((name, key))

    def summary(self):
        """Plain lines, one per changed class: what a reviewer reads before the diagram."""
        lines = []
        for name in sorted(self.classes):
            status = self.classes[name]
            if status in (ADDED, REMOVED):
                lines.append(f"{name}: new class" if status == ADDED else f"{name}: removed")
                continue
            parts = []
            for (cls, key), st in sorted(self.members.items()):
                if cls == name:
                    parts.append({ADDED: "+ ", REMOVED: "− ", CHANGED: "~ "}[st] + key)
            old_b, new_b = self.old.classes[name].bases, self.new.classes[name].bases
            if old_b != new_b:
                parts.append(f"bases {', '.join(old_b) or 'none'} → {', '.join(new_b) or 'none'}")
            lines.append(f"{name}: " + ("; ".join(parts) if parts else "edited outside its members"))
        return lines