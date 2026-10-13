#!/bin/sh
# Review M6 check (plan task 56): can a worker reach the host? Run on the host with the stack up:
#
#   sh scripts/check_gateway.sh [compose project] [host LAN address ...]
#   sh scripts/check_gateway.sh filex 192.168.1.20
#
# From inside worker-core, as the runner's user (not a step), it tries TCP to the internal
# network's gateway, host.docker.internal and every address given, on the ports a home server
# usually listens on (web admin UI 80/443, SSH, SMB, Docker API, common app ports). Every line should say
# "closed" or "timeout"; "OPEN" means the host answers a worker: see docs/reviews/m6-gateway-check.md.
# Exit code: 0 nothing open, 1 something open, 2 usage or setup error.
set -eu
project=${1:-filex}
[ $# -gt 0 ] && shift
net="${project}_internal"
subnet=$(docker network inspect -f '{{range .IPAM.Config}}{{.Subnet}}{{end}}' "$net") || exit 2
echo "network $net subnet $subnet"

# The gateway is the subnet's first address (with inhibit_ipv4 the host does not hold it).
docker compose -p "$project" exec -T worker-core python - "$subnet" host.docker.internal "$@" <<'EOF'
import ipaddress, socket, sys

sys.argv[1] = str(next(ipaddress.ip_network(sys.argv[1]).hosts()))

PORTS = [22, 53, 80, 139, 443, 445, 2375, 2376, 3000, 5000, 8000, 8080, 8443, 9000, 9090, 9443]
print("probing as uid", __import__("os").getuid())
print("default route:", any(l.split()[1] == "00000000" for l in open("/proc/net/route").readlines()[1:]))
opened = False
for target in dict.fromkeys(a for a in sys.argv[1:] if a):
    try:
        addr = socket.gethostbyname(target)
    except OSError as e:
        print(f"{target}: does not resolve ({e.__class__.__name__})")
        continue
    for port in PORTS:
        s = socket.socket()
        s.settimeout(1.5)
        try:
            s.connect((addr, port))
            result, opened = "OPEN", True
        except socket.timeout:
            result = "timeout"
        except OSError as e:
            result = f"closed ({e.strerror or e})"
        finally:
            s.close()
        print(f"{target} ({addr}):{port} {result}")
sys.exit(1 if opened else 0)
EOF
