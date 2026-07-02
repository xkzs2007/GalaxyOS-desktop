#!/bin/bash
# Run sidecar + Electron + screenshot
set -e
fuser -k 5757/tcp 5758/tcp 2>/dev/null || true
pkill -9 -f galaxyos-sidecar 2>/dev/null || true
pkill -9 -f "node_modules/electron" 2>/dev/null || true
sleep 1

echo "=== starting sidecar (detached) ==="
setsid nohup /workspace/galaxyos-desktop/desktop-shell/python/dist/galaxyos-sidecar </dev/null &>/tmp/sc.log &
disown 2>/dev/null || true
echo "Sidecar launched, waiting 8s for ready..."
sleep 8
echo "Sidecar ready: $(grep -c 'listening' /tmp/sc.log 2>/dev/null)"
ps aux | grep galaxyos-sidecar | grep -v grep | head -1

echo "=== starting electron ==="
cd /workspace/galaxyos-desktop/desktop-shell
NODE_OPTIONS="" xvfb-run -a --server-args="-screen 0 1280x800x24" ./node_modules/electron/dist/electron /workspace/galaxyos-desktop/test/screenshot-real.mjs --no-sandbox &>/tmp/elec.log
echo "Electron done"
echo "--- /tmp/elec.log head ---"
head -20 /tmp/elec.log

ls -lh /workspace/galaxyos-desktop/test/screenshot-real.png

# cleanup
kill -9 $SIDECAR_PID 2>/dev/null || true
pkill -9 -f galaxyos-sidecar 2>/dev/null || true
