# HY2 代理扩展

[整体架构](../main.md) | [Tailscale 基础网络](../tailscale/setup.md)

## 部署前提

先完成 Tailscale 部署. 服务器与客户端已经加入同一个 Tailnet.

- 服务器: Ubuntu 22.04, systemd, x86_64.
- 操作环境: root Bash, 工作目录为本文件所在的 `hy2` 目录.
- 客户端: 支持 Hysteria2 和 Gecko 的 Clash/Mihomo, 与 Tailscale 同时运行并具有 TUN 权限.
- 版本: Hysteria2 2.13.0, 客户端参考内核 Mihomo 1.19.32.

- 部署范围: 首次安装 HY2. 重复执行会重新生成密码和证书.
- 执行顺序: 所有代码块在同一个 Bash 会话中依次执行.
- 错误处理: 命令报错后停止, 处理错误后继续.

```bash
set -euo pipefail
```

## 1. 安装软件

```bash
apt-get update
apt-get install -y ca-certificates curl python3 python3-yaml openssl nftables
```

安装固定版本 Hysteria2, 下载后校验官方发行文件的 SHA256:

```bash
hysteria_download=$(mktemp)
curl -fL https://github.com/HyNetworks/hysteria/releases/download/app/v2.13.0/hysteria-linux-amd64 \
  -o "$hysteria_download"
if printf '%s  %s\n' \
  907ba8c9693edb104b20582681fb7dc15639d5b64a9cbb616a7b539190a86691 \
  "$hysteria_download" | sha256sum -c -; then
  install -m 0755 "$hysteria_download" /usr/local/bin/hysteria
else
  rm -f "$hysteria_download"
  exit 1
fi
rm -f "$hysteria_download"
/usr/local/bin/hysteria version
```

该文件和摘要对应 Linux amd64 v2.13.0. 其他架构使用对应发行文件和摘要.

## 2. 生成部署配置

以下操作在本文件所在的 `hy2` 目录执行.

```bash
umask 077
network_stage=$(mktemp -d)
cp -R files "$network_stage/"
export NETWORK_STAGE="$network_stage"
python3 - <<'PY'
import os, secrets, subprocess
from pathlib import Path

stage = Path(os.environ['NETWORK_STAGE'])
address = subprocess.check_output(['tailscale', 'ip', '-4'], text=True).strip()
if not address or '\n' in address:
    raise SystemExit('Expected one Tailscale IPv4 address')
replacements = {
    'CHANGE_TAILNET_IP': address,
    'CHANGE_HY2_PASSWORD': secrets.token_hex(32),
    'CHANGE_GECKO_PASSWORD': secrets.token_hex(32),
}
for path in stage.rglob('*'):
    if path.is_file():
        text = path.read_text()
        for old, new in replacements.items():
            text = text.replace(old, new)
        path.write_text(text)
print('Configuration generated without printing credentials')
PY
```

## 3. 安装配置与证书

创建专用运行账号和配置目录:

```bash
if ! id hysteria >/dev/null 2>&1; then
  useradd --system --no-create-home --home-dir /nonexistent \
    --shell /usr/sbin/nologin hysteria
fi
install -d -o root -g hysteria -m 0750 /etc/hysteria
install -d -m 0755 /etc/nftables.d /etc/sysctl.d \
  /etc/systemd/system/tailscaled.service.d
```

安装配置文件:

```bash
python3 - <<'PY'
import os
from pathlib import Path

root = Path(os.environ['NETWORK_STAGE']) / 'files'
for source in root.rglob('*'):
    if source.is_file():
        target = Path('/') / source.relative_to(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        # 写入文件时统一设置权限.
        mode = 0o640 if target == Path('/etc/hysteria/config.yaml') else 0o644
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        with os.fdopen(fd, 'wb') as output:
            os.fchmod(output.fileno(), mode)
            output.write(source.read_bytes())
PY
chown root:hysteria /etc/hysteria/config.yaml
```

生成自签证书和私钥, 有效期为 1095 天. 客户端跳过证书验证, 无需配置 SNI 或证书指纹:

```bash
openssl req -x509 -newkey ec -pkeyopt ec_paramgen_curve:P-256 -nodes \
  -keyout /etc/hysteria/server.key \
  -out /etc/hysteria/server.crt \
  -days 1095 -subj '/CN=hysteria'
chown root:hysteria /etc/hysteria/server.crt /etc/hysteria/server.key
chmod 0640 /etc/hysteria/server.crt /etc/hysteria/server.key
rm -rf -- "$network_stage"
unset NETWORK_STAGE network_stage
```

## 4. 启动服务

先校验访问规则, 再加载内核设置和 systemd 配置:

```bash
nft -c -f /etc/nftables.d/hysteria.nft
sysctl -p /etc/sysctl.d/90-hysteria.conf
systemctl daemon-reload
systemctl restart tailscaled
for attempt in {1..20}; do
  if [ "$(cat /sys/class/net/tailscale0/mtu 2>/dev/null || true)" = 1360 ]; then
    break
  fi
  sleep 1
done
test "$(cat /sys/class/net/tailscale0/mtu)" = 1360
systemctl enable --now hysteria.service
```

重启 Tailscale 后等待接口初始化, 最长等待约 20 秒. 实际 MTU 为 1360 后启动 HY2, 依赖的规则服务先加载入站限制.

若等待结束后 MTU 检查失败, 先检查 `journalctl -u tailscaled`, 处理错误后再启动 HY2.

服务器与服务商防火墙允许 Tailscale UDP 41641 通信. HY2 通过 Tailnet 访问, 无需公网放行 UDP 8443.

在 Tailnet 管理端的网络访问策略中, 允许授权客户端访问服务器的 UDP 8443. 本机入站规则和 Tailnet 策略均须允许此连接.

