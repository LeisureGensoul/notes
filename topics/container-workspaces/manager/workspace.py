"""Manage a user's container workspace and its optional AOSP versions."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from host.version_mounts import detach, mounted, publish

HERE = Path(__file__).resolve().parent
CATALOG = dict(line.split() for line in (HERE / "default-baselines.tsv").read_text().splitlines())
LABEL = "local-manager.workspace"
SECCOMP_PROFILE = HERE / "docker/workspace-seccomp.json"


def config(path):
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    user, accounts = data["user"], data.get("accounts", [])
    if not isinstance(accounts, list) or len(set(accounts)) != len(accounts):
        raise ValueError("accounts must be a list of distinct aliases")
    for alias in [user, *accounts]:
        if not isinstance(alias, str) or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", alias):
            raise ValueError("Invalid user or account alias")
    versions = data.get("versions", {})
    if "version" in data or "output_target" in data:
        if "versions" in data or "version" not in data:
            raise ValueError("Set lunch targets in versions")
        versions = {data["version"]: data.get("output_target")}
    if not isinstance(versions, dict):
        raise ValueError("versions must map AOSP tags to lunch targets or null")
    for tag, target in versions.items():
        if tag not in CATALOG:
            raise ValueError(f"Unsupported AOSP version: {tag}")
        if target is not None and (not isinstance(target, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", target)):
            raise ValueError(f"Invalid lunch target for {tag}")
    image = data["image"]
    if not isinstance(image, str) or not image or image.startswith("-"):
        raise ValueError("Set image to a locally available Docker image")
    root = Path(data.get("disk_root", "/mnt/aosp"))
    ssh_port = data.get("ssh_port")
    if ssh_port is not None and (type(ssh_port) is not int or not 1 <= ssh_port <= 65535):
        raise ValueError("ssh_port must be a port number or null")
    adb_server = data.get("adb_server")
    if adb_server is not None:
        host_port = r"(?:[A-Za-z0-9.-]+|\[[0-9a-fA-F:]+\]):([0-9]{1,5})"
        endpoint = re.fullmatch(host_port, adb_server) if isinstance(adb_server, str) else None
        if not endpoint or not 1 <= int(endpoint.group(1)) <= 65535:
            raise ValueError("adb_server must be host:port or null")
    cpus = data.get("cpus", 16)
    if cpus is not None and (type(cpus) not in (int, float) or not 0.01 <= cpus < float("inf")):
        raise ValueError("cpus must be a number of at least 0.01 or null")
    memory_gib = data.get("memory_gib", 32)
    if memory_gib is not None and (type(memory_gib) is not int or memory_gib < 1):
        raise ValueError("memory_gib must be a positive integer or null")
    swap_gib = data.get("swap_gib")
    if swap_gib is not None and (type(swap_gib) is not int or swap_gib < 0 or memory_gib is None):
        raise ValueError("swap_gib requires memory_gib and must be a nonnegative integer")
    return root, user, dict(sorted(versions.items())), sorted(accounts), image, ssh_port, adb_server, cpus, memory_gib, swap_gib


def client_key(name):
    return HERE / "local_keys" / name / "id_ed25519"


def login_key_file(name):
    public_key = client_key(name).with_suffix(".pub")
    key_list = public_key.with_name("authorized_keys")
    return key_list if os.path.lexists(key_list) else public_key


def check_login_keys(path):
    if not path.is_file() or path.resolve() != path:
        raise ValueError(f"Expected a public key file at {path}")
    lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if path.suffix == ".pub" and len(lines) != 1:
        raise ValueError(f"Expected one public key in {path}")
    if any(not line.startswith("ssh-ed25519 ") for line in lines):
        raise ValueError(f"Expected one Ed25519 public key per line in {path}")


def write_ssh_config(name, key, port):
    path = key.with_name("ssh_config")
    if path.resolve() != path or (path.exists() and not path.is_file()):
        raise ValueError(f"Expected a regular SSH config at {path}")
    if not path.exists():
        identity = key.as_posix()
        drive = re.fullmatch(r"/(?:mnt/|mnt/host/|run/desktop/mnt/host/)([a-z])/(.+)", identity)
        if drive:
            identity = f"{drive[1].upper()}:/{drive[2]}"
        identity = identity.replace("%", "%%").replace('"', r'\"')
        with tempfile.TemporaryDirectory(dir=key.parent) as temp:
            draft = Path(temp) / "ssh_config"
            draft.write_text(f"Host {name}\n    HostName 127.0.0.1\n    Port {port}\n"
                             f"    User root\n    HostKeyAlias {name}\n"
                             f'    IdentityFile "{identity}"\n    IdentitiesOnly yes\n', encoding="utf-8")
            if os.name == "nt":
                run("icacls", draft, "/inheritance:r", "/grant:r", f"{output('whoami')}:F",
                    "*S-1-5-18:F", "*S-1-5-32-544:F")
            else:
                draft.chmod(0o600)
            try:
                os.link(draft, path)
            except FileExistsError:
                pass
    print(f"SSH config: {path}")


def generate_client_key(name, ssh_port):
    key = client_key(name)
    public = key.with_suffix(".pub")
    directory = key.parent.parent
    if key.resolve() != key or public.resolve() != public:
        raise ValueError("Expected regular files inside local_keys/")
    directory.mkdir(mode=0o700, exist_ok=True)
    if os.name == "nt":
        run("icacls", directory, "/inheritance:r", "/grant:r", f"{output('whoami')}:(OI)(CI)F",
            "*S-1-5-18:(OI)(CI)F", "*S-1-5-32-544:(OI)(CI)F")
    else:
        directory.chmod(0o700)
    key.parent.mkdir(mode=0o700, exist_ok=True)
    if not key.exists() and not public.exists():
        with tempfile.TemporaryDirectory(dir=directory) as temp:
            staged_key = Path(temp) / key.name
            run("ssh-keygen", "-q", "-t", "ed25519", "-C", name, "-f", staged_key)
            try:
                os.link(staged_key, key)
            except FileExistsError:
                pass
            else:
                try:
                    os.link(staged_key.with_suffix(".pub"), public)
                except OSError:
                    key.unlink()
                    raise
    if not key.is_file() or not public.is_file():
        raise ValueError(f"Incomplete key pair in {key.parent}")
    print(f"IdentityFile {key.as_posix()}")
    print(f"Public key: {public}")
    if ssh_port is not None:
        write_ssh_config(name, key, ssh_port)


def output(*args):
    return subprocess.check_output(args, text=True).strip()


def run(*args):
    subprocess.run(args, check=True)


def bind(source, target, readonly=False):
    mode = ",readonly" if readonly else ""
    return ["--mount", f"type=bind,source={source},target={target}{mode}"]


def grant_account(directory, credentials, account):
    """Publish the current credential inode into this user's read-only view."""
    source = credentials / account / "auth.json"
    if directory.resolve() != directory or source.resolve() != source or not source.is_file():
        raise ValueError(f"Missing or invalid credential for {account}: use login first")
    target = directory / (account + ".json")
    if target.is_file() and not target.is_symlink() and target.samefile(source):
        return
    with tempfile.TemporaryDirectory(dir=directory.parent) as temp:
        link = Path(temp) / "auth.json"
        os.link(source, link)
        link.replace(target)


