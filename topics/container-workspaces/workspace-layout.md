# 容器开发工作区

每位用户使用一个工作区和一个容器。所选版本并列挂在 `/workspace/` 下。每个版本的源码与 `out/` 位于同一层。

管理器使用已准备好的 AOSP SSD。公共源码、版本差分层和构建基线保存在盘上；仓库保存管理脚本、配置和基线清单。

```text
aosp-android-15.0.0_r36/
├── art/
├── build/
├── frameworks/
└── out/
```

公共版本的 `out/` 只读。SSD 按用户分目录。

```text
workspaces/<用户>/
├── shared/                各版本共用，对应容器 /workspace/
├── accounts/              容器 /codex-accounts/，只读，存放获准使用的账号凭证
├── codex/                 容器 /codex-home/，存放该用户的配置和对话
├── ssh/                   用户容器的 SSH 主机密钥（可选）
└── aosp-<版本>/           每个版本各一份
```

| 版本目录内 | 用途 |
| --- | --- |
| `view/` | 源码和输出的合并视图，挂载时对应容器的 `/workspace/aosp-<版本>/` |
| `upper/` | 用户的源码改动 |
| `out/` | 目标匹配时保存构建差分，否则保存独立输出 |
| `work/` | 源码和输出的挂载辅助目录 |

[工作区管理](manager/workspace-management.md) · [默认基线清单](manager/default-baselines.tsv)

[AOSP 差分存储](design/aosp-layers-and-workspaces.md) · [工作区生命周期](design/workspace-lifecycle.md) · [Codex 账号与对话](design/codex-management.md)
