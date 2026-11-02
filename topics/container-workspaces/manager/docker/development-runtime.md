# 开发运行时

在 `manager/` 目录执行以下命令。

## 工作区镜像

```sh
docker build --platform linux/amd64 -t builder:local docker/
```

镜像基于 Ubuntu 24.04，包含 AOSP 构建工具和 ADB 客户端。可用 `--build-arg UBUNTU_MIRROR=<地址>` 更换软件源。[AOSP 构建环境要求](https://source.android.com/docs/setup/start/requirements)

管理器自动加载工作区的 seccomp 策略。已有容器运行 `sudo python3 workspace.py rebuild` 应用变更。[工作区策略](workspace-seccomp.json) · [上游默认策略](https://github.com/moby/profiles/blob/seccomp/v0.2.4/seccomp/default.json)

## Codex（可选）

```sh
sudo sh docker/install-codex.sh /mnt/aosp
```

这条命令将 SSD 上的公共运行时更新到官方最新稳定版。WSL 使用 `/mnt/wsl/AOSP`。

- **账号后台（daemon）**　运行中的旧版自动重启。
- **交互终端（CLI）**　下次启动 `codex` 即使用新版。已打开的 CLI 可退出后用 `codex resume` 继续原对话。