def user_accounts(directory, credentials, initial):
    if directory.resolve() != directory:
        raise ValueError(f"Unexpected account directory: {directory}")
    if not directory.exists():
        directory.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory.parent) as temp:
            stage = Path(temp) / "accounts"
            stage.mkdir(mode=0o700)
            for account in initial:
                grant_account(stage, credentials, account)
            stage.rename(directory)


def revoke_account(directory, user, account):
    names = output("docker", "ps", "-a", "--filter", f"label={LABEL}", "--format", "{{.Names}}").splitlines()
    running = []
    containers = json.loads(output("docker", "inspect", *names)) if names else []
    for item in containers:
        owner = item["Config"]["Labels"][LABEL].split("/", 1)[0]
        if owner != user:
            continue
        destinations = {m["Destination"] for m in item["Mounts"]}
        if any(p.startswith("/codex-accounts/") for p in destinations):
            raise ValueError(f"Rebuild {item['Name']} once to migrate its account mounts")
        if "/codex-accounts" in destinations and item["State"]["Running"]:
            running.append(item["Name"].lstrip("/"))
    (directory / (account + ".json")).unlink(missing_ok=True)
    failures = []
    for name in running:
        if subprocess.run(["docker", "exec", name, "codex-switch", "--revoke", account]).returncode:
            failures.append(name)
    if failures:
        raise RuntimeError("Account removed. Retry revoke to stop its processes in: " + ", ".join(failures))


def default_baseline(root, tag, target):
    folder = root / "aosp/.baselines" / CATALOG[tag]
    if not folder.exists():
        return None, ""
    manifest = json.loads((folder / "manifest.json").read_text())
    if (not (folder / "READY").is_file() or not (folder / "out").is_dir()
            or manifest["source_tag"] != tag or manifest["baseline_id"] != folder.name):
        raise ValueError(f"Incomplete or mismatched baseline: {folder}")
    return folder, folder.name if target and target == manifest["target"] else ""


