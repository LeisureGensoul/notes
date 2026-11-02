#!/bin/sh
set -eu
if test "$#" -ne 1; then echo 'Usage: install-codex.sh SSD_MOUNT' >&2; exit 2; fi
disk=$1
package=codex-package-x86_64-unknown-linux-musl.tar.gz
runtime="$disk/development/codex/runtime"

test "$(uname -m)" = x86_64
test "$(findmnt -rn -o UUID --mountpoint "$disk")" = 9fa88778-a01d-458f-932b-f59d3673e77d
mkdir -p "$runtime/releases"
exec 9<"$runtime"
flock -x 9
stage=$(mktemp -d "$runtime/.install-XXXXXX")
trap 'rm -rf -- "$stage"' EXIT

curl -fsSL --retry 2 https://api.github.com/repos/openai/codex/releases/latest -o "$stage/release.json"
python3 - "$stage/release.json" "$package" > "$stage/asset" <<'PY'
import json, re, sys
with open(sys.argv[1]) as f:
    release = json.load(f)
tag = re.fullmatch(r"rust-v(\d+\.\d+\.\d+)", release["tag_name"])
if not tag or release["draft"] or release["prerelease"]:
    sys.exit("Expected a stable Codex release")
asset, = (a for a in release["assets"] if a["name"] == sys.argv[2])
digest = re.fullmatch(r"sha256:([0-9a-f]{64})", asset["digest"] or "")
if not digest:
    sys.exit("Release has no SHA-256 digest for the Linux package")
print(tag[1], digest[1])
PY
read -r version digest < "$stage/asset"
release="$runtime/releases/$version"
if ! test -d "$release"; then
  archive="$stage/$package"
  curl -fL --retry 2 -o "$archive" "https://github.com/openai/codex/releases/download/rust-v$version/$package"
  printf '%s  %s\n' "$digest" "$archive" | sha256sum -c -
  mkdir "$stage/package"
  tar -xozf "$archive" -C "$stage/package"
  test "$("$stage/package/bin/codex" --version)" = "codex-cli $version"
  "$stage/package/bin/codex" app-server daemon --help >/dev/null
  mv "$stage/package" "$release"
fi
test "$("$release/bin/codex" --version)" = "codex-cli $version"
if test "$(readlink "$runtime/current" || :)" != "releases/$version"; then
  ln -s "releases/$version" "$stage/current"
  mv -Tf "$stage/current" "$runtime/current"
fi
echo "Codex $version"

# Only notify running workspaces that bind this SSD runtime.
if ! command -v docker >/dev/null; then
  echo 'Docker CLI unavailable. Running daemons can be refreshed later with codex-switch --refresh.' >&2
  exit 0
fi
if ! names=$(docker ps --filter label=local-manager.workspace --format '{{.Names}}'); then
  echo 'Runtime published. To refresh running daemons later, rerun this command with Docker available.' >&2
  exit 1
fi
identity=$(stat -Lc '%d:%i' "$runtime")
status=0
for name in $names; do
  docker exec "$name" sh -c '
    if test -x /usr/local/bin/codex-switch && test "$(stat -Lc %d:%i /opt/codex 2>/dev/null)" = "$1"; then
      exec /usr/local/bin/codex-switch --refresh
    fi' sh "$identity" || status=1
done
exit "$status"
