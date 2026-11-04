"""Read-only workspace monitor for Ubuntu and WSL; Python standard library only."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import threading
import time
import unicodedata
from codex.status import account_state

LABEL = "local-manager.workspace"
DISKS = ("/mnt/aosp", "/mnt/wsl/AOSP", "/run/desktop/mnt/host/wsl/AOSP",
         "/mnt/host/wsl/AOSP")
CODEX_STATUS = Path(__file__).parent / "codex/status.py"
COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"


def paint(text, code):
    return f"\033[{code}m{text}\033[0m" if COLOR and code else text


def load_color(ratio):
    return "31" if ratio >= 0.95 else "33" if ratio >= 0.8 else ""


def width(text):
    text = re.sub(r"\033\[[0-9;]*m", "", text)
    return sum(2 if unicodedata.east_asian_width(char) in "WF" else 1 for char in text)


def centered(text, columns):
    space = columns - width(text)
    left = space // 2
    return " " * left + text + " " * (space - left)


def account_panel(quotas):
    data = [["ACCOUNT", "QUOTA", "CREDITS"]]
    now = time.time()
    time_width = percent_width = 0
    for name, (windows, credits) in sorted(quotas.items()):
        if isinstance(windows, str):
            quota = windows  # Pending or unavailable.
        else:
            parts = []
            for reset_at, remaining in windows:
                left = f"{max(0, reset_at - now) / 3600:.1f}h" if reset_at is not None else "—"
                percent = f"{remaining:g}%"
                parts.append((left, percent))
                time_width = max(time_width, len(left))
                percent_width = max(percent_width, len(percent))
            quota = parts or "—"
        data.append([name, quota, credits])
    window_count = max((len(row[1]) for row in data if isinstance(row[1], list)), default=0)
    for row in data[1:]:
        if isinstance(row[1], list):
            pairs = [f"{left:>{time_width}} : {percent}" for left, percent in row[1]]
            slot = time_width + len(" : ") + percent_width + len(", ")
            quota = "".join((pair + ", ").ljust(slot) for pair in pairs[:-1]) + pairs[-1]
            row[1] = quota.ljust(window_count * slot - len(", "))
    widths = [max(width(row[i]) for row in data) for i in range(len(data[0]))]
    lines = []
    for index, row in enumerate(data):
        cells = []
        for column, cell in enumerate(row):
            code = ""
            if cell in ("---", "—"):
                code = "2"
            elif cell == "N/A":
                code = "31"
            elif column == 1:
                remaining = [float(value) for value in re.findall(r": ([\d.]+)%", cell)]
                if remaining:
                    code = load_color((100 - min(remaining)) / 100)
            cells.append(centered(paint(cell, code), widths[column]))
        line = "  ".join(cells).rstrip()
        lines.append(line if index else paint(line, "1;36"))
    return lines


def overview(left, right, columns):
    columns = min(columns, shutil.get_terminal_size((120, 24)).columns)
    left_width = max(map(width, left))
    right_width = max(map(width, right))
    if left_width + right_width + 2 > columns:
        return left + [""] + right
    start = columns - right_width
    lines = []
    for index in range(max(len(left), len(right))):
        line = left[index] if index < len(left) else ""
        if index < len(right):
            line += " " * (start - width(line)) + right[index]
        lines.append(line)
    return lines


def command(*args, timeout=15):
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or f"{args[0]} exited {result.returncode}")
    return result.stdout.strip()


def gib(value):
    return f"{value / 1024 ** 3:.1f} GiB"


def compact_number(value):
    if abs(value) >= 100:
        return f"{value:.0f}"
    return f"{value:.1f}".removesuffix(".0")


def meter(used, total, cells=12, percentage=True, label=None):
    ratio = used / total if total else 0
    if label:
        cells = max(cells, len(label) + 2)
    filled = round(min(1, max(0, ratio)) * cells)
    code = load_color(ratio) or "36"
    if COLOR:
        track = (label or "").center(cells)
        foreground = "97" if code == "31" else "30"
        text = (paint("[", code) + paint(track[:filled], f"{int(code) + 10};{foreground}")
                + paint(track[filled:], code) + paint("]", code))
    else:
        track = "█" * filled + "░" * (cells - filled)
        if label:
            start = (cells - len(label)) // 2
            track = track[:start] + label + track[start + len(label):]
        text = f"[{track}]"
    if percentage:
        text += paint(f" {compact_number(ratio * 100):>4}%", code)
    return text


def cpu_sample():
    # guest counters are already included in user/nice.
    fields = [int(n) for n in Path("/proc/stat").read_text().splitlines()[0].split()[1:9]]
    return sum(fields), fields[3] + fields[4]


def split_meter(wsl_ratio, host_ratio, cells=12):
    upper = round(min(1, max(0, wsl_ratio)) * cells)
    lower = round(min(1, max(0, host_ratio or 0)) * cells)
    both = min(upper, lower)
    # Upper-half glyph: foreground paints WSL, background paints Windows.
    overlap = paint("▀" * both, "36;45") if COLOR else "█" * both
    track = (overlap + paint("▀" * (upper - both), "36") + paint("▄" * (lower - both), "35")
             + (" " if COLOR else "░") * (cells - max(upper, lower)))
    return f"[{track}]"


def windows_values(sample):
    cpu = ram = "N/A"
    try:
        with sample.open() as source:
            if time.time() - os.fstat(source.fileno()).st_mtime < 15:
                cpu_fields = source.readline().split()
                ram_fields = source.readline().split()
                if len(cpu_fields) == 2:
                    percent, cores = map(int, cpu_fields)
                    cpu = percent / 100, str(cores)
                if len(ram_fields) == 2:
                    total, available = map(int, ram_fields)  # Windows reports KiB.
                    if total > 0:
                        ram = (total - available) / total, compact_number(total / 1024 ** 2)
    except FileNotFoundError:
        cpu = ram = "---"
    except (OSError, ValueError):
        pass
    return {"CPU": cpu, "RAM": ram}


def host_lines(before, after, requested, windows=None):
    elapsed, idle = after[0] - before[0], after[1] - before[1]
    cpu = 100 * (elapsed - idle) / elapsed if elapsed else 0
    mem = {}
    for line in Path("/proc/meminfo").read_text().splitlines():
        key, value, *_ = line.split()
        mem[key.rstrip(":")] = int(value) * 1024
    wsl = "microsoft" in Path("/proc/sys/kernel/osrelease").read_text().lower()
    title = "WSL host" if wsl else "Linux host"
    ram = mem['MemTotal'] - mem['MemAvailable']
    swap = mem['SwapTotal'] - mem['SwapFree']
    capacities = [("RAM", ram, mem['MemTotal']), ("SWAP", swap, mem['SwapTotal'])]
    path = disk_path(requested)
    if path:
        try:
            stat = os.statvfs(path)
        except OSError as error:
            disk_note = f"SSD   {path} unavailable: {error}"
        else:
            total = stat.f_blocks * stat.f_frsize
            capacities.append(("SSD", total - stat.f_bfree * stat.f_frsize, total))
            disk_note = f"  └─→ {path}"
    else:
        disk_note = f"SSD     mount not found: {requested}" if requested else "SSD     mount not found; use --disk"
    capacity_width = max(len(compact_number(total / 1024 ** 3)) for _, _, total in capacities)
    metrics = [("CPU", cpu / 100, str(os.cpu_count()), "cores")]
    for name, used, total in capacities:
        metrics.append((name, used / total if total else 0, compact_number(total / 1024 ** 3).rjust(capacity_width), "GiB"))
    lines = [paint("WSL" if wsl else "Linux", "36") + " / " + paint("Host", "35") if windows else title]
    for name, ratio, capacity, unit in metrics:
        if windows and name in windows:
            reading = windows[name]
            if isinstance(reading, tuple):
                host_ratio, host_capacity = reading
                host_percent = compact_number(host_ratio * 100) + "%"
            else:
                host_ratio, host_percent, host_capacity = None, reading, reading
            bar = split_meter(ratio, host_ratio, cells=10)
            wsl_percent = paint(f"{compact_number(ratio * 100):>4}%", load_color(ratio) or "36")
            host_percent = paint(host_percent.ljust(5), load_color(host_ratio or 0) or "35")
            percentages = f"{wsl_percent} / {host_percent}"
            lines.append(f"{name:<5}{bar} {percentages} {capacity:>{capacity_width}}/{host_capacity} {unit}")
        elif windows:
            percent = paint(f"{compact_number(ratio * 100):>4}%", load_color(ratio) or "36")
            lines.append(f"{name:<5}{meter(ratio, 1, cells=10, percentage=False)} {percent}{' ' * 9}{capacity} {unit}")
        else:
            lines.append(f"{name:<5}{meter(ratio, 1)} {capacity} {unit}")
    lines.append(disk_note)
    if not windows:
        lines[0] = centered(lines[0], max(map(width, lines))).rstrip()
    return lines


def disk_path(requested):
    paths = [requested] if requested else list(DISKS)
    return next((Path(path).resolve() for path in paths if os.path.ismount(path)), None)


def cpu_limit(host):
    quota = host.get("NanoCpus", 0) / 1e9
    if not quota and host.get("CpuQuota", 0) > 0:
        quota = host["CpuQuota"] / (host.get("CpuPeriod") or 100000)
    cpuset = host.get("CpusetCpus", "")
    if cpuset:
        count = 0
        for part in cpuset.split(","):
            ends = part.split("-")
            count += int(ends[-1]) - int(ends[0]) + 1
        quota = min(quota, count) if quota else count
    return quota or None


def inspect_containers(user, sizes=False):
    selector = f"label={LABEL}" + (f"={user}" if user else "")
    ids = command("docker", "ps", "-aq", "--filter", selector).split()
    if not ids:
        return []
    options = ["--size"] if sizes else []
    return json.loads(command("docker", "inspect", *options, *ids, timeout=120 if sizes else 15))


def memory_gib(value):
    match = re.fullmatch(r"([\d.]+)\s*(B|KiB|MiB|GiB|TiB|PiB)", value)
    if not match:
        return None
    units = ("B", "KiB", "MiB", "GiB", "TiB", "PiB")
    return float(match[1]) * 1024 ** (units.index(match[2]) - 3)


def resource_cell(used, limit):
    if limit is None:
        return ("unlimited" if used is None else f"{compact_number(used)} / unlimited"), None
    label = f"{limit:g}"
    if used is None:
        return label, None
    return f"{label} ({compact_number(100 * used / limit)}%)", (used, limit)


def container_rows(user):
    containers = inspect_containers(user)
    running = [item["Id"] for item in containers if item["State"]["Running"]]
    stats = {}
    if running:
        for line in command("docker", "stats", "--no-stream", "--format", "{{json .}}", *running).splitlines():
            stat = json.loads(line)
            stats[stat["Name"]] = stat
    rows = []
    for item in sorted(containers, key=lambda x: x["Name"]):
        name = item["Name"].lstrip("/")
        host = item["HostConfig"]
        stat = stats.get(name)
        used_cores = used_ram = None
        if stat:
            percent = stat["CPUPerc"].removesuffix("%")
            if percent and percent != "--":
                used_cores = float(percent) / 100
            used_ram = memory_gib(stat["MemUsage"].split(" / ")[0])
        cpu = resource_cell(used_cores, cpu_limit(host))
        ram = resource_cell(used_ram, host["Memory"] / 1024 ** 3 or None)
        rows.append([name, item["State"]["Status"], cpu, ram])
    return rows


def resource_snapshot(user):
    try:
        return container_rows(user), []
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        return [], [f"Container status unavailable: {error}"]


def table(rows, columns=None):
    selected = {row[0]: row[6] for row in rows}
    display = []
    for row in rows:
        accounts = row[7]
        if not COLOR:
            accounts = ",".join("*" + name if name == row[6] and name not in ("---", "—") else name
                                for name in accounts.split(","))
        display.append([*row[:2], row[2][0], row[3][0], *row[4:6], accounts])
    data = [["USER", "STATE", "CPU", "RAM (GiB)",
             "WORKSPACE", "CONTAINER", "CODEX"], *display]

    def styled(cell, column, account):
        code = ""
        if cell in ("---", "—"):
            code = "2"
        elif "N/A" in cell:
            code = "31"
        elif column == 1:
            code = {"running": "32", "paused": "33", "restarting": "33", "dead": "31"}.get(cell, "2")
        elif column == 6:
            return ",".join(paint(name, "1;36") if name == account else name for name in cell.split(","))
        return paint(cell, code)

    minimum = [4, 7, 13, 13, 9, 9, 7]
    widths = [max(minimum[i], *(width(row[i]) for row in data)) for i in range(len(data[0]))]
    required = max([16, *(width(row[col]) + 4 for row in display for col in (2, 3))])
    other_columns = sum(widths[:2]) + sum(widths[4:]) + len(widths) - 1
    available = ((columns or 0) - other_columns) // 2
    widths[2] = widths[3] = max(required, min(18, available))

    def line(cells):
        return " ".join(centered(cell, size) for cell, size in zip(cells, widths)).rstrip()

    lines = [paint(line(data[0]), "1;36"), paint(" ".join("-" * size for size in widths), "2")]
    for row, source in zip(display, rows):
        cells = []
        for column, cell in enumerate(row):
            if column in (2, 3):
                values = source[column][1]
                value = meter(*values, cells=widths[column] - 2, percentage=False, label=cell) if values else styled(cell, column, None)
            else:
                value = styled(cell, column, selected[row[0]])
            cells.append(value)
        lines.append(line(cells))
    return lines


def workspace_size(path):
    # Exclude every mounted view, including bind mounts on the same filesystem.
    # du -x alone does not exclude those and would count shared baselines again.
    mounts = []
    for line in Path("/proc/self/mountinfo").read_text().splitlines():
        mount = re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), line.split()[4])
        if mount.startswith(str(path) + "/"):
            mounts.append(f"--exclude={mount}")
    result = command("du", "-sx", "--block-size=1", *mounts, "--", str(path), timeout=120)
    return gib(int(result.split()[0]))


def storage_snapshot(requested, user):
    rows, quotas, notes = {}, {}, []
    disk = disk_path(requested)
    if disk:
        try:
            for path in sorted((disk / "workspaces").iterdir()):
                if path.is_symlink() or not path.is_dir() or (user and path.name != user):
                    continue
                try:
                    size = workspace_size(path)
                except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
                    size = "N/A"
                    notes.append(f"{path.name} workspace: {error}")
                selected, accounts = account_state(path / "codex", path / "accounts")
                rows[path.name] = [path.name, size, "—", selected or "—", ",".join(accounts) or "—"]
        except OSError as error:
            notes.append(f"Workspace size unavailable: {error}")
    else:
        notes.append("Workspace size unavailable: SSD not mounted")
    try:
        for item in inspect_containers(user, sizes=True):
            name = item["Name"].lstrip("/")
            row = rows.setdefault(name, [name, *(["—"] * 4)])
            size = item.get("SizeRw")
            row[2] = gib(size) if size is not None and size >= 0 else "N/A"
            destinations = {mount["Destination"] for mount in item.get("Mounts", [])}
            if item["State"]["Running"] and not item["State"].get("Paused") and "/codex-accounts" in destinations:
                try:
                    result = subprocess.run(
                        ["docker", "exec", "-i", item["Id"], "python3", "-", *quotas],
                        input=CODEX_STATUS.read_text(), capture_output=True, text=True, timeout=30,
                    )
                    if result.returncode:
                        raise RuntimeError("Codex status unavailable")
                    status = json.loads(result.stdout)
                    row[3:] = [status["selected"] or "—", ",".join(status["accounts"]) or "—"]
                    quotas.update(status["quotas"])
                except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired):
                    for account in row[4].split(","):
                        if account != "—":
                            quotas.setdefault(account, ["N/A", "N/A"])
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        notes.append(f"Container size unavailable: {error}")
    return rows, quotas, notes


class SnapshotCache:
    def __init__(self, collect, initial, interval):
        self.collect = collect
        self.snapshot = initial
        self.interval = interval
        self.updated = float("-inf")
        self.worker = None

    def refresh(self):
        self.snapshot = self.collect()

    def read(self, wait=False):
        if wait:
            self.refresh()
        elif (self.worker is None or not self.worker.is_alive()) and time.monotonic() - self.updated >= self.interval:
            self.updated = time.monotonic()
            self.worker = threading.Thread(target=self.refresh, daemon=True)
            self.worker.start()
        return self.snapshot


def workspace_placeholders(requested, user):
    disk = disk_path(requested)
    rows = {}
    if disk:
        try:
            for path in (disk / "workspaces").iterdir():
                if path.is_symlink() or not path.is_dir() or (user and path.name != user):
                    continue
                selected, accounts = account_state(path / "codex", path / "accounts")
                rows[path.name] = [path.name, "---", "---", selected or "—", ",".join(accounts) or "—"]
        except OSError:
            pass
    return rows


def positive(value):
    number = float(value)
    if not 0 < number < float("inf"):
        raise argparse.ArgumentTypeError("Interval must be greater than 0")
    return number


def main():
    parser = argparse.ArgumentParser(description="Workspace monitor (Ubuntu / WSL)")
    parser.add_argument("--disk", help="SSD mount point; auto-detect common paths by default")
    parser.add_argument("--user", help="Show only the specified user")
    parser.add_argument("--interval", type=positive, default=1, help="Refresh interval in seconds (default: 1)")
    parser.add_argument("--once", action="store_true", help="Print once and exit")
    parser.add_argument("--windows-host", "--windows-cpu", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if sys.platform != "linux":
        parser.error("Run on Ubuntu or WSL")
    interactive = not args.once and sys.stdout.isatty()
    try:
        if interactive:
            print("\033[?25l", end="", flush=True)
        monitor(args, interactive)
    finally:
        if interactive:
            print("\033[0m\033[?25h", end="", flush=True)


def monitor(args, interactive):
    previous = cpu_sample()
    resources_cache = SnapshotCache(lambda: resource_snapshot(args.user), ([], []), args.interval)
    storage = SnapshotCache(lambda: storage_snapshot(args.disk, args.user), ({}, {}, []), 60)
    time.sleep(0.1)
    while True:
        started = time.monotonic()
        lines = [paint(f"Workspace monitor  {datetime.now():%Y-%m-%d %H:%M:%S}", "1"), ""]
        current = cpu_sample()
        windows = windows_values(args.windows_host) if args.windows_host else None
        host = host_lines(previous, current, args.disk, windows)
        previous = current
        rows, resource_notes = resources_cache.read(wait=args.once)
        sizes, quotas, notes = storage.read(wait=args.once)
        details = workspace_placeholders(args.disk, args.user)
        for name, cached in sizes.items():
            if name in details:
                details[name][1:3] = cached[1:3]
            else:
                details[name] = cached
        accounts = {account: quotas.get(account, ["---", "---"])
                    for detail in details.values() for account in detail[4].split(",")
                    if account not in ("—", "---")}
        resources = {row[0]: row for row in rows}
        combined = []
        for name in sorted(resources.keys() | details.keys()):
            row = resources.get(name, [name, "---", ("---", None), ("---", None)])
            combined.append(row + details.get(name, [name, *(["---"] * 4)])[1:])
        columns = shutil.get_terminal_size((120, 24)).columns - 1 if sys.stdout.isatty() else None
        users = table(combined, columns) if combined else ["No matching workspaces"]
        panel = account_panel(accounts)
        overview_width = max(map(width, users))
        if args.windows_host:
            overview_width = max(map(width, host)) + 2 + max(map(width, panel))
        lines.extend(overview(host, panel, overview_width))
        lines.extend(["", *users])
        lines.extend(["", *resource_notes, *notes])
        if interactive:
            print("\033[H" + "\033[K\n".join(lines) + "\033[K\033[J", flush=True)
        else:
            print("\n".join(lines), flush=True)
        if args.once:
            return
        time.sleep(max(0, args.interval - (time.monotonic() - started)))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