def remove_entry(parent, relative):
    # Anchor deletion to the parent directory even if a running container renames a subdirectory.
    parts = relative.split("/")
    if any(part in ("", ".", "..") for part in parts) or parent.resolve() != parent:
        raise ValueError("Expected a relative path inside the selected directory")
    target = parent.joinpath(*parts)
    mounts = json.loads(output("findmnt", "--json", "--list", "-o", "TARGET"))["filesystems"]
    if any(Path(m["target"]).is_relative_to(target) for m in mounts):
        raise ValueError(f"Active mount under {target}")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(parent, flags)
    try:
        for part in parts[:-1]:
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
        subprocess.run(["rm", "-rf", "--one-file-system", "--", f"/proc/self/fd/{fd}/{parts[-1]}"],
                       pass_fds=(fd,), check=True)
    finally:
        os.close(fd)


def delete_user(root, user):
    home = root / "workspaces" / user
    if home.resolve() != home:
        raise ValueError(f"Unexpected user directory: {home}")
    listing = output("docker", "ps", "-a", "--filter", f"label={LABEL}", "--format",
                     '{{.Names}}\t{{.Label "local-manager.workspace"}}')
    for line in listing.splitlines():
        name, owner = line.split("\t")
        if owner.split("/", 1)[0] == user:
            run("docker", "stop", name)
            run("docker", "rm", name)
    for state in sorted(home.glob("aosp-android-*")):
        if (state / ".local-manager").is_file():
            detach(home / "shared", state, root / "aosp/.baselines" / CATALOG[state.name[5:]])
            run("sh", HERE / "host/workspace-host.sh", "stop", root, user, state.name[5:], "")
    if home.exists():
        remove_entry(home.parent, user)


