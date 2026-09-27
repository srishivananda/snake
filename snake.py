#!/usr/bin/env python3
"""Snake vs. Ball -- a terminal territory-capture game.

The playfield is sized to your terminal.  Steer the snake with the arrow keys
(or WASD).  Leaving solid ground starts carving a trail through the unpainted
field; the moment the trail touches solid ground again, whichever side of the
field does *not* hold a bouncing ball gets filled in.

Let a ball touch your trail and you lose a life.  Paint 75% of the field to
clear the level.  A score good enough for the top ten earns a spot on the
high-score table, which lives in $XDG_DATA_HOME/snake/highscores.json
(or ~/.local/share/snake/highscores.json); set SNAKE_SCORES to move it.

    python3 snake.py            play
    python3 snake.py --scores   print the high-score table
    python3 snake.py --selftest run the headless logic checks
"""

from __future__ import annotations

import curses
import json
import os
import random
import sys
import tempfile
import time
from pathlib import Path
from typing import NamedTuple

EMPTY, FILL, TRAIL, BORDER = 0, 1, 2, 3
SOLID = (FILL, BORDER)

TARGET_PCT = 75.0
START_LIVES, MAX_LIVES = 3, 5
PLAYER_INTERVAL = 0.05
FRAME = 0.012
DEATH_PAUSE, CLEAR_PAUSE = 1.2, 1.8
MIN_W, MIN_H = 20, 10

HIGH_SCORE_LIMIT = 10
NAME_MAX = 12
DEFAULT_NAME = "Anon"
SCORES_FILE_MAX_BYTES = 64 * 1024   # anything bigger is not a table we wrote
MAX_SCORE, MAX_LEVEL = 10**9, 10**4  # sanity bounds for values read from disk


class Ball:
    __slots__ = ("x", "y", "dx", "dy")

    def __init__(self, x, y, dx, dy):
        self.x, self.y, self.dx, self.dy = x, y, dx, dy


