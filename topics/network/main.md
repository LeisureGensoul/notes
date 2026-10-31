# 网络设置

## Tailscale 基础网络

Tailscale 通过设备身份和访问策略连接个人设备与服务器, 提供设备互联, SSH 和 exit node.

- 设备互联: 使用 Tailscale 地址或 MagicDNS 名称.
- 远程管理: Tailnet 内使用 Tailscale SSH, 公网 OpenSSH 保留私钥登录.
- 互联网出口: 选择服务器作为 exit node, 由服务器转发流量.
- 内网订阅: Tailscale Serve 提供全部 HY2 节点与分享信息.

```text
设备 -> Tailscale -> SSH, 设备互联, exit node, 完整订阅
```

设备注册, DNS, 网络访问策略, SSH 授权和出口路由批准属于 Tailnet 管理状态.

## HY2 代理与授权

同一个 HY2 服务提供 Tailnet 和公网接入. `tailscale` 仅允许 Tailnet 来源, large 和 small 提供有期限和额度的公网代理.

```text
应用 -> Mihomo -> Tailnet 或公网 HY2 -> 目标网站
```

内网订阅返回 1 个 tailscale, 4 个 large 和 16 个 small. large-token 返回自身和四个下级, small-token 仅返回自身. 每次订阅直接生成完整 YAML, DNS 和分流逻辑集中维护.

- 认证: 订阅 token 与 HY2 密码分别签发. 服务器按授权身份检查期限, 来源和额度.
- 生命周期: large 每周换代, small 每天换代, 无重叠期. 到期拒绝新认证并清理旧会话.
- 分发: 内网通过 Tailscale Serve, 公网通过 HTTPS token 路径. 公网不提供完整内网配置.
- 加密: 内外 HY2 正常校验可信 IP 证书, Certbot 自动续期.
- 隔离: HY2 拒绝访问本机, Tailnet, 私网及元数据地址. 运行进程使用专用账号和 systemd 隔离.

## 文件职责

| 文件 | 职责 |
| --- | --- |
| [Tailscale 部署](tailscale/setup.md) | 设备注册, SSH 和出口转发 |
| [HY2 部署](hy2/setup.md) | 双入口, 分层订阅, 配额和客户端操作 |
| [subscriptions.py](hy2/subscriptions.py) | 授权状态, 订阅范围, 认证, 换代与会话撤销 |
| [build-config.py](hy2/build-config.py) | 共用客户端配置生成与快照导出 |
| [test_subscriptions.py](hy2/test_subscriptions.py) | 授权隔离, 到期, 配额持久化和订阅边界测试 |
| [Tailscale 启动参数](tailscale/files/etc/default/tailscaled) | 传输端口 |
| [出口转发](tailscale/files/etc/sysctl.d/99-tailscale.conf) | IPv4 和 IPv6 转发 |
| [HY2 配置](hy2/files/etc/hysteria/config.yaml) | 监听, TLS, 认证回调, Gecko, 统计与出站限制 |
| [授权配置](hy2/files/etc/hy2-subscriptions/config.yaml) | 地址, 状态位置和额度 |
| [Caddy 配置](hy2/files/etc/caddy/Caddyfile) | HTTPS token 入口和证书验证路径 |
| [HY2 服务](hy2/files/etc/systemd/system/hysteria.service) | 代理进程, 依赖与隔离 |
| [授权服务](hy2/files/etc/systemd/system/hy2-subscriptions.service) | 本机订阅与认证进程 |
| [Caddy 服务](hy2/files/etc/systemd/system/caddy.service) | HTTPS 入口进程 |
| [Tailnet 入站规则](hy2/files/etc/nftables.d/hysteria.nft) | HY2 的 Tailnet 地址入口限制 |
| [规则加载服务](hy2/files/etc/systemd/system/hysteria-firewall.service) | 加载入站规则 |
| [Tailscale MTU](hy2/files/etc/systemd/system/tailscaled.service.d/40-mtu.conf) | Tailnet HY2 的隧道 MTU |
| [证书续期服务](hy2/files/etc/systemd/system/hy2-certificate.service) | 执行 Certbot 续期 |
| [证书续期定时器](hy2/files/etc/systemd/system/hy2-certificate.timer) | 每 6 小时检查续期 |
| [证书发布脚本](hy2/files/usr/local/lib/hysteria/deploy-certificate.sh) | 原子替换证书文件并重新加载 HTTPS 入口 |

各目录的 `files` 对应服务器绝对路径. 真实授权状态, 凭据和证书位于仓库外, 实际数据集中在 `/data/network`.