def main():
    parser = argparse.ArgumentParser(description=__doc__, epilog=(
        "Use sync after editing versions. Use rebuild after changing container settings. "
        "To delete version data, unload the version first."
    ))
    parser.add_argument("action", choices="keygen login grant revoke create start stop sync rebuild delete delete-shared delete-user enter".split())
    parser.add_argument("subject", nargs="?", help="Version for enter or delete, relative path for delete-shared")
    parser.add_argument("--config", type=Path, default=HERE / "workspace.json")
    parser.add_argument("--account")
    parser.add_argument("--confirm")
    args = parser.parse_args()
    root, user, versions, accounts, image, ssh_port, adb_server, cpus, memory_gib, swap_gib = config(args.config)
    action = args.action
    if action == "delete":
        if args.subject not in CATALOG:
            parser.error("Specify the version to delete")
    elif action == "enter" and args.subject:
        if args.subject not in versions:
            parser.error("Version is not configured for this user")
    elif args.subject and action != "delete-shared":
        parser.error("Unexpected argument for " + action)
    name = user
    if action == "keygen":
        generate_client_key(name, ssh_port)
        return
    if os.name != "posix":
        parser.error("Run workspace management commands on the Linux host")
    import fcntl
    if not root.is_absolute():
        raise ValueError("disk_root must be the absolute SSD mountpoint")
    root = root.resolve()
    if action == "delete-shared":
        if not args.subject or any(p in ("", ".", "..") for p in args.subject.split("/")):
            parser.error("Specify a file or directory relative to shared/")
        first = args.subject.split("/")[0]
        if first == "baseline" or (first.startswith("aosp-") and first[5:] in CATALOG):
            parser.error("This name is reserved for a container mount")
    confirmations = {"delete": f"{user}/{args.subject}", "delete-user": user, "delete-shared": f"{user}/shared/{args.subject}"}
    if action in confirmations and args.confirm != confirmations[action]:
        raise ValueError(f"Deletion requires --confirm {confirmations[action]}")
    if os.geteuid() != 0 or output("findmnt", "-rn", "-o", "UUID", "--mountpoint", root) != "9fa88778-a01d-458f-932b-f59d3673e77d":
        raise RuntimeError("Run as root with the AOSP SSD mounted")
    lock = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)

    home = root / "workspaces" / user
    shared = home / "shared"
    if action == "delete-user":
        delete_user(root, user)
        return
    if action == "delete-shared":
        remove_entry(shared, args.subject)
        return
    codex = root / "development/codex"
    runtime = codex / "runtime"
    credentials = codex / "credentials"
    authorized = home / "accounts"

    def workspace(step, tag, output_id=""):
        run("sh", HERE / "host/workspace-host.sh", step, root, user, tag, output_id)

    def unload(tag):
        detach(shared, home / f"aosp-{tag}", root / "aosp/.baselines" / CATALOG[tag])
        workspace("stop", tag)

    if action in ("login", "grant", "revoke"):
        if not args.account or not re.fullmatch(r"[a-z][a-z0-9_-]{0,31}", args.account):
            raise ValueError("Specify a valid --account alias")
    if action in ("grant", "revoke"):
        user_accounts(authorized, credentials, accounts)
        if action == "grant":
            grant_account(authorized, credentials, args.account)
        else:
            revoke_account(authorized, user, args.account)
        return
    if action == "login":
        if not os.access(runtime / "current/bin/codex", os.X_OK):
            raise ValueError(f"Install the Codex runtime first: {runtime}")
        directory = credentials / args.account
        if directory.resolve() != directory:
            raise ValueError(f"Unexpected credential directory: {directory}")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=directory) as temp:
            run("docker", "run", "--pull=never", "--rm", "-it",
                *bind(runtime, "/opt/codex", True), *bind(temp, "/account"),
                "-e", "CODEX_HOME=/account", image, "/opt/codex/current/bin/codex",
                "-c", 'cli_auth_credentials_store="file"', "login", "--device-auth")
            (Path(temp) / "auth.json").replace(directory / "auth.json")
        for link in (root / "workspaces").glob("*/accounts/" + args.account + ".json"):
            grant_account(link.parent, credentials, args.account)
        return

    listing = output("docker", "ps", "-a", "--format", '{{.Names}}\t{{.Label "local-manager.workspace"}}')
    item = None
    for line in listing.splitlines():
        container, _, owner = line.partition("\t")
        if owner.startswith(user + "/"):
            raise ValueError(f"Stop and remove the old version container {container} before using this workspace")
        if container == name:
            if owner != user:
                raise ValueError(f"Container {name} belongs to another workspace")
            item = json.loads(output("docker", "inspect", name))[0]

    saved = [p.name[5:] for p in sorted(home.glob("aosp-android-*")) if (p / ".local-manager").is_file()]
    if action == "stop":
        if item and item["State"]["Running"]:
            run("docker", "stop", name)
        for tag in saved:
            unload(tag)
        return
    if action == "delete":
        project = f"/workspace/aosp-{args.subject}"
        if (args.subject in versions or mounted(shared / f"aosp-{args.subject}")
                or mounted(shared / "baseline" / args.subject)
                or (item and any(m["Destination"] == project for m in item["Mounts"]))):
            raise ValueError("Remove this version from versions and sync before deleting its data")
        workspace("delete", args.subject)
        return

    plans = {tag: default_baseline(root, tag, target) for tag, target in versions.items()}
    policy_hash = hashlib.sha256(SECCOMP_PROFILE.read_bytes()).hexdigest()
    settings = [str(shared), image, "rslave", policy_hash]
    if adb_server:
        settings.append({"adb_server": adb_server})
    if ssh_port is not None:
        login_keys = login_key_file(user)
        settings.extend([ssh_port, str(login_keys)])
    limits = []
    if cpus is not None:
        limits.extend(["--cpus", str(cpus)])
    if memory_gib is not None:
        limits.extend(["--memory", f"{memory_gib}g"])
    if swap_gib is not None:
        limits.extend(["--memory-swap", f"{memory_gib + swap_gib}g"])
    settings.extend(limits)
    config_label = json.dumps(settings)
    if item and action != "rebuild" and item["Config"]["Labels"].get("local-manager.config") != config_label:
        raise ValueError("Workspace configuration changed: use rebuild")
    if action == "sync" and not (item and item["State"]["Running"]):
        raise ValueError("Start the workspace before syncing versions")
    if action == "enter":
        if not item or not item["State"]["Running"]:
            raise ValueError("Start the workspace first")
        os.close(lock)
        options = []
        if args.subject:
            project = f"/workspace/aosp-{args.subject}"
            pin_file = home / f"aosp-{args.subject}" / ".out-baseline"
            pin = pin_file.read_text().strip() if pin_file.is_file() else ""
            if not mounted(shared / f"aosp-{args.subject}" / "out") or pin != plans[args.subject][1]:
                raise ValueError("Version mounts have changed: use sync")
            options = ["-w", project, "-e", f"AOSP_SOURCE_DIR={project}", "-e", f"OUT_DIR={project}/out",
                       "-e", f"AOSP_BUILD_TARGET={versions[args.subject] or ''}"]
        run("docker", "exec", "-it", *options, name, "bash")
        return

    if not item or action == "rebuild":
        output("docker", "image", "inspect", "--format", "{{.Id}}", image)
        if cpus is not None and cpus > int(output("docker", "info", "--format", "{{.NCPU}}")):
            raise ValueError("cpus exceeds the number of CPUs available to Docker")
    user_accounts(authorized, credentials, accounts)
    runtime.mkdir(parents=True, exist_ok=True)
    if ssh_port is not None:
        check_login_keys(login_keys)
    if shared.resolve() != shared:
        raise ValueError(f"Unexpected shared directory: {shared}")
    shared.mkdir(mode=0o700, parents=True, exist_ok=True)
    if "shared" not in output("findmnt", "-no", "PROPAGATION", "--target", shared).split(","):
        raise ValueError(f"Enable mount propagation on the SSD first: mount --make-shared {root}")
    base_point = shared / "baseline"
    if base_point.is_symlink() or (base_point.exists() and not base_point.is_dir()):
        raise ValueError(f"Expected a directory for baseline mounts: {base_point}")
    for tag, (_, pin) in plans.items():
        for point in (shared / f"aosp-{tag}", base_point / tag):
            if point.resolve() != point or (point.exists() and not mounted(point)
                                           and (not point.is_dir() or any(point.iterdir()))):
                raise ValueError(f"Expected an empty mountpoint: {point}")
        if tag in saved:
            workspace("check-output", tag, pin)
        run("sh", HERE / "host/public-sources.sh", "mount", root, tag)
    if action == "rebuild":
        if item and item["State"]["Running"]:
            run("docker", "stop", name)
        if item:
            run("docker", "rm", name)
        item = None
    for tag in sorted(set(saved) - versions.keys()):
        unload(tag)

    options = [*limits, "--mount", f"type=bind,source={shared},target=/workspace,bind-propagation=rslave"]
    if adb_server:
        options += ["-e", f"ADB_SERVER_SOCKET=tcp:{adb_server}"]
    for tag, (baseline, pin) in plans.items():
        state = home / f"aosp-{tag}"
        view = state / "view"
        pin_file = state / ".out-baseline"
        old_pin = pin_file.read_text().strip() if pin_file.is_file() else ""
        if mounted(view) and old_pin != pin:
            unload(tag)
        workspace("create", tag, pin)
        project = shared / state.name
        publish(view, project)
        # A retry can encounter a source whose nested out/ was already detached.
        if not mounted(project / "out"):
            publish(view / "out", project / "out")
        elif not (project / "out").samefile(view / "out"):
            raise ValueError(f"Unexpected output mount: {project / 'out'}")
        if baseline:
            base_point.mkdir(exist_ok=True)
            publish(baseline, base_point / tag, readonly=True)
    if item:
        if not item["State"]["Running"]:
            run("docker", "start", name)
        if action == "sync":
            print(f"{user}: mounted versions: {', '.join(versions) or '(none)'}")
        return
    command = ["sleep", "infinity"]
    if ssh_port is not None:
        host_keys = home / "ssh"
        if host_keys.resolve() != host_keys:
            raise ValueError(f"Unexpected SSH host key directory: {host_keys}")
        host_keys.mkdir(mode=0o700, exist_ok=True)
        options += ["--publish", f"{ssh_port}:22", *bind(login_keys, "/run/local-manager/client.pub", True),
                    *bind(host_keys, "/var/lib/local-manager/ssh")]
        command = ["/usr/local/bin/local-manager-sshd"]
    profile = home / "codex"
    if profile.resolve() != profile:
        raise ValueError(f"Unexpected Codex directory: {profile}")
    profile.mkdir(mode=0o700, exist_ok=True)
    options += [*bind(profile, "/codex-home"), *bind(runtime, "/opt/codex", True),
                *bind(authorized, "/codex-accounts", True),
                *bind(HERE / "codex/codex", "/usr/local/bin/codex", True),
                *bind(HERE / "codex/codex-switch", "/usr/local/bin/codex-switch", True),
                "-e", "CODEX_HOME=/codex-home"]
    run("docker", "run", "--pull=never", "-d", "--name", name, "--security-opt", f"seccomp={SECCOMP_PROFILE}",
        "--label", f"{LABEL}={user}", "--label", f"local-manager.config={config_label}", *options,
        "-w", "/workspace", "-e", "HOME=/root", image, *command)


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, KeyError, RuntimeError, subprocess.CalledProcessError) as error:
        sys.exit(str(error))