class Game:
    """All of the rules, with no terminal in sight so it stays testable."""

    def __init__(self, w, h, level=1, lives=START_LIVES, score=0, seed=None):
        self.w, self.h = w, h
        self.level = level
        self.lives = lives
        self.score = score
        self.rng = random.Random(seed)
        self.reset_level()

    # -- setup ------------------------------------------------------------

    def reset_level(self):
        w, h = self.w, self.h
        self.grid = [bytearray(w) for _ in range(h)]
        for x in range(w):
            self.grid[0][x] = BORDER
            self.grid[h - 1][x] = BORDER
        for y in range(h):
            self.grid[y][0] = BORDER
            self.grid[y][w - 1] = BORDER

        self.px, self.py = 0, 0
        self.launch = (0, 0)
        self.dx = self.dy = 0
        self.trail = []
        self.balls = [self._spawn_ball() for _ in range(self.level + 1)]
        self.ball_interval = max(0.030, 0.075 - 0.006 * (self.level - 1))

    def _spawn_ball(self):
        """Drop a ball on open ground, clear of the snake's starting corner."""
        x, y = self.w // 2, self.h // 2
        for _ in range(200):
            cx = self.rng.randrange(2, self.w - 2)
            cy = self.rng.randrange(2, self.h - 2)
            crowds_player = abs(cx - self.px) + abs(cy - self.py) < 8
            if self.grid[cy][cx] == EMPTY and not crowds_player:
                x, y = cx, cy
                break
        return Ball(x, y, self.rng.choice((-1, 1)), self.rng.choice((-1, 1)))

    # -- helpers ----------------------------------------------------------

    def cell(self, x, y):
        if 0 <= x < self.w and 0 <= y < self.h:
            return self.grid[y][x]
        return BORDER

    @property
    def interior(self):
        return (self.w - 2) * (self.h - 2)

    def filled_pct(self):
        return 100.0 * sum(row.count(FILL) for row in self.grid) / self.interior

    # -- the snake --------------------------------------------------------

    def set_dir(self, dx, dy):
        # Reversing into your own trail is instant death, so refuse it.
        if self.trail and dx == -self.dx and dy == -self.dy:
            return
        self.dx, self.dy = dx, dy

    def halt(self):
        if self.grid[self.py][self.px] in SOLID:
            self.dx = self.dy = 0

    def step_player(self):
        """Advance one cell.  Returns None, 'dead' or 'captured'."""
        if not (self.dx or self.dy):
            return None
        nx, ny = self.px + self.dx, self.py + self.dy
        if not (0 <= nx < self.w and 0 <= ny < self.h):
            self.dx = self.dy = 0  # ran into the edge of the world: stop
            return None

        ahead = self.grid[ny][nx]
        if ahead == TRAIL:
            return "dead"

        if ahead in SOLID:
            closing_loop = bool(self.trail)
            self.px, self.py = nx, ny
            if closing_loop:
                self.dx = self.dy = 0
                return "captured"
            return None

        # Stepping out into open field.
        if self.grid[self.py][self.px] in SOLID:
            self.launch = (self.px, self.py)
        self.px, self.py = nx, ny
        self.grid[ny][nx] = TRAIL
        self.trail.append((nx, ny))
        if any(b.x == nx and b.y == ny for b in self.balls):
            return "dead"
        return None

    # -- the balls --------------------------------------------------------

    def step_balls(self):
        """Move every ball.  Returns True if any of them hit the trail."""
        # Deliberately not short-circuiting: every ball still gets its move.
        return any([self._step_ball(b) for b in self.balls])

    def _step_ball(self, b):
        """Move one ball.  Returns True if it ran into the snake's trail."""
        side_x = self.cell(b.x + b.dx, b.y)
        side_y = self.cell(b.x, b.y + b.dy)
        corner = self.cell(b.x + b.dx, b.y + b.dy)
        if TRAIL in (side_x, side_y, corner):
            return True

        bounced = False
        if side_x in SOLID:
            b.dx = -b.dx
            bounced = True
        if side_y in SOLID:
            b.dy = -b.dy
            bounced = True
        if not bounced and corner in SOLID:
            b.dx, b.dy = -b.dx, -b.dy

        # The bounce may have turned the ball to face a cell nothing above
        # looked at, so re-check the square it is actually moving into.
        tx, ty = b.x + b.dx, b.y + b.dy
        target = self.cell(tx, ty)
        if target == TRAIL:
            return True
        if target == EMPTY:
            b.x, b.y = tx, ty
        return False

    # -- claiming territory -----------------------------------------------

    def capture(self):
        """Solidify the trail, then fill every pocket no ball can reach."""
        for x, y in self.trail:
            self.grid[y][x] = FILL
        gained = len(self.trail)
        self.trail.clear()

        w, h = self.w, self.h
        reach = [bytearray(w) for _ in range(h)]
        stack = []
        for b in self.balls:
            if self.grid[b.y][b.x] == EMPTY and not reach[b.y][b.x]:
                reach[b.y][b.x] = 1
                stack.append((b.x, b.y))
        while stack:
            x, y = stack.pop()
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if 0 <= nx < w and 0 <= ny < h:
                    if not reach[ny][nx] and self.grid[ny][nx] == EMPTY:
                        reach[ny][nx] = 1
                        stack.append((nx, ny))

        for row, seen in zip(self.grid, reach):
            for x in range(w):
                if row[x] == EMPTY and not seen[x]:
                    row[x] = FILL
                    gained += 1

        self.score += gained * 10 * self.level
        return gained

    def die(self):
        for x, y in self.trail:
            self.grid[y][x] = EMPTY
        self.trail.clear()
        self.px, self.py = self.launch
        self.dx = self.dy = 0
        self.lives -= 1


# ---------------------------------------------------------------------------
# high scores
# ---------------------------------------------------------------------------

class Entry(NamedTuple):
    name: str
    score: int
    level: int
    date: str


