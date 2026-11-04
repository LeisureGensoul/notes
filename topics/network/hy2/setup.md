# HY2 与分层订阅

[整体架构](../main.md) | [Tailscale 基础网络](../tailscale/setup.md)

## 接入与授权

同一个 HY2 实例提供 Tailnet 和公网接入. 内网订阅返回 21 个节点, 公网订阅按 token 返回对应范围.

| 入口 | 返回节点 | 换代与额度 |
| --- | --- | --- |
| Tailnet 订阅 | `tailscale`, 4 个 large, 16 个 small | `tailscale` 长期有效, 仅允许 Tailnet 来源 |
| large-token | 对应 large 和四个 small | large 每周换代, 每个 70 GB/周 |
| small-token | 对应 small | small 每天换代, 每个 2 GB/天 |

节点名为 `tailscale`, `large-A` 至 `large-D`, `small-A1` 至 `small-D4`. 订阅 token 与 HY2 密码独立生成, 换代不改变节点名.

时间使用 Asia/Singapore, UTC+8. 每天 00:00 更新 small, 每周一 00:00 同时更新 large 和全部 small. 首次签发使用当前周期的剩余时间. 不设重叠期.

刷新只读取配置, 不延长授权或重置额度. large-token 在本周内可以获取四个 small 的当日配置, 不能取得下一周的 large. small-token 不能取得下一天的授权.

旧 token 到期后返回 404, 旧 HY2 密码拒绝认证. 服务每 5 秒检查已有会话, 断开到期或额度耗尽的连接, 失败后重试. large 到期或撤销时, 对应四个 small 一并失效.

额度按凭据身份独立累计, 上传与下载合计. 1 GB = 1,000,000,000 字节. 经 Tailnet 使用 large 或 small 也计入额度.

所有 HY2 节点使用 Brutal, 上传和下载目标速率均为 1000 Mbps. 服务端 UDP 收发缓冲区上限均为 16 MiB.

small 按日重置, large 按周重置. 刷新, 恢复授权和服务重启不清零当期用量. large 额度用尽不扣减下级额度, 其有效 token 仍可读取下级配置.

流量统计来自 HY2 转发数据, 与服务商计费口径不同. 配额及会话撤销存在轮询延迟, 不能作为逐字节的账单硬上限. 统计超过 15 秒未成功时, 暂停公网新认证并清理已有公网会话; Tailnet 认证不依赖额度统计.

## 服务职责

- `subscriptions.py`: 本机订阅与认证服务. SQLite 保存唯一授权状态, 完整事务统一协调换代与记账. 统计与会话清理独立执行. 每轮根据在线身份与有效授权清理旧会话, 失败后重新计算并重试.
- `build-config.py`: 共用 DNS 与分流生成逻辑, 支持导出订阅快照.
- Tailscale Serve: 将内网订阅映射到本机服务的 `/all.yaml`.
- Caddy: 公网 HTTPS 提供 `/s/<token>/clash.yaml` 完整配置和 `/s/<token>/nodes.yaml` 节点数据, 其余路径返回 404.
- Certbot: 自动续期可信 IP 证书. 证书更新后重新加载 Caddy, HY2 在握手时读取证书.

HY2 可访问公网目标. 对服务器自身, 仅放行 TCP 22 和 443, 分别连接 OpenSSH 和公网 HTTPS 订阅. 本机 Tailnet IPv4/IPv6 的 TCP 22 映射到公网 OpenSSH. 其他本机, Tailnet, 私网及元数据地址拒绝访问.

公网 HTTPS 与 HY2 共用一份可信 IP 证书. 客户端正常验证证书. `tailscale` 节点连接 Tailnet IP, 使用公网 IP 作为证书验证名称.

| 端口 | 范围 | 用途 |
| --- | --- | --- |
| TCP 22 | 公网与 Tailnet | OpenSSH / Tailscale SSH |
| UDP 8443 | 公网与 Tailnet IPv4 | HY2 |
| TCP 443 | 公网 IPv4 | token 订阅 |
| TCP 80 | 公网 IPv4 | IP 证书验证路径, 其他请求返回 404 |
| TCP 18080 | Tailnet | 完整内网订阅 |
| TCP 9091 | 本机回环 | 订阅与认证 |
| TCP 9092 | 本机回环 | HY2 统计与会话管理 |

