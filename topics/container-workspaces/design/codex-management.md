# Codex 统一管理

各工作区共用 Codex 运行时，各自保存本地对话。

```mermaid
flowchart BT
    subgraph SSD["第一层 · SSD 存储"]
        subgraph common["公共资源 · 工作区只读"]
            rt["公共运行时<br/>runtime/"]
            auth["登录凭证 · 每个账号一份<br/>credentials/#60;账号别名#62;/"]
        end
        subgraph privateState["工作区私有 · 独立读写"]
            profile["后台状态 · 工作区内每个账号一份<br/>profiles/#60;账号别名#62;/"]
            history["本地对话 · 每个工作区一份<br/>history/"]
        end
    end
    subgraph ADMIN["第二层 · 管理员"]
        admin["发布运行时 · 维护账号凭证"]
    end
    subgraph USER["第三层 · 用户工作区"]
        backend["当前账号的 Codex 后台"]
        user(["使用者 · 选择账号, 发起或继续对话"])
    end
    rt -.- admin
    auth -.- admin
    admin -.->|指定可用账号| backend
    rt -->|只读| backend
    auth -->|用户授权目录 · 只读| backend
    profile <-->|读写| backend
    history <-->|读写| backend
    backend --- user
    style common stroke:#5386ad,stroke-width:2px
    style privateState stroke:#369078,stroke-width:2px,stroke-dasharray:6 3
```

公共资源位于 `development/codex/`。`workspaces/<用户>/accounts/` 只包含该用户获准使用的凭证。工作区私有数据位于 `workspaces/<用户>/codex/`。

## 工作区生命周期

- **授权**　用户容器只读挂载 `accounts/`。管理员增删授权后立即生效。
- **切换账号**　启动所选账号的后台，关闭其他账号的后台。重复选择也会清理。
- **重建**　保留账号配置和本地对话。
- **删除版本**　保留用户的账号状态和本地对话。
- **删除用户**　删除该用户的账号状态和本地对话。

## 公共维护

管理员统一维护登录凭证和公共运行时。[安装与更新](../manager/docker/development-runtime.md)
