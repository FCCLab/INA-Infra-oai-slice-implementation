#!/bin/bash
# ========================================================================
# Test Case 214: dedicated_asym_idle_ul_tcp
# Description: UE1 full traffic; UEs 2-5 IDLE. Slices 2-5 dedicated PRBs (52%) remain strictly RESERVED. (Uplink TCP)
# ========================================================================
# Usage: ./tc_214_dedicated_asym_idle_ul_tcp.sh [--time <seconds>] [--skip-prep]
# ========================================================================

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PARENT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

apply_tmux_styling() {
    local session="${1:-rfsim_slice_test}"
    tmux set-option -g mouse on 2>/dev/null
    tmux set-option -g allow-rename off 2>/dev/null
    tmux set-option -g automatic-rename off 2>/dev/null
    tmux set-option -t "$session" mouse on 2>/dev/null
    tmux set-option -t "$session" status on 2>/dev/null
    tmux set-option -t "$session" status-position bottom 2>/dev/null
    tmux set-option -t "$session" status-style "bg=#1e1e2e,fg=#cdd6f4" 2>/dev/null
    tmux set-option -t "$session" window-status-current-style "bg=#89b4fa,fg=#11111b,bold" 2>/dev/null
    tmux set-option -t "$session" window-status-format " #I:#W " 2>/dev/null
    tmux set-option -t "$session" window-status-current-format " #[bold]#I:#W#[default] " 2>/dev/null
    tmux set-option -t "$session" status-left "#[bold,fg=#a6e3a1][5G-Slice Matrix] #[default]" 2>/dev/null
    tmux set-option -t "$session" status-right "#[fg=#f9e2af]Tabs: [0:console] [1:servers] [2:clients] (Click tab or mouse scroll) #[default]" 2>/dev/null
    tmux rename-window -t "$session:0" "console" 2>/dev/null
    tmux set-option -w -t "$session:0" allow-rename off 2>/dev/null
    tmux set-option -w -t "$session:0" automatic-rename off 2>/dev/null
}

# Launch inside tmux session "rfsim_slice_test" if not already inside tmux
if [ -z "$TMUX" ]; then
    tmux new-session -d -s rfsim_slice_test -n "console" "bash $0 $@" 2>/dev/null
    apply_tmux_styling "rfsim_slice_test"
    exec tmux attach-session -t rfsim_slice_test
fi

apply_tmux_styling "rfsim_slice_test"

cd "${PARENT_DIR}"
python3 test.py --test 214 "$@"

echo ""
read -p "Test completed. Press Enter to exit tmux session... " _dummy
