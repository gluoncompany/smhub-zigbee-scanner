#!/bin/sh
# Run as root: installs local feed + zigbee-scanner app
set -e
HERE=$(cd "$(dirname "$0")" && pwd)
CONF=/etc/opkg/smlight.conf
mkdir -p /opt/localfeed
cp -f "$HERE"/feed/* /opt/localfeed/
if ! grep -q 'file:///opt/localfeed' "$CONF"; then
    cp -n "$CONF" "$CONF.bak-zigbee-scanner" || true
    echo "src/gz local file:///opt/localfeed" >> "$CONF"
fi
opkg update 2>&1 | tail -3
opkg install --force-reinstall zigbee-scanner 2>&1 | tail -5
echo "Reiniciando smhub-services para registrar la app..."
rc-service smhub-services restart >/dev/null 2>&1 || true
sleep 3
rc-service zigbee-scanner status || true
echo INSTALL_ROOT_DONE
