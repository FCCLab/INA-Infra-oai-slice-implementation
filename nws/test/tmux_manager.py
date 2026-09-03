#!/usr/bin/env python3
"""
TmuxManager: Clean, systematic controller for 5G slicing multi-tab visual testbed environment.
Manages session lifecycle, visual Catppuccin styling, tab layouts, and pane command execution.
"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

TMUX_SESSION = "rfsim_slice_test"


def _run_cmd(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    """Run shell command via subprocess."""
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


class TmuxManager:
    """Systematic controller for 5G slicing multi-tab visual testbed environment."""

    def __init__(self, session_name: str = TMUX_SESSION):
        self.session = session_name
        self.in_tmux = "TMUX" in os.environ

    @classmethod
    def launch_in_tmux(cls, argv: list[str]) -> None:
        """Reliably hand over terminal execution into an interactive tmux session with visual tabs."""
        session = TMUX_SESSION
        log_file = Path("/tmp/tmux_manager_debug.log")
        with open(log_file, "a") as f:
            f.write(f"\n[{datetime.datetime.now()}] launch_in_tmux called with argv: {argv}\n")

        # Always kill previous tmux session on start to guarantee a fresh, clean environment
        subprocess.run(["tmux", "kill-session", "-t", session], capture_output=True)
        time.sleep(0.1)

        # Apply global tmux styling options
        mgr = cls(session)
        mgr.apply_styling()

        args_str = " ".join([f'"{a}"' for a in argv[1:]])
        script_abs = str(Path(argv[0]).resolve())
        cmd = f"{sys.executable} {script_abs} {args_str}".strip()

        with open(log_file, "a") as f:
            f.write(f"[{datetime.datetime.now()}] Executing atomic handover: {cmd}\n")

        # Create fresh new-session with fallback to interactive bash so window never closes on exit
        full_pane_cmd = f"{cmd}; exec bash"
        os.execvp("tmux", ["tmux", "new-session", "-s", session, "-n", "console", full_pane_cmd])

    def has_session(self) -> bool:
        """Check if tmux session exists."""
        res = subprocess.run(["tmux", "has-session", "-t", self.session], capture_output=True)
        return res.returncode == 0

    def kill_session(self) -> None:
        """Destroy the tmux session cleanly."""
        subprocess.run(["tmux", "kill-session", "-t", self.session], capture_output=True)

    def apply_styling(self) -> None:
        """Apply unified Catppuccin theme styling, mouse mode, and custom navigation bar."""
        options_global = [
            ("mouse", "on"),
            ("allow-rename", "off"),
            ("automatic-rename", "off"),
            ("status", "on"),
            ("status-position", "bottom"),
            ("status-style", "bg=#1e1e2e,fg=#cdd6f4"),
            ("window-status-current-style", "bg=#89b4fa,fg=#11111b,bold"),
            ("window-status-format", " #I:#W "),
            ("window-status-current-format", " #[bold]#I:#W#[default] "),
            ("status-left", "#[bold,fg=#a6e3a1][5G-Slice Matrix] #[default]"),
            ("status-right", "#[fg=#f9e2af]Tabs: [0:console] [1:servers] [2:clients] (Click tab or mouse scroll) #[default]"),
        ]
        for opt, val in options_global:
            _run_cmd(["tmux", "set-option", "-g", opt, val])

        if self.has_session():
            for opt, val in options_global:
                _run_cmd(["tmux", "set-option", "-t", self.session, opt, val])

    def ensure_session(self) -> None:
        """Ensure session exists with a persistent console window."""
        if not self.has_session():
            subprocess.run(["tmux", "new-session", "-d", "-s", self.session, "-x", "160", "-y", "40", "-n", "console", "bash"], check=True)
        self.apply_styling()

    def get_window_indices(self) -> set[str]:
        """List currently existing window indices in the session."""
        if not self.has_session():
            return set()
        out = _run_cmd(["tmux", "list-windows", "-t", self.session, "-F", "#{window_index}"]).stdout
        return set(out.strip().splitlines())

    def ensure_tab(self, win_idx: int, win_name: str, num_panes: int = 1, layout: str = "tiled") -> None:
        """Ensure a named tab (window) exists with the exact number of tiled panes."""
        self.ensure_session()
        existing = self.get_window_indices()
        target_win = f"{self.session}:{win_idx}"

        if str(win_idx) not in existing:
            subprocess.run(["tmux", "new-window", "-d", "-t", target_win, "-n", win_name, "bash"], capture_output=True)
            _run_cmd(["tmux", "set-window-option", "-t", target_win, "window-size", "largest"])
            _run_cmd(["tmux", "resize-window", "-t", target_win, "-x", "220", "-y", "60"])
            for _ in range(num_panes - 1):
                res = subprocess.run(["tmux", "split-window", "-d", "-t", target_win, "bash"], capture_output=True)
                if res.returncode == 0:
                    subprocess.run(["tmux", "select-layout", "-t", target_win, "tiled"], capture_output=True)
            if num_panes > 1:
                subprocess.run(["tmux", "select-layout", "-t", target_win, layout], capture_output=True)
            # Give newly spawned bash subshells 0.3s to finish tty and rc script loading
            time.sleep(0.3)
        else:
            _run_cmd(["tmux", "rename-window", "-t", target_win, win_name])

    def send_pane_command(self, win_idx: int, pane_idx: int, cmd: str) -> None:
        """Send an executable command to a target pane cleanly."""
        target_pane = f"{self.session}:{win_idx}.{pane_idx}"
        subprocess.run(["tmux", "send-keys", "-t", target_pane, "C-c"], capture_output=True)
        time.sleep(0.05)
        subprocess.run(["tmux", "send-keys", "-t", target_pane, "C-u", cmd, "Enter"], check=True)

    def select_tab(self, win_idx: int) -> None:
        """Focus on a specific tab without jumping."""
        subprocess.run(["tmux", "select-window", "-t", f"{self.session}:{win_idx}"], capture_output=True)

    def setup_test_panes(self, server_scripts: dict[int, Path], ue_scripts: dict[int, Path]) -> None:
        """Set up Tab 0 (console), Tab 1 (servers 5 panes), and Tab 2 (clients 5 panes)."""
        self.ensure_session()

        # Tab 0: console
        self.ensure_tab(0, "console", num_panes=1)

        # Tab 1: servers (5 UPF receiver panes)
        self.ensure_tab(1, "servers", num_panes=5, layout="tiled")
        for u in range(1, 6):
            if u in server_scripts:
                self.send_pane_command(1, u - 1, f"bash {server_scripts[u]}")
                time.sleep(0.02)

        # Tab 2: clients (5 UE traffic client panes)
        self.ensure_tab(2, "clients", num_panes=5, layout="tiled")
        for u in range(1, 6):
            if u in ue_scripts:
                self.send_pane_command(2, u - 1, f"bash {ue_scripts[u]}")
                time.sleep(0.02)

        # Keep active focus on Tab 0 (console)
        self.select_tab(0)
