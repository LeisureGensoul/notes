#!/bin/sh
set -eu
umask 077
action=$1; root=$2; user=$3; tag=$4; baseline=$5
state="$root/workspaces/$user/aosp-$tag"
project="$state/view"
lower="$root/aosp/aosp-$tag"
out="$state/out"
pin="$state/.out-baseline"

die() { echo "$*" >&2; exit 1; }
case "$user" in ''|[!a-z]*|*[!a-z0-9_-]*) exit 2;; esac
case "$tag" in android-*) ;; *) exit 2;; esac
case "$tag$baseline" in *[!a-zA-Z0-9._-]*) exit 2;; esac
test "$(findmnt -rn -o UUID --mountpoint "$root")" = 9fa88778-a01d-458f-932b-f59d3673e77d
test "$(realpath -m "$state")" = "$state" || die "Unexpected workspace path: $state"

if test "$action" = create && ! test -e "$state"; then
  mkdir -p "$(dirname "$state")"
  tmp=$(mktemp -d "$(dirname "$state")/.new-XXXXXX")
  mkdir -p "$tmp/upper" "$tmp/work/src" "$tmp/out"
  printf '%s\n' "$user/$tag" > "$tmp/.local-manager"
  mv -T "$tmp" "$state"
fi
test "$(cat "$state/.local-manager")" = "$user/$tag" || die "Workspace not found: $state"
for path in "$project" "$project/out" "$out" "$state/work/src" "$state/work/out" "$state/upper" "$pin"; do
  test "$(realpath -m "$path")" = "$path" || die "Unexpected workspace path: $path"
done

case "$action" in
stop|delete)
  if mountpoint -q "$project/out"; then umount "$project/out"; fi
  if mountpoint -q "$project"; then umount "$project"; fi
  if test -d "$project"; then rmdir "$project"; fi
  if test "$action" = delete; then
    if mountpoint -q "$state" || findmnt -rn -o TARGET | grep -Fq "$state/"; then
      die "Workspace still has active mounts: $state"
    fi
    rm -rf --one-file-system -- "$state"
  fi
  exit;;
create|start|rebuild|check-output) ;;
*) exit 2;;
esac

pinned=
if test -f "$pin"; then pinned=$(cat "$pin"); fi
test -d "$out" || die "Missing workspace out/"
if test "$pinned" != "$baseline" && test -n "$(ls -A "$out")"; then
  die 'Output baseline changed: unload the version and set aside its existing out/ before syncing'
fi
if test "$action" = check-output; then exit; fi

if ! mountpoint -q "$project"; then
  mountpoint -q "$lower" || die "Public source is not mounted: $lower"
  mkdir -p "$project"
  test -z "$(ls -A "$project")" || die "Nonempty workspace mountpoint: $project"
  mount -t overlay overlay -o "lowerdir=$lower,upperdir=$state/upper,workdir=$state/work/src,index=off,metacopy=off" "$project"
  trap 'umount "$project" && rmdir "$project"' EXIT
fi
if mountpoint -q "$project/out"; then
  test "$pinned" = "$baseline" || die 'Output baseline changed: use sync'
  trap - EXIT; exit
fi
test "$(realpath -m "$project/out")" = "$project/out" || die 'Unexpected out/ mountpoint'
mkdir -p "$project/out"
test -z "$(ls -A "$project/out")" || die 'Nonempty out/ mountpoint'
if test -z "$baseline"; then
  rm -f -- "$pin"
  mount --bind "$out" "$project/out"
else
  saved="$root/aosp/.baselines/$baseline"
  test -f "$saved/READY" && test -d "$saved/out" || die "Missing output baseline: $baseline"
  mkdir -p "$state/work/out"
  if test "$pinned" != "$baseline"; then
    printf '%s\n' "$baseline" > "$pin.new"; mv -T "$pin.new" "$pin"
  fi
  mount -t overlay overlay -o "lowerdir=$saved/out,upperdir=$out,workdir=$state/work/out,index=off,metacopy=off" "$project/out"
fi
trap - EXIT
