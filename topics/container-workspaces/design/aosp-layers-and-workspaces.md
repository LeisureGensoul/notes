# AOSP 差分存储与工作区

```mermaid
%%{init: {"flowchart": {"rankSpacing": 50, "nodeSpacing": 32, "subGraphTitleMargin": {"top": 0, "bottom": 14}}}}%%
flowchart BT
    subgraph PUB["公共源码"]
        subgraph L1["第一层 · 共享基线"]
            base["aosp/aosp-#60;基线版本#62;/"]
        end
        subgraph L2["<nobr>第二层 · 版本差分层（可选） · aosp/.layers/aosp-#60;版本#62;/</nobr>"]
            delta["upper/ · 版本差异"]
            vw["work/ · 挂载辅助"]
        end
        saved["out/"]
        base -.-> delta
    end
    subgraph MID[" "]
        sourceNote["版本源码"]
        view(["公共版本源码视图<br/>aosp/aosp-#60;版本#62;/"])
    end
    buildNote["基础构建（可选）"]
    style MID fill:none,stroke-dasharray:6 4
    classDef note fill:none,stroke:none
    class sourceNote,buildNote note
    subgraph STATE["<nobr>第三层 · 可写上层 · workspaces/#60;用户#62;/aosp-#60;版本#62;/</nobr>"]
        subgraph SOURCE_WRITE["<span style='display:inline-block;width:28em;text-align:left;padding-top:0.15em'>源码差分</span>"]
            upper["upper/ · 用户修改"]
            uw["<nobr>work/ · 挂载辅助</nobr><br/><nobr>src/ 源码 · out/ 输出</nobr>"]
        end
        subgraph OUTPUT_WRITE["<span style='display:inline-block;width:12em;text-align:right;padding-top:1.3em'>构建差分</span>"]
            ownOut["out/ · 用户修改"]
        end
    end
    %% 两个边框在 work/ 节点处重叠。
    style SOURCE_WRITE fill:none
    style OUTPUT_WRITE fill:none,transform-box:fill-box,transform-origin:right center,scale:2.22 0.76,vector-effect:non-scaling-stroke
    subgraph USER["用户工作区 · shared/ → 容器 /workspace/"]
        src(["<nobr>/workspace/aosp-#60;版本#62;/</nobr>"])
    end
    upper --> src
    uw ~~~ src
    ownOut -->|挂到 out/| src
    delta --- sourceNote --> upper
    saved --- buildNote --> ownOut

    %% 仅约束排布：左右两条数据流保持并排，公共版本源码视图居中。
    vw ~~~ view
    view ~~~ uw
    base ~~~ vw
    base ~~~ saved
```

## 路径约定

以下路径相对于 `disk_root` 指定的 SSD 挂载点，Ubuntu 默认使用 `/mnt/aosp`。

```text
aosp/
├── aosp-android-14.0.0_r1/        共享基线，实际存放源码
├── aosp-android-15.0.0_r36/       公共版本源码视图的挂载点
├── aosp-android-16.0.0_r4/        公共版本源码视图的挂载点
├── .layers/
│   ├── aosp-android-15.0.0_r36/   upper/ 存版本差异，work/ 辅助挂载
│   └── aosp-android-16.0.0_r4/    同上
└── .baselines/<基线标识>/         每版一套，含 out/、logs/ 和发布信息

workspaces/<用户>/
├── shared/                        容器的 /workspace/
│   ├── notes/                     用户笔记（示例）
│   └── tools/                     用户工具（示例）
├── accounts/                      用户的 Codex 授权，容器只读
├── codex/                         用户的 Codex 配置和对话
├── ssh/                           用户容器的 SSH 主机密钥（可选）
└── aosp-<版本>/
    ├── upper/
    ├── work/
    ├── out/
    └── view/                      容器的 /workspace/aosp-<版本>/
```

- **公共版本源码视图**。Android 14 直接使用共享基线。Android 15、16 各自叠加版本 `upper/`，在对应挂载点提供只读源码。
- **基础构建**。`aosp/.baselines/<基线标识>/out/` 只读挂到 `aosp/aosp-<版本>/out/`，与 `art/`、`build/` 等源码目录并列。
- **用户工作区**。`shared/` 挂到容器的 `/workspace/`。主机将所选版本的 `view/` 和其中的 `out/` 一起挂入 `shared/aosp-<版本>/`。挂载变化会同步到运行中的容器。

## 写入与输出

- **源码差分**。用户的源码改动写入工作区的 `upper/`。
- **构建输出**。版本和目标匹配时，复用公共 `out/`，工作区的 `out/` 只保存差分。否则，直接在工作区的 `out/` 中构建。
- **挂载辅助**。源码差分使用 `work/src/`，构建差分使用 `work/out/`。

## 共享基线维护

1. 停止工作区，在 `manager/` 下运行 `sudo sh host/public-sources.sh unmount <SSD挂载点>`。
2. 更新共享基线和受影响的版本差分层。
3. 运行 `sudo sh host/public-sources.sh mount <SSD挂载点> all`，再恢复工作区。[工作区生命周期](workspace-lifecycle.md)
