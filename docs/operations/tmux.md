---
title: tmux
parent: Operations
nav_order: 2
---

# tmux

The daemon runs as a systemd service and does not need tmux. The TUI, bench scripts and long-running shell jobs do: a tmux session survives an SSH drop, so a scan started from a terminal is not killed when the laptop goes to sleep. (See [Troubleshooting](troubleshooting.html) for what an *abandoned* SSH session can do to the TUI.)

## Starting a new tmux session

```bash
tmux new -s <session_name>  # e.g. muon_window
```

## Detaching safely from a session

To detach from the current session and leave the process running in the background:

```
Ctrl+B, D
```

## Reattaching to a session

To reattach to an existing session:

```bash
tmux a -t <session_name>
```

## Useful shortcuts

- **Ctrl+B %** — Split the window into two panes horizontally.
- **Ctrl+B "** — Split the window into two panes vertically.
- **Ctrl+B Arrow Key (Left, Right, Up, Down)** — Move between panes.
- **Ctrl+B X** — Close the current pane.
- **Ctrl+B :** — Open the tmux command line (tab completion available).
- **Ctrl+B ?** — Show all keybindings. Press Q to exit.

## Other handy commands

- `tmux ls` — List all active sessions.
- `tmux kill-session -t <session_name>` — Terminate a specific session.
- **Ctrl+B $** — Rename the current session.
- **Ctrl+B ,** — Rename the current window.