真实配置位于 `/etc/hysteria`, `/etc/hy2-subscriptions` 和 `/etc/caddy`. 授权状态, 证书, ACME 状态和 Certbot 环境位于 `/data/network`, 均在仓库外.

## 首次部署

环境为 Ubuntu 22.04, systemd, Linux amd64. 先完成 Tailscale 部署并确认 OpenSSH 公钥登录可用. 以下命令使用 root Bash, 在本文件所在的 `hy2` 目录依次执行.

需要 Tailscale 1.104.4, Hysteria2 2.13.0, Caddy 2.11.7, Certbot 5.9.0. 客户端支持 Hysteria2 和 Gecko, 参考内核 Mihomo 1.19.32.

已有授权状态时使用后面的更新步骤, 不重新初始化.

### 1. 安装软件

```bash
set -euo pipefail
umask 077
apt-get update
apt-get install -y ca-certificates curl nftables openssl python3-yaml python3-venv
hy2_stage=$(mktemp -d)
cp -R files "$hy2_stage/"
export HY2_STAGE="$hy2_stage"
test ! -e /data/network/subscriptions/state.sqlite3
```

安装固定版本并校验发行文件:

```bash
curl -fL https://github.com/apernet/hysteria/releases/download/app/v2.13.0/hysteria-linux-amd64 \
  -o "$hy2_stage/hysteria"
printf '%s  %s\n' \
  907ba8c9693edb104b20582681fb7dc15639d5b64a9cbb616a7b539190a86691 \
  "$hy2_stage/hysteria" | sha256sum -c -
install -m 0755 "$hy2_stage/hysteria" /usr/local/bin/hysteria
curl -fL https://github.com/caddyserver/caddy/releases/download/v2.11.7/caddy_2.11.7_linux_amd64.tar.gz \
  -o "$hy2_stage/caddy_2.11.7_linux_amd64.tar.gz"
curl -fL https://github.com/caddyserver/caddy/releases/download/v2.11.7/caddy_2.11.7_checksums.txt \
  -o "$hy2_stage/checksums.txt"
(cd "$hy2_stage" && awk '$2 == "caddy_2.11.7_linux_amd64.tar.gz"' checksums.txt | sha512sum -c -)
tar -xzf "$hy2_stage/caddy_2.11.7_linux_amd64.tar.gz" -C "$hy2_stage" caddy
install -m 0755 "$hy2_stage/caddy" /usr/local/bin/caddy
```

### 2. 安装配置

将 `CHANGE_PUBLIC_IP` 替换为服务器公网 IPv4:

