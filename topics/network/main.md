# 网络设置

## Tailscale 基础网络

Tailscale 通过设备身份和访问策略连接个人设备与服务器, 构成基础网络.

- 设备互联: 通过 Tailscale 地址或 MagicDNS 设备名访问服务器.
- 远程管理: Tailscale SSH 提供 Tailnet 内登录. 系统 OpenSSH 提供公网私钥登录入口.
- 互联网出口: 客户端选择服务器作为 exit node, 由服务器转发到互联网.

```text
客户端 -> Tailscale exit node -> 服务器公网 -> 互联网
```

Tailscale 管理设备连接和出口 NAT. 设备注册, DNS, 访问策略, SSH 授权和出口节点批准属于 Tailnet 管理状态.

| 文件 | 职责 |
| --- | --- |
| [tailscaled](tailscale/files/etc/default/tailscaled) | Tailscale 进程启动参数 |
| [99-tailscale.conf](tailscale/files/etc/sysctl.d/99-tailscale.conf) | exit node 的 IPv4 和 IPv6 转发 |

## HY2 代理扩展

HY2 在 Tailscale 私有网络内提供基于 QUIC 的代理传输. 客户端 Mihomo 负责规则分流, 服务器 Hysteria2 负责连接目标网站.

```text
应用 -> 客户端 Mihomo -> Tailscale -> 服务器 Hysteria2 -> 目标网站
```

HY2 代理与 exit node 由客户端选择.

- 接入: HY2 绑定 Tailscale 地址, 入站规则限制访问接口.
- 认证: 客户端自动读取节点中的 HY2 密码和 Gecko 密码.
- 加密: 服务端证书和私钥用于 QUIC 连接. 客户端跳过证书验证, 网络身份和访问授权由 Tailscale 管理.
- 传输: Gecko 对 QUIC 长头握手包分片.
- 运行: systemd 管理自启动和异常恢复. HY2 依赖 Tailscale 与访问规则服务, 随 Tailscale 重启.
- 权限: HY2 使用专用账号. 进程隔离集中在主服务文件.
- 订阅: Tailscale Serve 在 Tailnet 内提供完整客户端配置, 客户端通过订阅更新获取.

| 文件 | 职责 |
| --- | --- |
| [config.yaml](hy2/files/etc/hysteria/config.yaml) | 监听, 认证, TLS 和 Gecko |
| [hysteria.service](hy2/files/etc/systemd/system/hysteria.service) | 账号, 依赖, 恢复和隔离 |
| [hysteria-firewall.service](hy2/files/etc/systemd/system/hysteria-firewall.service) | 访问规则加载 |
| [hysteria.nft](hy2/files/etc/nftables.d/hysteria.nft) | Tailnet 入站限制 |
| [40-mtu.conf](hy2/files/etc/systemd/system/tailscaled.service.d/40-mtu.conf) | Tailscale 接口 MTU, 作用于整个接口 |
| [90-hysteria.conf](hy2/files/etc/sysctl.d/90-hysteria.conf) | 服务启动时的地址绑定 |
| [build-config.py](hy2/build-config.py) | 从服务端配置生成完整 Clash/Mihomo YAML |

## 部署入口

1. [Tailscale 基础网络](tailscale/setup.md): 安装, 设备注册, exit node 和 SSH.
2. [HY2 代理扩展](hy2/setup.md): 代理部署, 客户端配置生成和订阅导入.

各目录的 `files` 对应服务器绝对路径. 客户端配置生成器位于 `hy2/build-config.py`.

服务端配置位于 `/etc/hysteria`. 生成的客户端配置位于 `/root/client-config-export/clash.yaml`.
