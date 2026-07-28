#! /bin/bash
# Multi-UPF entrypoint: 5 open5gs-upfd (DNN oai1..oai5) + 5gc with no_upf.
set -euo pipefail

export UE_GATEWAY_IP="${UE_IP_BASE}.2/24"
export UE_IP_RANGE="${UE_IP_BASE}.0/24"

envsubst < open5gs-5gc-mult.yml.in > open5gs-5gc.yml
for i in 1 2 3 4 5; do
  envsubst < "upf${i}.yml.in" > "upf${i}.yml"
done

# create dummy interfaces on localhost ip range for open5gs entities to bind to
for IP in {2..40}
do
    ip link add name lo$IP type dummy 2>/dev/null || true
    ip ad ad 127.0.0.$IP/24 dev lo$IP 2>/dev/null || true
    ip link set lo$IP up 2>/dev/null || true
done

# N3 aliases for UPF1..5 (10.53.1.3 .. 10.53.1.7) on the n2n3 face
N3_IF=$(ip -o -4 addr show | awk '/10\.53\.1\.2\//{print $2; exit}')
if [ -z "${N3_IF:-}" ]; then
    N3_IF=$(ip -o link show | awk -F': ' '$2 !~ /lo|ogstun|docker/{print $2; exit}')
fi
echo "Adding UPF N3 aliases on ${N3_IF}"
for N3 in 3 4 5 6 7; do
    ip addr add "10.53.1.${N3}/24" dev "${N3_IF}" 2>/dev/null || true
done

# run webui
cd webui && npm run dev &
cd /open5gs

# run mongodb
mkdir -p /data/db && mongod --logpath /tmp/mongodb.log &

while ! ( nc -zv "${MONGODB_IP}" 27017 2>&1 >/dev/null )
do
    echo waiting for mongodb
    sleep 1
done

python3 setup_tun.py --ip_range "${UE_IP_RANGE}"
if [ $? -ne 0 ]
then
    echo "Failed to setup ogstun and routing"
    exit 1
fi

echo "SUBSCRIBER_DB=${SUBSCRIBER_DB}"
python3 add_users.py --mongodb "${MONGODB_IP}" --subscriber_data "${SUBSCRIBER_DB}"
if [ $? -ne 0 ]
then
    echo "Failed to add subscribers to database"
    exit 1
fi

ARCH=$(uname -m)
echo "Detected architecture: $ARCH"
if [ "$ARCH" = "aarch64" ]; then
    mkdir -p /open5gs/install/lib/x86_64-linux-gnu
    ln -sf /open5gs/install/lib/aarch64-linux-gnu/freeDiameter /open5gs/install/lib/x86_64-linux-gnu/freeDiameter
elif [ "$ARCH" = "x86_64" ]; then
    mkdir -p /open5gs/install/lib/aarch64-linux-gnu
    ln -sf /open5gs/install/lib/x86_64-linux-gnu/freeDiameter /open5gs/install/lib/aarch64-linux-gnu/freeDiameter
fi

sysctl -w net.ipv4.ip_forward=1
iptables -t nat -A POSTROUTING -s 10.45.0.0/16 ! -o ogstun -j MASQUERADE

UPFD=/open5gs/install/bin/open5gs-upfd
if [ ! -x "$UPFD" ]; then
    UPFD=/open5gs/build/src/upf/open5gs-upfd
fi
echo "Starting 5x open5gs-upfd via $UPFD"
for i in 1 2 3 4 5; do
    stdbuf -o L "$UPFD" -c "/open5gs/upf${i}.yml" > "/tmp/upf${i}.log" 2>&1 &
    echo "  UPF${i} pid=$! (N3=10.53.1.$((i+2)) DNN=oai${i})"
done
sleep 1

if ${DEBUG}
then
    exec stdbuf -o L gdb -batch -ex=run -ex=bt --args "$@"
else
    exec stdbuf -o L "$@"
fi