```bash
export HY2_PUBLIC_IP=CHANGE_PUBLIC_IP
python3 - <<'PY'
from pathlib import Path
import grp, ipaddress, json, os, pwd, re, secrets, shutil, subprocess
import yaml

stage = Path(os.environ['HY2_STAGE'])
address = str(ipaddress.IPv4Address(os.environ['HY2_PUBLIC_IP']))
device = json.loads(subprocess.check_output(['tailscale', 'status', '--json'], text=True))['Self']
tailnet = next(ip for ip in device['TailscaleIPs'] if ':' not in ip)
tailnet_v6 = next(ip for ip in device['TailscaleIPs'] if ':' in ip)
tailnet_name = device['DNSName'].split('.')[0]
for user in ('hysteria', 'caddy', 'hy2-subscriptions'):
    if subprocess.run(['id', user], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        subprocess.run(['useradd', '--system', '--no-create-home', '--home-dir', '/nonexistent',
                        '--shell', '/usr/sbin/nologin', user], check=True)
if subprocess.run(['getent', 'group', 'network-tls'], stdout=subprocess.DEVNULL).returncode:
    subprocess.run(['groupadd', '--system', 'network-tls'], check=True)
for user in ('hysteria', 'caddy'):
    subprocess.run(['usermod', '-aG', 'network-tls', user], check=True)
for path, owner, group, mode in (
    ('/data/network', 'root', 'root', 0o755),
    ('/data/network/subscriptions', 'hy2-subscriptions', 'hy2-subscriptions', 0o700),
    ('/data/network/caddy', 'caddy', 'caddy', 0o700),
    ('/data/network/tls', 'root', 'network-tls', 0o750),
    ('/data/network/acme-webroot', 'root', 'caddy', 0o750),
    ('/etc/hysteria', 'root', 'hysteria', 0o750),
    ('/etc/hy2-subscriptions', 'root', 'hy2-subscriptions', 0o750),
    ('/etc/caddy', 'root', 'caddy', 0o750),
    ('/usr/local/lib/hysteria', 'root', 'root', 0o755),
):
    target = Path(path)
    target.mkdir(parents=True, exist_ok=True)
    target.chmod(mode)
    os.chown(target, pwd.getpwnam(owner).pw_uid, grp.getgrnam(group).gr_gid)
addresses = json.loads(subprocess.check_output(['ip', '-j', '-6', 'addr', 'show', 'scope', 'global'], text=True))
public_v6 = [a['local'] for interface in addresses for a in interface['addr_info']
             if a.get('local') and ipaddress.ip_address(a['local']).is_global]
replacements = {'CHANGE_PUBLIC_IP': address, 'CHANGE_TAILNET_IP': tailnet,
                'CHANGE_TAILNET_IPV6': tailnet_v6, 'CHANGE_TAILNET_NAME': tailnet_name,
                'CHANGE_TAILNET_FQDN': device['DNSName'].rstrip('.'),
                'CHANGE_GECKO_PASSWORD': secrets.token_urlsafe(32),
                'CHANGE_STATS_SECRET': secrets.token_urlsafe(32)}
if public_v6:
    replacements['CHANGE_PUBLIC_IPV6'] = public_v6[0]
for source in (stage / 'files').rglob('*'):
    if not source.is_file():
        continue
    text = source.read_text()
    text = re.sub(r'CHANGE_[A-Z0-9_]+', lambda match: replacements.get(match[0], match[0]), text)
    if 'CHANGE_PUBLIC_IPV6' in text:
        config = yaml.safe_load(text)
        config['acl']['inline'].remove('reject(CHANGE_PUBLIC_IPV6)')
        text = yaml.safe_dump(config, sort_keys=False)
    source.write_text(text)
    relative = source.relative_to(stage / 'files')
    if str(relative) == 'etc/caddy/Caddyfile':
        continue
    target = Path('/') / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    group = {'etc/hysteria/config.yaml': 'hysteria',
             'etc/hy2-subscriptions/config.yaml': 'hy2-subscriptions'}.get(str(relative))
    target.chmod(0o640 if group else 0o755 if target.suffix == '.sh' else 0o644)
    if group:
        os.chown(target, 0, grp.getgrnam(group).gr_gid)
for name in ('subscriptions.py', 'build-config.py'):
    shutil.copyfile(name, Path('/usr/local/lib/hysteria') / name)
    (Path('/usr/local/lib/hysteria') / name).chmod(0o644)
bootstrap = Path('/etc/caddy/Caddyfile')
bootstrap.write_text(f'''http://{address} {{
    bind {address}
    handle /.well-known/acme-challenge/* {{
        root * /data/network/acme-webroot
        file_server
    }}
    handle {{
        respond 404
    }}
}}
''')
bootstrap.chmod(0o640)
os.chown(bootstrap, 0, grp.getgrnam('caddy').gr_gid)
PY
python3 -m venv /data/network/certbot
/data/network/certbot/bin/pip install 'certbot==5.9.0'
runuser -u hy2-subscriptions -- python3 -B /usr/local/lib/hysteria/subscriptions.py \
  init /etc/hy2-subscriptions/config.yaml
systemctl daemon-reload
systemctl enable --now caddy
```

### 3. 申请证书与启动

服务器及服务商防火墙允许 TCP 80, TCP 443 和 UDP 8443. Tailscale 继续使用 UDP 41641. TCP 9091 和 9092 只监听回环地址.

