#! /bin/bash

export UE_GATEWAY_IP="${UE_IP_BASE}.2/24"
export UE_IP_RANGE="${UE_IP_BASE}.0/24"

envsubst < open5gs-5gc.yml.in > open5gs-5gc.yml

# create dummy interfaces on localhost ip range for open5gs entities to bind to
for IP in {2..22}
do
    ip link add name lo$IP type dummy
    ip ad ad 127.0.0.$IP/24 dev lo$IP
    ip link set lo$IP up
done

# run webui
cd webui && npm run dev &

# run mongodb
mkdir -p /data/db && mongod --logpath /tmp/mongodb.log &

# wait for mongodb to be available, otherwise open5gs will not start correctly
while ! ( nc -zv $MONGODB_IP 27017 2>&1 >/dev/null )
do
    echo waiting for mongodb
    sleep 1
done

# setup ogstun and routing
python3 setup_tun.py --ip_range ${UE_IP_RANGE}
if [ $? -ne 0 ]
then
    echo "Failed to setup ogstun and routing"
    exit 1
fi

# Add subscriber data to open5gs mongo db
echo "SUBSCRIBER_DB=${SUBSCRIBER_DB}"
python3 add_users.py --mongodb ${MONGODB_IP} --subscriber_data ${SUBSCRIBER_DB}
if [ $? -ne 0 ]
then
    echo "Failed to add subscribers to database"
    exit 1
fi

# Auto-detect architecture and fix freeDiameter library paths for cross-platform compatibility
ARCH=$(uname -m)
echo "Detected architecture: $ARCH"

if [ "$ARCH" = "aarch64" ]; then
    echo "Setting up ARM64 freeDiameter library paths..."
    mkdir -p /open5gs/install/lib/x86_64-linux-gnu
    ln -sf /open5gs/install/lib/aarch64-linux-gnu/freeDiameter /open5gs/install/lib/x86_64-linux-gnu/freeDiameter
elif [ "$ARCH" = "x86_64" ]; then
    echo "Setting up x86_64 freeDiameter library paths..."
    mkdir -p /open5gs/install/lib/aarch64-linux-gnu
    ln -sf /open5gs/install/lib/x86_64-linux-gnu/freeDiameter /open5gs/install/lib/aarch64-linux-gnu/freeDiameter
else
    echo "Warning: Unknown architecture $ARCH, attempting to create both paths..."
    mkdir -p /open5gs/install/lib/x86_64-linux-gnu
    mkdir -p /open5gs/install/lib/aarch64-linux-gnu
    # Try to find the actual freeDiameter directory
    if [ -d "/open5gs/install/lib/$ARCH-linux-gnu/freeDiameter" ]; then
        ln -sf /open5gs/install/lib/$ARCH-linux-gnu/freeDiameter /open5gs/install/lib/x86_64-linux-gnu/freeDiameter
        ln -sf /open5gs/install/lib/$ARCH-linux-gnu/freeDiameter /open5gs/install/lib/aarch64-linux-gnu/freeDiameter
    fi
fi

# SMF is started as part of the main 5gc process, no need to start it separately

sysctl -w net.ipv4.ip_forward=1
iptables -t nat -A POSTROUTING -s 10.45.0.0/16 ! -o ogstun -j MASQUERADE

# iperf3 is not started here — E2E / operators run: docker exec <5gc> iperf3 -s -p <port> -D
# (see nws/scripts/e2e_nw_slice_docker.py ensure_iperf_server_core)

if $DEBUG
then
    exec stdbuf -o L gdb -batch -ex=run -ex=bt --args $@
else
    exec stdbuf -o L $@
fi
