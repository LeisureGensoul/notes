# 工作区运行与生命周期

每个用户有一个工作区和一个容器。主机管理版本挂载，SSD 保存源码改动、构建输出和对话。

## 启动与恢复

```mermaid
flowchart TD
    subgraph HOST["Linux 宿主机"]
        disk["核对 SSD 和工作区标识"]
        first["创建私有目录"]
        reuse["核对并复用原私有目录"]
        src["准备只读版本下层<br/>在工作区挂载源码和 out/"]
        start["按挂载配置启动容器"]
        disk -->|首次启用| first --> src
        disk -->|恢复| reuse --> src
        src --> start
    end
    subgraph CONTAINER["工作区容器"]
        ready(["工作区就绪"])
        codex["以已授权账号启动 Codex"]
        ready -.->|按需| codex
    end
    start --> ready
```

## 运行期间的操作

| 操作 | 容器 | 工作区数据 |
| --- | --- | --- |
| 同步版本 `sync` | 保持运行和 SSH 连接 | 按 `versions` 挂载或卸载，保留数据 |
| 停止 `stop` | 停止并保留 | 卸载版本，保留用户数据 |
| 重建 `rebuild` | 按新配置替换 | 重新挂载原数据 |
| 删除已卸载的版本 `delete` | 保留 | 仅删除指定版本的数据 |
| 删除共享文件或目录 | 保留 | 仅删除 `shared/` 下的指定路径 |
| 删除整个用户 | 移除用户容器 | 删除 `workspaces/<用户>/` |

完整命令见[工作区管理](../manager/workspace-management.md)。