```bash
/data/network/certbot/bin/certbot certonly --non-interactive --agree-tos \
  --register-unsafely-without-email --preferred-profile shortlived --reuse-key \
  --webroot --webroot-path /data/network/acme-webroot \
  --ip-address "$HY2_PUBLIC_IP" --cert-name hy2-ip \
  --config-dir /data/network/acme/config --work-dir /data/network/acme/work \
  --logs-dir /data/network/acme/logs
RENEWED_LINEAGE=/data/network/acme/config/live/hy2-ip \
  /usr/local/lib/hysteria/deploy-certificate.sh
install -o root -g caddy -m 0640 "$hy2_stage/files/etc/caddy/Caddyfile" /etc/caddy/Caddyfile
caddy fmt --overwrite /etc/caddy/Caddyfile
caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
nft -c -f /etc/nftables.d/hysteria.nft
sysctl -p /etc/sysctl.d/99-hy2-udp-buffers.conf
ip link set dev tailscale0 mtu 1360
systemctl reload caddy
systemctl enable --now hy2-subscriptions hysteria hy2-certificate.timer
tailscale serve --bg --yes --http=18080 --set-path=/clash.yaml http://127.0.0.1:9091/all.yaml
rm -rf -- "$hy2_stage"
unset HY2_STAGE HY2_PUBLIC_IP hy2_stage
```

IP 证书约 6 天有效, 定时器每 6 小时检查续期. 续期复用证书私钥并原子替换文件. HY2 无需为证书续期重启.

Tailnet 访问策略允许客户端访问 TCP 18080 和 UDP 8443. 永久 `tailscale` 凭据仅接受 Tailnet 来源; Tailscale 的入站规则须继续禁止公网接口伪造 Tailnet 来源.

## 客户端使用

- 内网: 连接 Tailscale, 导入 `tailscale serve status` 返回的 `/clash.yaml` 地址.
- 公网: 使用有效的 HTTPS 分享链接直接导入, 首次导入不需要 Tailscale 或 HY2.

下载并启用配置后, 按需选择接管方式:

| 方式 | 接管范围 |
| --- | --- |
| Clash 系统代理 | 本机遵循系统代理设置的应用 |
| Clash TUN | 路由内的 TCP/UDP 流量 |
| Tailscale exit node | 将公网流量交给所选出口设备 |

Clash 的两种接管方式共用规则模式, 本机不选择其他 exit node. 选择 `tailscale` 节点须保持 Tailscale 连接, 公网节点无需连接 Tailscale.

