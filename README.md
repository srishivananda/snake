# Snake vs. Ball

[![Built with Claude Code](https://img.shields.io/badge/Built%20with-Claude%20Code-D97757?logo=claude&logoColor=white)](https://claude.com/claude-code)
![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-3776AB?logo=python&logoColor=white)
![Dependencies: none](https://img.shields.io/badge/dependencies-none-2ea44f)

A terminal territory-capture game, written by Claude Code.

You are a snake living on the border of a dark field. Step off solid ground
and you start carving a trail. The moment the trail touches solid ground
again, the loop closes and whichever side of the field has no ball in it gets
painted in. Balls bounce around the unpainted area the whole time, and if one
so much as grazes your trail you lose a life. Paint 75% of the field and the
level is cleared; the next one has one more ball and everything moves a bit
faster. A good run earns a spot on a top-ten table that survives between games.

## Where this came from

This was not a project. It was a vibing session.

In the early 90s, in India, I had a personal computer with an 8088 in it, and my 
friends and I spent more hours than we should have on the handful of games that 
ran on it. One of them was a territory game exactly like this: carve a line, 
trap the bouncing balls into an ever smaller pocket, lose everything when a ball
clipped your trail with 74% painted. We argued over the high-score table.
We passed the keyboard around. We knew every quirk of how the balls bounced.

Thirty-odd years later, that memory surfaced, and I described it to Claude
Code the way I would have described it to one of those friends. A few minutes
and a few dollars of tokens later, the game was running in my terminal,
balls bouncing, trail glowing yellow, high scores and all. Then we spent a
little longer polishing it, hardening the score file, and writing the
installer, but the game itself was there almost immediately.

It is a small thing. But sitting in front of it, with the same "one more go"
feeling I had at nineteen, was worth far more than it cost. This repository 
is here mostly as a commemoration of those good old times.

<img width="1470" height="853" alt="image" src="https://github.com/user-attachments/assets/a033bd42-0449-488f-a424-abd9a04e1780" />

## Playing

| Key                    | Action                    |
| ---------------------- | ------------------------- |
| Arrow keys or `WASD`   | Move                      |
| `Space`                | Stop (on solid ground)    |
| `P`                    | Pause / resume            |
| `Q`                    | Quit                      |
| `R`                    | Restart after game over   |

You start with three lives and gain one for every level you clear, up to
five. Reversing straight back into your own trail is refused rather than
letting you die by accident. The playfield is sized to your terminal, so a
bigger window means a bigger board.

## Installing

The game is a single Python file with no third-party dependencies. It needs
Python 3.8 or newer with the `curses` module, which every Python that ships
on a Mac already has.

On a Mac that has nothing set up yet:

```
./install.sh
snake
```

The installer finds a suitable Python 3, or installs one through Homebrew or
Apple's Command Line Tools, runs the game's built-in checks, and puts a
`snake` launcher on your PATH. Pass `--no-link` to skip the launcher and run
the file directly instead:

```
python3 snake.py            play
python3 snake.py --scores   print the high-score table
python3 snake.py --selftest run the headless logic checks
```

## High scores

The top ten live in a small JSON file at
`~/.local/share/snake/highscores.json` (or under `$XDG_DATA_HOME` if that is
set). Set `SNAKE_SCORES=/some/path.json` to keep them somewhere else. The file
is treated as untrusted input, so a corrupt or hand-edited table is quietly
ignored rather than crashing the game, and saves are atomic so a crash
mid-write cannot leave a torn file.

## Files

| File               | What it is                                          |
| ------------------ | --------------------------------------------------- |
| `snake.py`         | The whole game: rules, rendering, input, high scores, selftest |
| `install.sh`       | Sets up Python and a launcher on a fresh Mac         |
| `requirements.txt` | Empty of packages on purpose; documents the runtime |
| `README.md`        | This                                                |

Built with Claude Code, from a memory.
