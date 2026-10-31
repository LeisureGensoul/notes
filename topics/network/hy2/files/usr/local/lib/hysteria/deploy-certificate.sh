#!/bin/sh
set -eu
certificate=$(mktemp /data/network/tls/.fullchain.XXXXXX)
private_key=$(mktemp /data/network/tls/.privkey.XXXXXX)
trap 'rm -f "$certificate" "$private_key"' EXIT
install -o root -g network-tls -m 0640 "$RENEWED_LINEAGE/fullchain.pem" "$certificate"
install -o root -g network-tls -m 0640 "$RENEWED_LINEAGE/privkey.pem" "$private_key"
mv -f "$private_key" /data/network/tls/privkey.pem
mv -f "$certificate" /data/network/tls/fullchain.pem
systemctl reload caddy