直接使用新加坡 exit node 时, 关闭客户端的 Clash 系统代理和 TUN. 操作见 [Tailscale 使用](../tailscale/setup.md#5-客户端使用).

客户端保留订阅的 hosts, DNS 和 TUN 路由设置. Tailnet 出站接口见 [设备分流](#tailnet-设备分流), 公网 HY2 节点不绑定接口.

内网配置包含 `Large A` 至 `Large D` 和 `Tailscale` 五个分组. 首次启用默认选择 `Large A`, 客户端可保留之前的选择. 分组为手动选择, 不自动检查节点有效性.

每个 Large 组包含一个内置 large 和四个 small. small 来自节点集合 `small-A` 至 `small-D`. large 公网配置包含自身和四个 small, small 公网配置只包含自身.

### 分流与 DNS

- 国内域名: 直连, 使用国内 DNS.
- Tailscale 公网服务: `tailscale.com` 及其子域名直连, DNS 经 `223.5.5.5` 直连解析并返回真实 IP.
- 其余域名: 通过 HY2 解析和访问.
- 纯 IP 请求: 国内 IP 直连, 其余通过 HY2.
- 局域网地址: 本地直连.
- Tailnet 设备: 名单内的业务走 HY2, 其他设备走 Tailscale 原生连接.
- `.lan` 和 `.local`: 使用系统配置的 DNS, 解析后直连. 该 DNS 须能解析这些名称.
- Tailnet 名称: 名单内使用生成的地址映射, 其他名称使用 Tailscale DNS.

`cn-domain` 同时用于域名分流和 DNS, `cn-ip` 仅用于纯 IP 分流. 未识别的域名不因解析结果命中国内 IP 而改为直连.

两个 [MetaCubeX 规则集](https://github.com/MetaCubeX/meta-rules-dat/tree/meta/geo) 通过所选 HY2 下载, 每 24 小时更新.

业务与国外 DNS 使用 `Overseas` 当前所选节点. 关闭 Tailscale 前, 先切换到可用的 large 或 small. HY2 失败时不自动改为直连.

公网 HY2 的承载出口由客户端内核选择.

### 通过 exit node 共享分流

使用 Windows 设备作为分流网关:

1. 在网关启用本配置和 Clash TUN, 在 `Overseas` 选择可用节点.
2. 在 Tailscale 开启 `Run exit node`, 在管理端批准该出口.
3. 其他设备关闭本机的 Clash 系统代理和 TUN, 连接 Tailscale 并选择该网关作为 exit node.

国内请求从网关的本地网络出网, 其余公网请求经所选 HY2. 系统代理不接管 Tailscale 的转发连接.

TUN 以四条 `/1` 接管 IPv4 和 IPv6, 为真实网卡保留 `/0` 默认路由. Tailscale 的底层连接通过默认接口出站, `tailscaled.exe` 转发的业务进入现有分流.

开机后, Tailscale 独立建连; 连通后即可使用 `tailscale` HY2 节点.

### Tailnet 设备分流

在 [授权配置](files/etc/hy2-subscriptions/config.yaml) 的 `hy2_devices` 中维护走 HY2 的设备. 当前只有 Singapore, 未列出的设备使用 Tailscale 原生连接.

- `addresses`: 设备的 Tailnet IPv4 和 IPv6.
- `names`: 短名和完整 MagicDNS 名称.
- `direct_tcp_ports`: 保留原生连接的 TCP 端口. Singapore 的 18080 用于完整内网订阅.

生成器统一生成地址映射, 分流规则和 TUN 主机路由. 删除设备条目即恢复原生连接, 空名单写为 `hy2_devices: {}`. 修改后更新并启用整份订阅.

`tailnet_interface` 指定客户端 Tailscale 接口, 当前为 `Tailscale`. 内置 `tailnet-direct` 出站与 `tailscale` HY2 节点共用此接口. `tailnet-direct` 只承载原生访问和 Tailnet DNS, 不列入 HY2 选择组.

服务端访问权限由 [HY2 ACL](files/etc/hysteria/config.yaml) 管理. 新设备须有可达路径和对应服务许可.

### SSH 与文件传输

在客户端选择有效的 HY2 节点并开启 TUN. 替换服务器的公网 IPv4, Tailnet IPv4 和客户端私钥路径:

```bash
ssh -o HostKeyAlias=SERVER_PUBLIC_IP -i /path/to/private-key root@SERVER_TAILNET_IP
```

`HostKeyAlias` 使用公网 OpenSSH 的主机密钥记录, 与原生 Tailscale SSH 区分. 目标也可使用设备名单中的 IPv6, MagicDNS 名称或公网 IPv4.

VS Code Remote SSH 和 SFTP 使用相同的地址, 私钥和 `HostKeyAlias`.

选择 `tailscale` 时, SSH 经 HY2 over Tailscale; 选择 large 或 small 时使用公网 HY2 并计入对应额度. 所有 HY2 授权共用这条 SSH 出站许可, 登录仍由 OpenSSH 公钥认证控制.

仅开启系统代理时, 在 SSH 中配置 `ProxyCommand` 连接本机代理. 原生 Tailscale SSH 见 [Tailscale 使用](../tailscale/setup.md#5-客户端使用).

### 更新与分享

整份订阅由客户端下载, 启用代理后的下载路径取决于客户端的订阅更新设置. 内网订阅更新需要连接 Tailscale.

| 配置来源 | small 节点更新 | 到期处理 |
| --- | --- | --- |
| 内网完整订阅 | 四个 provider 分别经对应 large 更新 | large 到期后, 连接 Tailscale 更新并启用整份订阅 |
| large 分享链接 | 一个 provider 经该 large 更新四个 small | large 到期后, 向分享者获取新 large 链接并导入 |
| small 分享链接 | 无 provider | small 到期后, 向分享者获取新 small 链接并导入 |

provider 每 12 小时通过内置 large 获取节点数据, 与 `Overseas` 当前选择无关. 更新不依赖 small 的有效性, 流量计入该 large. large 须有效, 有余额且可连接.

首次导入自带当前节点数据. provider 只更新节点; DNS, 分流和 TUN 设置随整份订阅更新.

provider 中的 small 换代后, 到下一次成功更新前最多约 12 小时不可用. 网络失败或内核停止会延长等待. 永久 `tailscale` 节点不依赖公网凭据换代.

provider 更新失败不自动直连. 使用仍有效的分享链接时, 可关闭代理后更新整份配置.

内网完整配置的 YAML 顶部附带 Tailnet 订阅地址. 分享链接按 Large 分组, small 缩进显示. 链接是生成时快照; 节点数据响应包含当日 small 链接. 节点名称固定, 不显示 token.

### 客户端更新操作

| 客户端 | 整份订阅更新 | small 节点集合更新, 通过 large 的 HY2 |
| --- | --- | --- |
| Clash Verge Rev | 订阅 -> 当前订阅 -> 更新 | 代理 -> 代理集合 -> 对应集合右侧刷新按钮 |
| FlClash v0.8.92 | 配置 -> 当前配置 -> 更新 | 代理 -> 右上角菜单 -> 提供者 / Providers -> 对应集合 -> 同步 |

内网配置更新 `small-A` 至 `small-D`; large 分享配置更新自己的集合. 在集合页面查看最近更新时间.

手机通过 Tailscale 获取完整配置后, 切换 FlClash 使用公网节点. 两个 VPN 无需同时运行. 系统停止内核后, 自动更新停止.

界面参考: [Clash Verge Rev](https://github.com/clash-verge-rev/clash-verge-rev/blob/main/src/components/proxy/provider-button.tsx), [FlClash v0.8.92](https://github.com/chen08209/FlClash/blob/v0.8.92/lib/views/proxies/proxies.dart). 其他版本的入口可能不同.

## 更新与管理

在 `/etc/hy2-subscriptions/config.yaml` 调整设备名单或客户端接口. 在 `hy2` 目录更新代码并加载配置, 不初始化授权状态:

```bash
install -m 0644 subscriptions.py build-config.py /usr/local/lib/hysteria/
systemctl restart hy2-subscriptions
systemctl start hysteria
```

修改 HY2 访问规则后, 执行 `systemctl restart hysteria` 加载配置.

查看授权状态:

```bash
python3 -B /usr/local/lib/hysteria/subscriptions.py status /etc/hy2-subscriptions/config.yaml
```

撤销单个授权, 将 `small-A1` 替换为实际名称. 撤销 large 会同时撤销其四个下级:

```bash
python3 -B /usr/local/lib/hysteria/subscriptions.py revoke /etc/hy2-subscriptions/config.yaml small-A1
```

恢复时签发新凭据, 保留本周期已用流量:

```bash
python3 -B /usr/local/lib/hysteria/subscriptions.py restore /etc/hy2-subscriptions/config.yaml small-A1
```

需要本地文件时导出配置, `--snapshot` 从临时数据库副本生成, 不改变运行授权状态:

```bash
install -d -m 0700 /root/client-config-export
python3 -B /usr/local/lib/hysteria/build-config.py \
  /etc/hy2-subscriptions/config.yaml /root/client-config-export/clash.yaml --snapshot
```

导出一个 large 或 small 时, 在命令末尾加 `--scope large-A` 或对应名称.

## 官方资料

- [Hysteria2 认证和服务端配置](https://v2.hysteria.network/docs/advanced/Full-Server-Config/)
- [Hysteria2 统计与会话撤销](https://v2.hysteria.network/docs/advanced/Traffic-Stats-API/)
- [Hysteria2 ACL](https://v2.hysteria.network/docs/advanced/ACL/)
- [可信 IP 证书与 Certbot](https://letsencrypt.org/2026/03/11/shorter-certs-certbot)
- [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve)
- [Tailscale 转发机制](https://tailscale.com/docs/reference/kernel-vs-userspace-routers)
- [Tailscale 基础连接](https://tailscale.com/docs/reference/faq/firewall-ports)
- [Tailscale Windows 出口选择](https://github.com/tailscale/tailscale/blob/v1.104.4/net/netmon/interfaces_windows.go)
- [Mihomo TUN 路由](https://wiki.metacubex.one/config/inbound/tun/#route-address)
- [Mihomo DNS](https://wiki.metacubex.one/config/dns/)
- [Mihomo proxy-providers](https://wiki.metacubex.one/config/proxy-providers/)
- [Mihomo 系统 DNS](https://wiki.metacubex.one/config/dns/type/#system)
- [Clash 系统代理与 TUN](https://www.clashverge.dev/guide/term.html)
- [OpenSSH ProxyCommand](https://man.openbsd.org/ssh_config#ProxyCommand)
