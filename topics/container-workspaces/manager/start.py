"""Restore existing Linux workspaces and open the monitor."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

from workspace import HERE, LABEL, SSD_UUID, config, output, run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user", help="Restore and monitor only this user")
    parser.add_argument("--once", action="store_true", help="Print one monitor snapshot and exit")
    parser.add_argument("--restore-only", type=Path, metavar="MOUNTPOINT",
                        help="Restore workspaces on an already mounted SSD and exit")
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.exit(1, "Run with sudo to restore workspace mounts.\n")

    selector = f"label={LABEL}" + (f"={args.user}" if args.user else "")
    names = output("docker", "ps", "-a", "--filter", selector, "--format", "{{.Names}}").splitlines()
    if not names:
        parser.exit(1, "No matching workspace containers.\n")

    configs = []
    disks = set()
    for name in sorted(names):
        path = HERE / "local_keys" / name / "workspace.json"
        if not path.is_file():
            path = HERE / "workspace.json"
        disk, user, *_ = config(path)
        if user != name or not disk.is_absolute():
            raise ValueError(f"Expected a config for {name} with an absolute disk_root: {path}")
        configs.append(path)
        disks.add(disk.resolve())
    if len(disks) != 1:
        raise ValueError("Selected workspaces must use the same SSD mount point")
    disk = disks.pop()
    if args.restore_only and (disk != args.restore_only.resolve() or not os.path.ismount(disk)):
        raise ValueError(f"Expected the configured SSD at {args.restore_only}")
    if not os.path.ismount(disk):
        disk.mkdir(parents=True, exist_ok=True)
        if any(disk.iterdir()):
            raise ValueError(f"SSD mount point is not empty: {disk}")
        run("mount", "-U", SSD_UUID, disk)
    if output("findmnt", "-rn", "-o", "UUID", "--mountpoint", disk) != SSD_UUID:
        raise ValueError(f"Expected the AOSP SSD at {disk}")
    if "shared" not in output("findmnt", "-rn", "-o", "PROPAGATION", "--mountpoint", disk).split(","):
        run("mount", "--make-shared", disk)

    for path in configs:
        run(sys.executable, "-B", HERE / "workspace.py", "start", "--existing-only", "--config", path)
    if args.restore_only:
        return
    monitor = [sys.executable, "-B", str(HERE / "monitor.py"), "--disk", str(disk)]
    if args.user:
        monitor.extend(["--user", args.user])
    if args.once:
        monitor.append("--once")
    os.execv(sys.executable, monitor)


if __name__ == "__main__":
    try:
        main()
    except (KeyError, OSError, ValueError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