def clean_name(raw):
    """Printable ASCII only, trimmed to NAME_MAX; nothing left means DEFAULT_NAME."""
    kept = "".join(c for c in str(raw) if " " <= c <= "~")
    return kept.strip()[:NAME_MAX].strip() or DEFAULT_NAME


def default_scores_path():
    override = os.environ.get("SNAKE_SCORES")
    if override:
        return Path(override)
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "snake" / "highscores.json"


class HighScores:
    """The best few results, kept sorted and persisted as a small JSON file.

    The file is treated as untrusted input: anything missing, oversized,
    corrupt or oddly shaped is dropped rather than allowed to crash the game,
    and the next save writes a clean table.
    """

    def __init__(self, path, limit=HIGH_SCORE_LIMIT):
        self.path = Path(path)
        self.limit = limit
        self.entries = self._load()

    def _load(self):
        try:
            if self.path.stat().st_size > SCORES_FILE_MAX_BYTES:
                return []
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        if not isinstance(data, list):
            return []
        entries = [e for e in map(self._coerce, data) if e is not None]
        return self._top(entries)

    @staticmethod
    def _coerce(item):
        """One JSON object -> Entry, or None if it doesn't look like one of ours."""
        if not isinstance(item, dict):
            return None
        score, level = item.get("score"), item.get("level", 1)
        for value, upper in ((score, MAX_SCORE), (level, MAX_LEVEL)):
            # bool is an int subclass, and true/false is not a score.
            if isinstance(value, bool) or not isinstance(value, int):
                return None
            if not 0 <= value <= upper:
                return None
        if level < 1:
            return None
        date = item.get("date", "")
        if not (isinstance(date, str) and date.isascii() and date.isprintable()):
            date = ""
        return Entry(clean_name(item.get("name", "")), score, level, date[:10])

    def _top(self, entries):
        # Stable sort: on a tie the earlier entry keeps the higher rank.
        return sorted(entries, key=lambda e: (-e.score, -e.level))[:self.limit]

    @property
    def best(self):
        return self.entries[0].score if self.entries else 0

    def qualifies(self, score):
        if score <= 0:
            return False
        return len(self.entries) < self.limit or score > self.entries[-1].score

    def add(self, name, score, level):
        """Record a result.  Returns its 0-based rank, or None if it fell off."""
        entry = Entry(clean_name(name), score, level, time.strftime("%Y-%m-%d"))
        self.entries = self._top(self.entries + [entry])
        return self.entries.index(entry) if entry in self.entries else None

    def save(self):
        """Write via a temp file and rename so a crash can't leave a torn table."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps([e._asdict() for e in self.entries], indent=2) + "\n"
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".highscores-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, self.path)
        except OSError:
            Path(tmp).unlink(missing_ok=True)
            raise


def score_table(scores, highlight=None, rows=None):
    """The table as fixed-width lines; `highlight` marks one 0-based rank."""
    entries = scores.entries[:rows]
    if not entries:
        return ["No high scores yet"]
    return [f"{'>' if i == highlight else ' '}{i + 1:>2}. "
            f"{e.name:<{NAME_MAX}} {e.score:>8}  L{e.level}"
            for i, e in enumerate(entries)]


# -- typing a name -----------------------------------------------------------

BACKSPACE_KEYS = (curses.KEY_BACKSPACE, 127, 8)
ENTER_KEYS = (curses.KEY_ENTER, 10, 13)


def edit_name(typed, ch):
    """Apply one keypress to the name being entered.  Returns (typed, done)."""
    if ch in ENTER_KEYS:
        return typed, True
    if ch in BACKSPACE_KEYS:
        return typed[:-1], False
    if 32 <= ch <= 126 and len(typed) < NAME_MAX:
        return typed + chr(ch), False
    return typed, False


class Ending:
    """What's happening with the score prompt once the last life is gone."""

    __slots__ = ("typed", "rank", "saved")

    def __init__(self):
        self.typed = ""
        self.rank = None      # position on the table, once recorded
        self.saved = True     # False if writing the table to disk failed


