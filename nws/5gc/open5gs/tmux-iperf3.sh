#!/bin/bash

# Kill existing session if it exists
tmux kill-session -t iperf3-servers 2>/dev/null || true

# Create a new tmux session named 'iperf3-servers'
tmux new-session -d -s iperf3-servers

# Split window into 2 panes (horizontal split)
tmux split-window -h -t iperf3-servers

# Split first pane into 3 panes (vertical splits)
tmux split-window -v -t iperf3-servers:0.0
tmux split-window -v -t iperf3-servers:0.0

# Split second pane into 3 panes (vertical splits)
tmux split-window -v -t iperf3-servers:0.1
tmux split-window -v -t iperf3-servers:0.1

# Select layout to evenly distribute panes
tmux select-layout -t iperf3-servers tiled

# Start iperf3 servers in each pane
tmux send-keys -t iperf3-servers:0.0 "iperf3 -s -p 5301" C-m
tmux send-keys -t iperf3-servers:0.1 "iperf3 -s -p 5302" C-m
tmux send-keys -t iperf3-servers:0.2 "iperf3 -s -p 5303" C-m
tmux send-keys -t iperf3-servers:0.3 "iperf3 -s -p 5304" C-m
tmux send-keys -t iperf3-servers:0.4 "iperf3 -s -p 5305" C-m
tmux send-keys -t iperf3-servers:0.5 "iperf3 -s -p 5306" C-m

# Attach to the session
tmux attach-session -t iperf3-servers
