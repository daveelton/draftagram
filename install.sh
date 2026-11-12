#!/bin/sh
# Installs draft on this machine, or repairs an install: run it again after moving the repo,
# or after a pull that adds a dependency.
#
# draft gets a Python environment of its own in .venv, beside this script, and the `draft`
# command runs that Python directly. Running draft.py through `uv run` instead would have uv
# open its shared cache in ~/.cache/uv on every command, which Claude Code's Bash sandbox
# refuses. Allowing that write would let a sandboxed command change packages that other
# projects later run unsandboxed, so draft stays out of the cache once it is installed.
set -eu

here=$(cd "$(dirname "$0")" && pwd)
bin="$HOME/.local/bin"
skills="$HOME/.claude/skills"

if ! command -v uv >/dev/null 2>&1; then
    echo "draft needs uv to build its environment: brew install uv" >&2
    exit 1
fi

# The packages listed here are the ones in the dependencies line at the top of draft.py.
uv venv --quiet --allow-existing --python ">=3.11" "$here/.venv"
uv pip install --quiet --python "$here/.venv/bin/python" libclang

mkdir -p "$bin" "$skills"

# Earlier installs made this a link to draft.py. Remove it first: writing through the link
# would replace draft.py with the launcher.
rm -f "$bin/draft"
cat > "$bin/draft" <<EOF
#!/bin/sh
exec "$here/.venv/bin/python" "$here/draft.py" "\$@"
EOF
chmod +x "$bin/draft"

# A link, not a copy, so a git pull updates the skill.
ln -sfn "$here/skill" "$skills/draft"

"$bin/draft" --help >/dev/null
echo "draft is installed: $bin/draft runs $here/draft.py"

case ":$PATH:" in
    *":$bin:"*) ;;
    *) echo "Add $bin to PATH to run it as 'draft'." ;;
esac
