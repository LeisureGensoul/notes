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

配置基线为 Tailscale 1.104.4. 官方软件源安装其当前提供的版本, 使用命令输出确认实际版本.

## 2. 注册设备

```bash
tailscale up
tailscale ip -4
tailscale status
```

打开命令返回的链接完成登录, 将服务器加入自己的 Tailnet. 在管理端设置设备名称并确认 MagicDNS 开启.

## 3. 配置出口转发

```bash
install -m 0644 files/etc/default/tailscaled /etc/default/tailscaled
install -m 0644 files/etc/sysctl.d/99-tailscale.conf /etc/sysctl.d/99-tailscale.conf
sysctl -p /etc/sysctl.d/99-tailscale.conf
systemctl restart tailscaled
```

启动参数指定 Tailscale 传输端口. 转发设置允许服务器承载客户端互联网流量. 服务器及服务商防火墙允许所需的 UDP 41641 通信.

## 4. 启用出口和 SSH

通过公网 OpenSSH 或服务商控制台执行:

```bash
tailscale set --accept-dns=true --accept-routes=false --exit-node= \
  --advertise-exit-node --ssh=true
```

开启 Tailscale SSH 会接管 Tailnet 地址的 TCP 22. 经该地址建立的现有 SSH 会话可能断开. 公网 OpenSSH 保持原状.

在 Tailscale 管理端完成:

1. 批准服务器发布的 IPv4 和 IPv6 出口路由.
2. 允许授权设备访问服务器 TCP 22, 并允许所需的 exit node 上网访问.
3. 在 SSH 策略中授权实际用户登录目标系统账号.

网络访问规则和 SSH 登录授权分别配置.

## 5. 验收

服务器执行:

```bash
systemctl is-enabled tailscaled
systemctl is-active tailscaled
sysctl net.ipv4.ip_forward net.ipv6.conf.all.forwarding
tailscale status
tailscale debug prefs
```

预期 Tailscale 自启动启用, 服务为 active, 两项转发值为 1. SSH 开启, 出口路由已通告并在管理端批准.

客户端登录 SSH, 将地址替换为服务器的 Tailscale IPv4. 策略须已授权 root 登录:

```bash
ssh root@SERVER_TAILNET_IP
```

预期进入服务器 root 会话, 执行 `exit` 返回客户端.

在客户端执行:

1. 关闭 Clash TUN.
2. 在 Tailscale 中选择此服务器作为 exit node.
3. 使用 curl 绕过应用代理, 查看公网出口地址:

```bash
curl --fail --show-error --noproxy '*' --connect-timeout 5 --max-time 10 \
  --max-filesize 4096 --write-out '\nHTTP %{http_code}\n' https://api.ipify.org
```

预期返回服务器的公网出口地址和 `HTTP 200`, 命令退出码为 0. 完成后恢复日常的 TUN 和 exit node 设置.

## 官方资料

- [Tailscale Linux 安装](https://tailscale.com/download/linux)
- [Tailscale 出口节点](https://tailscale.com/docs/features/exit-nodes)
- [Tailscale SSH](https://tailscale.com/docs/features/tailscale-ssh)
