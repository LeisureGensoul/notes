#!/bin/sh
set -eu
action=$1; disk=$2; selected=${3:-all}
root=$disk/aosp
base=$root/aosp-android-14.0.0_r1
catalog=$(dirname "$0")/../default-baselines.tsv
test "$(findmnt -rn -o UUID --mountpoint "$disk")" = 9fa88778-a01d-458f-932b-f59d3673e77d

readonly_mount() {
  case ",$(findmnt -rn -o OPTIONS --mountpoint "$1")," in
    *,ro,*) ;; *) echo "Expected read-only mount: $1" >&2; exit 1;;
  esac
}

bind_readonly() (
  if mountpoint -q "$2"; then
    readonly_mount "$2"
    test "$(stat -Lc '%d:%i' "$1")" = "$(stat -Lc '%d:%i' "$2")"
    exit
  fi
  if test "$1" != "$2"; then test -d "$2" && test -z "$(ls -A "$2")" || exit 1; fi
  # Make the bind read-only before publishing it to other mount namespaces.
  stage=$(mktemp -d)
  trap 'if mountpoint -q "$stage"; then umount "$stage"; fi; rmdir "$stage"' EXIT
  mount --bind "$1" "$stage"
  mount --make-private "$stage"
  mount -o remount,bind,ro "$stage"
  mount --bind "$stage" "$2"
)

case "$action" in
mount)
  if test "$selected" != all; then cut -f1 "$catalog" | grep -Fxq -- "$selected"; fi
  test -d "$base/.repo"
  bind_readonly "$base" "$base"
  while read -r tag id; do
    source=$root/aosp-$tag
    if test "$selected" != all && test "$tag" != "$selected" && test "$source" != "$base"; then continue; fi
    if ! mountpoint -q "$source"; then
      layer=$root/.layers/aosp-$tag
      test -z "$(ls -A "$source")"
      mount -t overlay overlay -o "ro,lowerdir=$base,upperdir=$layer/upper,workdir=$layer/work,index=off,metacopy=off" "$source"
    fi
    readonly_mount "$source"
    saved=$root/.baselines/$id
    if test -e "$saved"; then
      test -f "$saved/READY" && test -d "$saved/out" || exit 1
      bind_readonly "$saved/out" "$source/out"
    elif mountpoint -q "$source/out"; then
      echo "Missing baseline for mounted output: $source/out" >&2; exit 1
    fi
  done < "$catalog";;
unmount)
  if findmnt -rn -t overlay -o TARGET | grep -Fq "$disk/workspaces/"; then
    echo 'Stop dependent workspaces first.' >&2; exit 1
  fi
  while read -r tag id; do
    source=$root/aosp-$tag
    if mountpoint -q "$source/out"; then umount "$source/out"; fi
    if test "$source" != "$base" && mountpoint -q "$source"; then umount "$source"; fi
  done < "$catalog"
  if mountpoint -q "$base"; then umount "$base"; fi;;
*) exit 2;;
esac
