# Tailscale 基础网络

[整体架构](../main.md) | [HY2 代理扩展](../hy2/setup.md)

## 部署前提

- 环境: Ubuntu 22.04, systemd, 公网连接.
- 执行身份: root Bash.
- 执行位置: 本文件所在的 `tailscale` 目录.

部署通过公网 OpenSSH 或服务商控制台操作. 客户端安装 Tailscale 并加入同一个 Tailnet. 系统 OpenSSH 保持启用.

```bash
set -euo pipefail
```

## 1. 安装 Tailscale

```bash
apt-get update
apt-get install -y ca-certificates curl
install -d -m 0755 /usr/share/keyrings
curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/jammy.noarmor.gpg \
  -o /usr/share/keyrings/tailscale-archive-keyring.gpg
curl -fsSL https://pkgs.tailscale.com/stable/ubuntu/jammy.tailscale-keyring.list \
  -o /etc/apt/sources.list.d/tailscale.list
apt-get update
apt-get install -y tailscale
systemctl enable --now tailscaled
tailscale version
```

配置基线为 Tailscale 1.104.4, 安装版本由官方软件源提供.

## 2. 注册设备

```bash
tailscale up
tailscale ip -4
tailscale status
```

打开命令返回的链接完成登录, 将服务器加入自己的 Tailnet. 在管理端设置设备名称并开启 MagicDNS.

## 3. 配置出口转发

```bash
install -m 0644 files/etc/default/tailscaled /etc/default/tailscaled
install -m 0644 files/etc/sysctl.d/99-tailscale.conf /etc/sysctl.d/99-tailscale.conf
sysctl -p /etc/sysctl.d/99-tailscale.conf
systemctl restart tailscaled
```

启动参数指定 Tailscale 传输端口. 转发设置允许服务器承载客户端互联网流量. 服务器及服务商防火墙允许所需的 UDP 41641 通信.

## 4. 启用出口和 SSH

```bash
tailscale set --accept-dns=true --accept-routes=false --exit-node= \
  --advertise-exit-node --ssh=true
```

Tailscale SSH 接管 Tailnet 地址的 TCP 22. 公网地址使用 OpenSSH.

在 Tailscale 管理端完成:

1. 批准服务器发布的 IPv4 和 IPv6 出口路由.
2. 允许客户端访问服务器 TCP 22, 并通过 exit node 访问互联网.
3. 在 SSH 策略中允许你的 Tailscale 用户登录服务器 root 账号.

网络访问规则和 SSH 登录授权分别配置.

## 5. 客户端使用

日常 SSH 使用 [HY2 连接](../hy2/setup.md#ssh-与文件传输).

使用原生 Tailscale SSH 时, 保持 Tailscale 连接并关闭 TUN. 将 `SERVER_TAILNET_IP` 替换为服务器的 Tailnet IPv4:

```bash
ssh root@SERVER_TAILNET_IP
```

直接使用服务器的 exit node 时, 在客户端选择此服务器并关闭 Clash 系统代理和 TUN. 国内外公网流量由服务器转发. 访问本地局域网时开启 `Allow LAN access`.

本机使用 [Clash 分流](../hy2/setup.md#客户端使用) 时, 不选择其他 exit node. 通过分流网关向其他设备提供分流, 见 [共享分流](../hy2/setup.md#通过-exit-node-共享分流).

## 官方资料

- [Tailscale Linux 安装](https://tailscale.com/download/linux)
- [Tailscale 出口节点](https://tailscale.com/docs/features/exit-nodes)
- [Tailscale SSH](https://tailscale.com/docs/features/tailscale-ssh)
