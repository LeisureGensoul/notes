# 工作区管理

每个用户使用一个容器，容器名就是用户名。日常文件放在 `/workspace/`，AOSP 版本按需挂在 `/workspace/aosp-<版本>/`。

## 首次启动

主机需要 Python 3、Docker 和已挂载的 SSD。

- **WSL 2**　在管理员 PowerShell 中运行[接盘脚本](windows/Mount-AospDisk.ps1)。将 `disk_root` 设为 `/mnt/wsl/AOSP`。
- **Ubuntu**　默认挂载到 `/mnt/aosp`。

```sh
sudo mkdir -p /mnt/aosp
sudo mount -U 9fa88778-a01d-458f-932b-f59d3673e77d /mnt/aosp
```

在 `manager/` 下[构建镜像](docker/development-runtime.md)。管理器默认读取 [workspace.json](workspace.json)，其中 `user` 指定要管理的工作区。用以下命令创建并进入该工作区。

```sh
sudo python3 workspace.py create
sudo python3 workspace.py enter
```

## 挂载和卸载版本

在 `versions` 中填写完整版本标签和构建目标。`null` 表示只用该版本的私有输出。`{}` 表示不挂载源码。下面的例子挂载 Android 14 和 15。

```json
{
  "versions": {
    "android-14.0.0_r1": null,
    "android-15.0.0_r36": "aosp_cf_arm64_only_phone-bp1a-userdebug"
  }
}
```

修改列表后，在主机同步版本。

```sh
sudo python3 workspace.py sync
```

`sync` 挂载新增版本，卸载移出列表的版本。容器和 SSH 保持运行。卸载保留源码改动和构建输出，重新挂载后可继续使用。目录被占用时，先退出该目录并结束相关任务，再运行 `sync`。

旧容器首次启用在线挂载需要一次 `rebuild`。若提示 SSD 挂载传播未启用，在主机运行 `sudo mount --make-shared <SSD挂载点>`。

## 构建 AOSP

在主机进入指定版本，例如 `sudo python3 workspace.py enter android-15.0.0_r36`。若该版本已配置目标，在容器内启用该目标。

```sh
source build/envsetup.sh
lunch "$AOSP_BUILD_TARGET"
```

通过 SSH 登录时，先进入 `/workspace/aosp-<版本>/`，再运行 `source build/envsetup.sh` 和 `lunch <目标>`。

目标匹配时，工作区复用公共构建产物，只把改动写入该版本的 `out/`。其他目标直接在 `out/` 中构建。已有的公共构建资料可在 `/workspace/baseline/<版本>/` 查看。

| 版本 | 基础构建目标 |
| --- | --- |
| `android-14.0.0_r1` | `aosp_panther-userdebug` |
| `android-15.0.0_r36` | `aosp_cf_arm64_only_phone-bp1a-userdebug` |
| `android-16.0.0_r4` | `aosp_cf_arm64_only_phone-trunk_staging-userdebug` |

切换构建基线前，先卸载该版本。若 SSD 上该版本的 `out/` 非空，将它移开并新建空目录。把版本和新目标加回列表，再运行 `sync`。

## SSH 登录

**提供者**在 `workspace.json` 中设置 `ssh_port`。每个用户选择不同的主机端口。运行 `python3 workspace.py keygen` 生成密钥，保存在 `local_keys/<用户>/`。新容器用 `create`，已有容器用 `rebuild`。

**使用者**向提供者取得地址、端口和私钥。在本机的 `~/.ssh/config` 保存别名。`IdentityFile` 填本机私钥路径。示例中，主机的 `2222` 端口连接到容器的 `22` 端口。

```sshconfig
Host shuo
    HostName <主机地址>
    Port 2222
    User root
    IdentityFile ~/.ssh/shuo
    IdentitiesOnly yes
```

终端运行 `ssh shuo`。VS Code 使用 **Remote-SSH: Connect to Host…** 选择同一别名，再打开 `/workspace/` 或某个版本目录。

多人共用私钥时，访问权限只能一起撤销。若要单独授权，将 `id_ed25519.pub` 复制为同目录的 `authorized_keys`。每行放一把公钥，增删后运行 `rebuild`。

## ADB 连接手机

手机接在主机上。容器里的 ADB 客户端连接主机的 `5037` 端口。多个容器可共用这个服务，看到同一组设备。

**首次配置**

1. **主机**　让 ADB server 监听容器可达的地址，只向可信客户端开放。
2. **工作区**　在 `workspace.json` 中设置 `adb_server`。本机 Docker Desktop 使用 `host.docker.internal:5037`。其他主机填实际地址和端口。`null` 使用 ADB 默认地址。修改此项后，已有容器运行 `sudo python3 workspace.py rebuild`。

**日常使用**

接入手机，开启 USB 调试并确认授权。在主机运行 `adb devices -l`，确认状态为 `device`。然后在容器终端或 VS Code 中选择手机。

```sh
adb devices -l
adb -s <序列号> shell
```

更换手机后重新列出设备即可。[ADB 命令说明](https://android.googlesource.com/platform/packages/modules/adb/+/refs/heads/main/docs/user/adb.1.md)

## Codex 账号（可选）

先[安装 Codex](docker/development-runtime.md)，再为配置中的用户授权。授权立即生效，所有 AOSP 版本共用。

| 主机操作 | `sudo python3 workspace.py` 子命令 |
| --- | --- |
| 登录或更新凭证 | `login --account <账号>` |
| 授权 / 撤销 | `grant --account <账号>` / `revoke --account <账号>` |

容器内用 `codex-switch` 查看账号，`codex-switch <账号>` 切换账号。撤销会结束该用户以此账号运行的 Codex 进程。账号配置和对话保存在 SSD 的 `workspaces/<用户>/codex/`。

## 容器配置与维护

`cpus` 默认上限为 16 个逻辑 CPU，支持小数。`memory_gib` 默认上限为 32 GiB。省略字段使用默认值，设为 `null` 取消该项限制。

| 主机操作 | `sudo python3 workspace.py` 子命令 |
| --- | --- |
| 停止 / 恢复工作区 | `stop` / `start` |
| 应用镜像、资源、SSH 或 ADB 配置（会断开连接） | `rebuild` |
| 删除已卸载版本的数据 | `delete <版本> --confirm <用户>/<版本>` |
| 删除共享文件或目录 | `delete-shared <相对路径> --confirm <用户>/shared/<相对路径>` |
| 删除用户容器及其工作区 | `delete-user --confirm <用户>` |
