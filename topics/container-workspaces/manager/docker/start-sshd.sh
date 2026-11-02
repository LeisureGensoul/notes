#!/bin/sh
set -eu
key=/var/lib/local-manager/ssh/ssh_host_ed25519_key
install -D -m 600 /run/local-manager/client.pub /etc/local-manager/keys/authorized_keys
if ! test -f "$key"; then ssh-keygen -q -t ed25519 -N '' -f "$key"; fi
mkdir -p /run/sshd
environment="CODEX_HOME=$CODEX_HOME"
if [ -n "${ADB_SERVER_SOCKET:-}" ]; then environment="$environment ADB_SERVER_SOCKET=$ADB_SERVER_SOCKET"; fi
exec /usr/sbin/sshd -D -e -h "$key" \
    -o AuthenticationMethods=publickey -o PermitRootLogin=prohibit-password \
    -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no -o UsePAM=yes \
    -o AuthorizedKeysFile=/etc/local-manager/keys/authorized_keys -o "SetEnv=$environment"
