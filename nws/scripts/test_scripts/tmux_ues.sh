#!/bin/bash

# Kill existing session if it exists
tmux kill-session -t ues 2>/dev/null || true

# Create a new tmux session named 'ues'
tmux new-session -d -s ues

# Split window into 2 panes (horizontal split)
tmux split-window -h -t ues

# Split first pane into 3 panes (vertical splits)
tmux split-window -v -t ues:0.0
tmux split-window -v -t ues:0.0

# Split second pane into 3 panes (vertical splits)
tmux split-window -v -t ues:0.1
tmux split-window -v -t ues:0.1

# Select layout to evenly distribute panes
tmux select-layout -t ues tiled

# Execute docker exec in each pane
tmux send-keys -t ues:0.0 "docker exec -it nws-oai-nr-ue1 bash" C-m
tmux send-keys -t ues:0.1 "docker exec -it nws-oai-nr-ue2 bash" C-m
tmux send-keys -t ues:0.2 "docker exec -it nws-oai-nr-ue3 bash" C-m
tmux send-keys -t ues:0.3 "docker exec -it nws-oai-nr-ue4 bash" C-m
tmux send-keys -t ues:0.4 "docker exec -it nws-oai-nr-ue5 bash" C-m
tmux send-keys -t ues:0.5 "docker exec -it nws-oai-nr-ue6 bash" C-m

# Attach to the session
tmux attach-session -t ues
