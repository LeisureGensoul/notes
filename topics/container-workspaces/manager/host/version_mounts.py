"""Publish a version tree into the running container's propagated /workspace."""
from contextlib import contextmanager
import ctypes
import os
import subprocess

# Linux mount API (x86-64 and arm64).
OPEN_TREE, MOVE_MOUNT, MOUNT_SETATTR = 428, 429, 442
AT_FDCWD, AT_RECURSIVE, AT_EMPTY_PATH = -100, 0x8000, 0x1000
OPEN_TREE_CLONE, MS_PRIVATE, MOUNT_ATTR_RDONLY = 1, 1 << 18, 1
MOVE_MOUNT_F_EMPTY_PATH, MOVE_MOUNT_T_EMPTY_PATH = 0x4, 0x40
UMOUNT_NOFOLLOW = 8


@contextmanager
def directory_fd(path, parent=None):
    fd = os.open(path, os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


def checked(result, path):
    if result == -1:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(path))
    return result


def mounted(path):
    return subprocess.run(["mountpoint", "-q", "--", path]).returncode == 0


def publish(source, target, readonly=False):
    # Clone off-tree so the container sees the source, nested out/ and permissions
    # together. Making the clone private keeps unmounts away from the original.
    libc = ctypes.CDLL(None, use_errno=True)
    with directory_fd(target.parent) as parent:
        try:
            os.mkdir(target.name, dir_fd=parent)
        except FileExistsError:
            pass
        with directory_fd(target.name, parent) as dest:
            if os.path.samestat(os.stat(source), os.fstat(dest)):
                return
            if os.listdir(dest) or mounted(target):
                raise ValueError(f"Expected an empty mountpoint: {target}")
            tree = checked(libc.syscall(OPEN_TREE, AT_FDCWD, os.fsencode(source),
                                       OPEN_TREE_CLONE | os.O_CLOEXEC | AT_RECURSIVE), source)
            try:
                attrs = (ctypes.c_uint64 * 4)(MOUNT_ATTR_RDONLY if readonly else 0, 0, MS_PRIVATE, 0)
                checked(libc.syscall(MOUNT_SETATTR, tree, b"", AT_EMPTY_PATH | AT_RECURSIVE,
                                     ctypes.byref(attrs), ctypes.sizeof(attrs)), source)
                checked(libc.syscall(MOVE_MOUNT, tree, b"", dest, b"",
                                     MOVE_MOUNT_F_EMPTY_PATH | MOVE_MOUNT_T_EMPTY_PATH), target)
            finally:
                os.close(tree)


def unmount(target):
    # The parent FD and UMOUNT_NOFOLLOW prevent a writable workspace from
    # redirecting the host unmount through a renamed directory or symlink.
    libc = ctypes.CDLL(None, use_errno=True)
    with directory_fd(target.parent) as parent:
        path = os.fsencode(f"/proc/self/fd/{parent}/{target.name}")
        checked(libc.umount2(path, UMOUNT_NOFOLLOW), target)


def detach(shared, state, baseline):
    project = shared / state.name
    view = state / "view"
    materials = shared / "baseline" / state.name[5:]
    has_materials = mounted(materials)
    if has_materials:
        if not materials.samefile(baseline):
            raise ValueError(f"Unexpected baseline mount: {materials}")
        unmount(materials)
    try:
        if mounted(project):
            if not project.samefile(view):
                raise ValueError(f"Unexpected source mount: {project}")
            if mounted(project / "out"):
                if not (project / "out").samefile(view / "out"):
                    raise ValueError(f"Unexpected output mount: {project}")
                unmount(project / "out")
            try:
                unmount(project)
            except OSError:
                # The source can still be busy after out/ was detached.
                publish(view / "out", project / "out")
                raise
    except (ValueError, OSError):
        if has_materials:
            publish(baseline, materials, readonly=True)
        raise
    for point in (project, materials):
        if point.resolve() == point and point.is_dir() and not any(point.iterdir()):
            point.rmdir()