## 5. 客户端配置

### 生成与订阅

客户端与服务器登录同一个 Tailnet, 启用 Tailscale DNS. 在服务器的 `hy2` 目录执行:

```bash
install -d -m 0700 /root/client-config-export
python3 build-config.py \
  /etc/hysteria/config.yaml /root/client-config-export/clash.yaml
```

`clash.yaml` 包含节点, 代理组, DNS, TUN 和分流规则. 地址, 端口和密码读取服务端配置.

通过 Tailscale Serve 提供单文件订阅:

```bash
tailscale serve --bg --http=18080 --set-path=/clash.yaml \
  /root/client-config-export/clash.yaml
tailscale serve status
```

使用命令返回的 `http://设备完整域名:18080/clash.yaml` 作为订阅地址. Tailnet 访问策略须允许客户端访问服务器 TCP 18080.

入口仅通过 Tailnet 访问, HTTP 由 Tailscale 隧道加密传输. Serve 随 Tailscale 启动后自动恢复, 仅提供这份 YAML 文件.

更新生成器或服务端节点参数后, 重新执行上方生成命令. 订阅入口直接提供新配置, 无需重启订阅入口.

客户端通过订阅更新获取配置. 支持定时更新时可开启自动更新.

> 附加说明: 单次文件分发.
>
> 在服务器列出设备. 将 `TARGET_DEVICE` 替换为列表中的设备名或 Tailscale IP, 保留末尾冒号后发送:
>
> ```bash
> tailscale file cp --targets
> tailscale file cp /root/client-config-export/clash.yaml TARGET_DEVICE:
> ```
>
> 接收端保存文件后导入 Clash/Mihomo. 命令行接收端执行:
>
> ```bash
> mkdir -p "$HOME/Downloads"
> tailscale file get "$HOME/Downloads"
> ```

### 导入与启用

1. 在 Clash/Mihomo 中添加订阅地址, 下载完整配置并确认加载成功.
2. 激活配置, 确认 `Overseas` 代理组选择 `Singapore`.
3. 按需使用客户端开关启用或关闭 TUN.

配置保留 TUN 参数, 不指定 `tun.enable`. 订阅更新不依赖 TUN.

直接使用 Mihomo 内核时, 下载配置为 `clash.yaml`, 按需设置 `tun.enable`, 再校验配置:

```bash
mihomo -t -f clash.yaml
```

配置检查成功后, 按所选模式验收.

客户端连接与分流:

- HY2 连接: 绑定客户端的 `Tailscale` 网卡.
- Tailscale 进程: 通过进程规则直连, 使用客户端自身网络建立隧道.
- Tailnet 地址: 排除在 TUN 路由之外.
- `.ts.net`: 使用 Tailscale DNS.
- 内网地址: 直连.
- 国内域名: 直连, 使用国内 DNS.
- 国内 IP: 直连, 覆盖 IPv4 和 IPv6.
- 其余域名: 通过 HY2 查询 DNS, 按解析结果匹配国内 IP 规则.
- 未匹配流量: 通过 HY2.

国内域名和 IP 使用 [MetaCubeX 规则集](https://github.com/MetaCubeX/meta-rules-dat/tree/meta/geo). 客户端通过 HY2 下载并缓存, 每 24 小时更新.

域名分流与 DNS 共用 `cn-domain`, IP 分流使用 `cn-ip`.

| 使用模式 | Clash TUN | Tailscale exit node |
| --- | --- | --- |
| HY2 接管应用流量 | 开启 | 不选择 |
| HY2 系统或应用代理 | 关闭 | 不选择 |
| Exit node | 关闭 | 选择服务器 |

## 6. 验收

在服务器执行只读检查:

```bash
systemctl is-enabled hysteria
systemctl is-active hysteria hysteria-firewall
cat /sys/class/net/tailscale0/mtu
ss -lunp 'sport = :8443'
nft list table inet hysteria
sysctl net.ipv4.ip_nonlocal_bind
tailscale serve status
```

完成条件:

- HY2 自启动启用, 代理服务处于 active, 规则服务为 active/exited.
- Tailscale 接口 MTU 为 1360, 地址绑定设置为 1.
- HY2 仅绑定服务器 Tailscale IPv4 的 UDP 8443.
- 规则拒绝非 `tailscale0` 和非 `lo` 的 HY2 入站连接.
- Tailnet 策略允许客户端访问 HY2 UDP 8443 和订阅 TCP 18080.
- Serve 将 `/clash.yaml` 映射到生成的客户端配置文件.

### 客户端验收

在装有 curl 的客户端执行. 将 7890 替换为 Mihomo 的实际 HTTP 或 mixed 监听端口, 确认代理组已选中 `Singapore`:

```bash
curl --fail --show-error --noproxy '' --proxy http://127.0.0.1:7890 \
  --connect-timeout 5 --max-time 10 --max-filesize 16384 \
  --write-out '\nHTTP %{http_code}\n' https://example.com/
```

预期返回网页正文和 `HTTP 200`, 命令退出码为 0.

在客户端确认:

- 订阅下载成功, `cn-domain` 和 `cn-ip` 已加载.
- 日常访问中, 国内网站命中 `DIRECT`, 国外网站命中 `Overseas`.
- 开启 TUN 时检查流量接管, 关闭时检查系统或应用代理.

## 官方资料

- [Hysteria2 发行文件](https://github.com/apernet/hysteria/releases)
- [Hysteria2 服务端配置](https://hysteria.network/docs/advanced/Full-Server-Config/)
- [Mihomo Hysteria2 节点](https://wiki.metacubex.one/config/proxies/hysteria2/)
- [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve)
