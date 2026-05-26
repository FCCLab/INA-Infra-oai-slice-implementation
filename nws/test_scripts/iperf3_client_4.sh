#!/bin/bash

# ip route add 10.45.0.0/16 via 10.45.0.1 dev oaitun_ue4

# iperf3 -c 10.45.0.1 -R -t 0 -p 5004


ip route add 10.1.101.37/32 via 10.45.0.1 dev oaitun_ue1
iperf3 -c 10.1.101.37 -R -t 0 -p 5304