# ---------------------------------------------------------------------------
# terminal rendering
# ---------------------------------------------------------------------------

ASCII_STYLE = {EMPTY: "  ", FILL: "::", TRAIL: "++", BORDER: "##"}
PLAYER_GLYPH, BALL_GLYPH = "[]", "()"


class Theme(NamedTuple):
    """Everything the renderer needs to know about looks."""

    cells: dict          # tile -> (glyph, attr)
    player: int
    ball: int
    hud: int


def build_theme():
    if not curses.has_colors():
        cells = {t: (glyph, curses.A_NORMAL) for t, glyph in ASCII_STYLE.items()}
        cells[BORDER] = (ASCII_STYLE[BORDER], curses.A_BOLD)
        return Theme(cells, curses.A_BOLD, curses.A_REVERSE, curses.A_NORMAL)

    curses.start_color()
    bg = curses.COLOR_BLACK
    try:
        curses.use_default_colors()
        bg = -1
    except curses.error:
        pass
    palette = ((curses.COLOR_BLACK, curses.COLOR_WHITE),   # 1 border
               (curses.COLOR_BLACK, curses.COLOR_BLUE),    # 2 filled
               (curses.COLOR_BLACK, curses.COLOR_YELLOW),  # 3 trail
               (curses.COLOR_BLACK, curses.COLOR_GREEN),   # 4 snake
               (curses.COLOR_WHITE, curses.COLOR_RED),     # 5 ball
               (curses.COLOR_CYAN, bg))                    # 6 hud
    for pair, (fg, back) in enumerate(palette, start=1):
        curses.init_pair(pair, fg, back)

    cells = {EMPTY: ("  ", curses.color_pair(0)),
             BORDER: ("  ", curses.color_pair(1)),
             FILL: ("  ", curses.color_pair(2)),
             TRAIL: ("  ", curses.color_pair(3))}
    return Theme(cells,
                 curses.color_pair(4) | curses.A_BOLD,
                 curses.color_pair(5) | curses.A_BOLD,
                 curses.color_pair(6))


def put(stdscr, y, x, text, attr):
    """addstr that shrugs off writes running off the edge of the screen."""
    try:
        stdscr.addstr(y, x, text, attr)
    except curses.error:
        pass


def draw(stdscr, g, theme, best):
    for y, row in enumerate(g.grid):
        x = 0
        while x < g.w:                      # one addstr per run of like tiles
            end = x + 1
            while end < g.w and row[end] == row[x]:
                end += 1
            glyph, attr = theme.cells[row[x]]
            put(stdscr, y, x * 2, glyph * (end - x), attr)
            x = end

    for b in g.balls:
        put(stdscr, b.y, b.x * 2, BALL_GLYPH, theme.ball)
    put(stdscr, g.py, g.px * 2, PLAYER_GLYPH, theme.player)

    hud = (f" Level {g.level}  Lives {'*' * max(g.lives, 0):<{MAX_LIVES}}  "
           f"Painted {g.filled_pct():5.1f}% / {TARGET_PCT:.0f}%  "
           f"Score {g.score:<7}  Best {best:<7}  "
           f"arrows/wasd move  space stop  p pause  q quit")
    width = g.w * 2 - 1
    put(stdscr, g.h, 0, hud[:width].ljust(width), theme.hud)


