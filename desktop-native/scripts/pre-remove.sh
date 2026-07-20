#!/bin/bash
set -e
echo "GalaxyOS pre-remove: stopping running processes..."
pkill -f galaxyos-desktop 2>/dev/null || true
sleep 1
