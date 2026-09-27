#!/bin/bash
# Set up Snake vs. Ball on a Mac that may have nothing installed yet.
#
#   ./install.sh            check/install Python 3, verify, add a `snake` launcher
#   ./install.sh --no-link  same, but don't create the launcher
#
# Safe to re-run; every step is a no-op once it has been done.

set -euo pipefail

MIN_MAJOR=3
MIN_MINOR=8
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
GAME="$HERE/snake.py"
BIN_DIR="$HOME/.local/bin"
LINK="$BIN_DIR/snake"
MAKE_LINK=1
[[ "${1:-}" == "--no-link" ]] && MAKE_LINK=0

say()  { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33mwarning:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[[ "$(uname -s)" == "Darwin" ]] || warn "This script is written for macOS; continuing anyway."
[[ -f "$GAME" ]] || die "snake.py not found next to this script ($HERE)."

# -- 1. find a usable python3 -----------------------------------------------

# Prints the path of a python3 that is new enough and has curses, or nothing.
find_python() {
    local candidate
    for candidate in python3 /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
        command -v "$candidate" >/dev/null 2>&1 || continue
        # Apple ships a /usr/bin/python3 stub that only offers to install the
        # Command Line Tools; running it non-interactively fails, which is
        # exactly what this check catches.
        if "$candidate" - "$MIN_MAJOR" "$MIN_MINOR" <<'PY' >/dev/null 2>&1
import sys
major, minor = int(sys.argv[1]), int(sys.argv[2])
sys.exit(0 if sys.version_info >= (major, minor) else 1)
PY
        then
            if "$candidate" -c "import curses" >/dev/null 2>&1; then
                command -v "$candidate"
                return 0
            fi
        fi
    done
    return 1
}

PY="$(find_python || true)"

if [[ -z "$PY" ]]; then
    say "No Python $MIN_MAJOR.$MIN_MINOR+ with curses found; installing one."
    if command -v brew >/dev/null 2>&1; then
        say "Homebrew is present: brew install python"
        brew install python
    elif ! xcode-select -p >/dev/null 2>&1; then
        say "Requesting Apple's Command Line Tools (this opens a system dialog)."
        xcode-select --install 2>/dev/null || true
        echo
        echo "  Finish the Command Line Tools installer, then run ./install.sh again."
        echo "  (Alternatively install Homebrew from https://brew.sh and re-run.)"
        exit 2
    else
        die "Command Line Tools are installed but no suitable python3 was found. Install Homebrew (https://brew.sh), then re-run."
    fi
    PY="$(find_python || true)"
    [[ -n "$PY" ]] || die "Python was installed but still isn't usable from this shell. Open a new terminal and re-run."
fi

say "Using $PY ($("$PY" -c 'import sys; print(sys.version.split()[0])'))"

# -- 2. dependencies (none, but honour the file) -----------------------------

if grep -Ev '^\s*(#|$)' "$HERE/requirements.txt" >/dev/null 2>&1; then
    say "Installing requirements"
    "$PY" -m pip install --user -r "$HERE/requirements.txt"
else
    say "No third-party packages required (standard library only)"
fi

# -- 3. verify ---------------------------------------------------------------

chmod +x "$GAME"
say "Running the game's built-in checks"
"$PY" "$GAME" --selftest | tail -n 1

# -- 4. launcher --------------------------------------------------------------

if [[ "$MAKE_LINK" == 1 ]]; then
    mkdir -p "$BIN_DIR"
    # A tiny wrapper rather than a symlink, so the interpreter that passed the
    # checks above is the one that runs the game.
    cat > "$LINK" <<WRAP
#!/bin/bash
exec "$PY" "$GAME" "\$@"
WRAP
    chmod +x "$LINK"
    say "Launcher written to $LINK"
    case ":$PATH:" in
        *":$BIN_DIR:"*) ;;
        *)
            shell_rc="$HOME/.zshrc"
            [[ "${SHELL:-}" == */bash ]] && shell_rc="$HOME/.bash_profile"
            if ! grep -qs 'HOME/.local/bin' "$shell_rc"; then
                printf '\n# added by snake/install.sh\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$shell_rc"
                say "Added ~/.local/bin to PATH in $shell_rc (takes effect in a new terminal)"
            fi
            ;;
    esac
fi

echo
say "Done.  Play with:  snake        (or: $PY $GAME)"
echo "    High scores:   snake --scores"