def draw_box(stdscr, lines, attr):
    """Centre a bordered panel over whatever is already on screen."""
    height, width = stdscr.getmaxyx()
    inner = max(len(s) for s in lines)
    box_w = inner + 4
    top = max(0, (height - (len(lines) + 2)) // 2)
    left = max(0, (width - box_w) // 2)
    edge = "+" + "-" * (box_w - 2) + "+"
    rows = [edge, *("| " + s.center(inner) + " |" for s in lines), edge]
    for i, text in enumerate(rows):
        put(stdscr, top + i, left, text[:max(0, width - left - 1)], attr)


def overlay(g, state, theme, scores, ending):
    """The banner for a non-playing state, as (lines, attr), or None."""
    if state == "naming":
        field = f"[ {ending.typed + '_':<{NAME_MAX + 1}} ]"
        return (["N E W   H I G H   S C O R E", "",
                 f"Level {g.level}   Score {g.score}", "",
                 "Enter your name:", field, "",
                 "Enter to save   Backspace to fix"], theme.player)
    if state == "over":
        # Every line outside the table, plus the box edges, needs a row of
        # its own; whatever is left of the screen goes to the table.
        rows = max(3, g.h + 1 - 11)
        lines = ["G A M E   O V E R", "",
                 f"Level {g.level}   Score {g.score}", "",
                 "H I G H   S C O R E S",
                 *score_table(scores, ending.rank, rows), ""]
        if not ending.saved:
            lines += ["Could not save the table to disk", ""]
        return (lines + ["r to restart   q to quit"], theme.ball)
    if state == "ready":
        return (["S N A K E   v s   B A L L", "",
                 "Carve through the dark field and close the loop.",
                 "The side without a ball gets painted.",
                 "Never let a ball touch your trail.", "",
                 f"Paint {TARGET_PCT:.0f}% to clear the level.",
                 "", "Press any key to start"], theme.hud)
    if state == "paused":
        return (["P A U S E D", "", "p to resume"], theme.hud)
    if state == "dying":
        return (["C A U G H T !", "",
                 f"{g.lives} live{'' if g.lives == 1 else 's'} left"], theme.ball)
    if state == "cleared":
        return ([f"L E V E L  {g.level}  C L E A R", "",
                 f"{g.filled_pct():.1f}% painted",
                 f"Score {g.score}"], theme.player)
    return None


# ---------------------------------------------------------------------------
# input
# ---------------------------------------------------------------------------

_MOVES = ((curses.KEY_UP, "w", (0, -1)), (curses.KEY_DOWN, "s", (0, 1)),
          (curses.KEY_LEFT, "a", (-1, 0)), (curses.KEY_RIGHT, "d", (1, 0)))
DIRS = {key: delta for arrow, letter, delta in _MOVES
        for key in (arrow, ord(letter), ord(letter.upper()))}

# Final byte of a cursor-key escape sequence -> the curses key it stands for.
CSI_ARROWS = {ord("A"): curses.KEY_UP, ord("B"): curses.KEY_DOWN,
              ord("C"): curses.KEY_RIGHT, ord("D"): curses.KEY_LEFT}


def read_keys(stdscr):
    """Drain the input queue into key codes.

    ncurses assembles cursor keys itself -- even in nodelay mode, where it
    holds a half-arrived escape sequence until the rest shows up.  It only
    recognises the spelling its terminfo entry advertises, though, so a
    terminal that reports arrows as ESC [ C where terminfo says ESC O C would
    otherwise surface as stray letters.  Fold those few bytes by hand.
    """
    raw = []
    while (ch := stdscr.getch()) != -1:
        raw.append(ch)

    keys, i = [], 0
    while i < len(raw):
        if (raw[i] == 27 and i + 2 < len(raw)
                and raw[i + 1] in (ord("["), ord("O"))):
            keys.append(CSI_ARROWS.get(raw[i + 2]))
            i += 3
        else:
            keys.append(raw[i])
            i += 1
    return [k for k in keys if k is not None]


# ---------------------------------------------------------------------------
# the game loop
# ---------------------------------------------------------------------------

def geometry(stdscr):
    height, width = stdscr.getmaxyx()
    return width // 2, height - 1


def run(stdscr, scores):
    curses.curs_set(0)
    stdscr.nodelay(True)
    stdscr.keypad(True)
    theme = build_theme()

    w, h = geometry(stdscr)
    if w < MIN_W or h < MIN_H:
        stdscr.nodelay(False)
        draw_box(stdscr, ["Terminal too small.",
                          f"Need at least {MIN_W * 2}x{MIN_H + 1} characters.",
                          "", "Press any key."], theme.hud)
        stdscr.getch()
        return

    g = Game(w, h)
    state = "ready"
    ending = None
    resume_at = 0.0
    next_player = next_ball = time.monotonic()

    while True:
        now = time.monotonic()

        for ch in read_keys(stdscr):
            if ch == curses.KEY_RESIZE:
                w, h = geometry(stdscr)
                if state in ("naming", "over"):
                    # A finished game keeps its board, however small the
                    # window gets: quitting here would drop an unsaved score.
                    pass
                elif w < MIN_W or h < MIN_H:
                    return
                else:
                    g = Game(w, h, g.level, g.lives, g.score)
                    state, resume_at = "ready", 0.0
                stdscr.clear()
            elif state == "naming":
                # Every key is text here -- 'q' is a letter, not quit.
                ending.typed, done = edit_name(ending.typed, ch)
                if done:
                    ending.rank = scores.add(ending.typed, g.score, g.level)
                    try:
                        scores.save()
                    except OSError:
                        ending.saved = False
                    state = "over"
                    stdscr.clear()
            elif ch in (ord("q"), ord("Q")):
                return
            elif state == "ready":
                state = "play"
                next_player = next_ball = now
            elif state == "over":
                if ch in (ord("r"), ord("R")):
                    if w < MIN_W or h < MIN_H:
                        return  # shrunk while the table was up; can't restart
                    g = Game(w, h)
                    state, ending = "ready", None
                    stdscr.clear()
            elif ch in (ord("p"), ord("P")):
                if state == "play":
                    state = "paused"
                elif state == "paused":
                    state = "play"
                    next_player = next_ball = now
            elif state == "play":
                if ch in DIRS:
                    g.set_dir(*DIRS[ch])
                elif ch == ord(" "):
                    g.halt()

        if state in ("dying", "cleared") and now >= resume_at:
            if state == "cleared":
                g.level += 1
                g.lives = min(g.lives + 1, MAX_LIVES)
                g.reset_level()
            state = "play"
            next_player = next_ball = now
            stdscr.clear()

        if state == "play":
            # After a stall (SIGSTOP, a slow redraw) don't replay the backlog.
            if now - next_player > 0.5:
                next_player = next_ball = now

            result = None
            while result is None and now >= next_player:
                next_player += PLAYER_INTERVAL
                result = g.step_player()
            if result == "captured":
                g.capture()

            if result == "dead":
                g.die()
                state, resume_at = "dying", now + DEATH_PAUSE
            elif g.filled_pct() >= TARGET_PCT:
                g.score += 500 * g.level
                state, resume_at = "cleared", now + CLEAR_PAUSE
            else:
                while now >= next_ball:
                    next_ball += g.ball_interval
                    if g.step_balls():
                        g.die()
                        state, resume_at = "dying", now + DEATH_PAUSE
                        break

            if g.lives <= 0:
                ending = Ending()
                state = "naming" if scores.qualifies(g.score) else "over"
                stdscr.clear()

        draw(stdscr, g, theme, scores.best)
        banner = overlay(g, state, theme, scores, ending)
        if banner:
            draw_box(stdscr, *banner)
        stdscr.refresh()
        time.sleep(FRAME)


# ---------------------------------------------------------------------------
# headless checks
# ---------------------------------------------------------------------------

def selftest():
    failures = []

    def check(name, cond):
        print(f"  {'PASS' if cond else 'FAIL'}  {name}")
        if not cond:
            failures.append(name)

    def carve_down(g, column):
        """Drive the snake straight down from the top border and capture."""
        g.px, g.py, g.launch = column, 0, (column, 0)
        g.set_dir(0, 1)
        for _ in range(200):
            if g.step_player() == "captured":
                g.capture()
                return

    print("capture fills the ball-free side")
    g = Game(21, 13, seed=1)
    g.balls = [Ball(4, 4, 1, 1)]  # left of centre
    carve_down(g, 10)
    check("snake reached the far border", (g.px, g.py) == (10, 12))
    check("right side filled", all(g.grid[6][x] == FILL for x in range(11, 20)))
    check("ball's side untouched", all(g.grid[6][x] == EMPTY for x in range(1, 10)))
    check("dividing line filled", g.grid[6][10] == FILL)
    check("no trail cells remain", not any(TRAIL in row for row in g.grid))
    check("percentage sane", 40.0 < g.filled_pct() < 60.0)
    check("score awarded", g.score > 0)

    print("ball stays inside and bounces")
    g = Game(30, 15, seed=7)
    g.balls = [Ball(5, 5, 1, 1)]
    b = g.balls[0]
    inside = True
    for _ in range(2000):
        g.step_balls()
        if not (1 <= b.x <= g.w - 2 and 1 <= b.y <= g.h - 2):
            inside = False
            break
    check("ball never escapes the border", inside)

    print("ball kills the trail")
    g = Game(30, 15, seed=3)
    g.balls = [Ball(10, 5, 1, 1)]
    g.grid[6][11] = TRAIL
    check("trail contact reported as death", g.step_balls() is True)

    print("a ball that ricochets into the trail still kills")
    g = Game(30, 15, seed=17)
    g.balls = [Ball(5, 1, 1, -1)]   # about to bounce off the top border...
    g.grid[2][6] = TRAIL            # ...straight into the trail below it
    check("post-bounce trail contact caught", g.step_balls() is True)

    print("every ball moves even after one is caught")
    g = Game(30, 15, seed=11)
    g.balls = [Ball(10, 5, 1, 1), Ball(20, 5, 1, 1)]
    g.grid[6][11] = TRAIL          # the first ball reports a kill...
    check("kill still reported", g.step_balls() is True)
    check("later ball still moved", (g.balls[1].x, g.balls[1].y) == (21, 6))

    print("snake cannot reverse into itself")
    g = Game(30, 15, seed=4)
    g.balls = []
    g.px, g.py, g.launch = 10, 0, (10, 0)
    g.set_dir(0, 1)
    g.step_player()
    g.step_player()
    g.set_dir(0, -1)
    check("reverse ignored while carving", (g.dx, g.dy) == (0, 1))

    print("snake stops at the edge of the world")
    g = Game(30, 15, seed=5)
    g.balls = []
    g.set_dir(1, 0)
    for _ in range(200):
        g.step_player()
    check("stopped in the top-right corner", (g.px, g.py) == (29, 0))
    check("direction cleared", (g.dx, g.dy) == (0, 0))

    print("full sweep clears the board")
    g = Game(25, 13, seed=9)
    g.balls = []          # nothing to protect the field
    carve_down(g, 12)
    check("everything painted when no ball is present", g.filled_pct() == 100.0)

    print("dying rewinds to the launch pad")
    g = Game(30, 15, seed=13)
    g.balls = []
    g.px, g.py, g.launch = 10, 0, (10, 0)
    g.set_dir(0, 1)
    for _ in range(3):
        g.step_player()
    g.die()
    check("snake back on solid ground", (g.px, g.py) == (10, 0))
    check("trail wiped", not any(TRAIL in row for row in g.grid))
    check("a life spent", g.lives == START_LIVES - 1)

    print("arrow escape sequences decode")
    keys = _decode([27, ord("["), ord("C"), ord("x"),
                    27, ord("O"), ord("A")])
    check("ESC [ C and ESC O A become arrows",
          keys == [curses.KEY_RIGHT, ord("x"), curses.KEY_UP])

    print("names are tidied")
    check("control chars and padding stripped",
          clean_name("  a\x1bb\tc  ") == "abc")
    check("long names cut to the limit",
          len(clean_name("x" * 40)) == NAME_MAX)
    check("empty becomes the default", clean_name("\x00 ") == DEFAULT_NAME)

    print("name entry")
    typed, done = "", False
    for ch in (ord("S"), ord("r"), ord("i"), curses.KEY_UP, 127, ord("i"), 10):
        typed, done = edit_name(typed, ch)
        if done:
            break
    check("letters, backspace and enter handled", (typed, done) == ("Sri", True))
    typed = "".join("a" for _ in range(NAME_MAX))
    check("input stops at the limit", edit_name(typed, ord("b"))[0] == typed)

    with tempfile.TemporaryDirectory() as tmpdir:
        path = Path(tmpdir) / "nested" / "highscores.json"

        print("high scores start empty")
        hs = HighScores(path, limit=3)
        check("no entries without a file", hs.entries == [] and hs.best == 0)
        check("zero never qualifies", not hs.qualifies(0))
        check("anything positive qualifies on an empty table", hs.qualifies(1))

        print("high scores rank, cap and persist")
        check("first entry ranks top", hs.add("a", 100, 1) == 0)
        check("higher score goes above", hs.add("b", 300, 2) == 0)
        check("lower score goes below", hs.add("c", 200, 1) == 1)
        check("table is full", not hs.qualifies(100) and hs.qualifies(101))
        check("worst entry falls off", hs.add("d", 250, 1) == 1
              and [e.name for e in hs.entries] == ["b", "d", "c"])
        check("a losing entry reports no rank", hs.add("e", 5, 1) is None)
        hs.save()
        again = HighScores(path, limit=3)
        check("round-trips through disk", again.entries == hs.entries)
        check("no temp file left behind",
              [p.name for p in path.parent.iterdir()] == [path.name])

        print("high scores survive a hostile file")
        path.write_text('[{"name": "ok", "score": 50, "level": 1},'
                        ' {"name": "\\u0007x\\u0000", "score": 40, "level": 2},'
                        ' {"name": "bool", "score": true, "level": 1},'
                        ' {"name": "neg", "score": -1, "level": 1},'
                        ' {"name": "huge", "score": 1e30, "level": 1},'
                        ' {"name": "lvl0", "score": 10, "level": 0},'
                        ' "junk", 7, null]', encoding="utf-8")
        hs = HighScores(path)
        check("only well-formed entries kept",
              [(e.name, e.score) for e in hs.entries] == [("ok", 50), ("x", 40)])
        path.write_text("{not json", encoding="utf-8")
        check("corrupt file treated as empty", HighScores(path).entries == [])
        path.write_text('{"a": 1}', encoding="utf-8")
        check("non-list file treated as empty", HighScores(path).entries == [])
        path.write_text("[" + "1," * SCORES_FILE_MAX_BYTES + "1]", encoding="utf-8")
        check("oversized file ignored", HighScores(path).entries == [])

        print("score table renders")
        hs = HighScores(path)
        check("empty table has a placeholder", score_table(hs) == ["No high scores yet"])
        hs.add("Sri", 1234, 2)
        hs.add("Bo", 999, 1)
        lines = score_table(hs, highlight=1, rows=1)
        check("rows limit honoured", len(lines) == 1)
        lines = score_table(hs, highlight=1)
        check("highlight marks the right row",
              lines[0].startswith(" ") and lines[1].startswith(">"))
        check("rows are equal width", len({len(s) for s in lines}) == 1)

    print()
    if failures:
        print(f"{len(failures)} check(s) failed")
        return 1
    print("all checks passed")
    return 0


def _decode(raw):
    """The escape-folding half of read_keys, exposed for the selftest."""
    class Fake:
        def getch(self):
            return raw.pop(0) if raw else -1
    return read_keys(Fake())


def print_scores(scores):
    print(f"High scores ({scores.path})")
    for line in score_table(scores):
        print(" ", line)


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if "--selftest" in args:
        return selftest()
    scores = HighScores(default_scores_path())
    if "--scores" in args:
        print_scores(scores)
        return 0
    try:
        curses.wrapper(run, scores)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
