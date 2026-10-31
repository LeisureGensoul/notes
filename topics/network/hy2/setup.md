# HY2 与分层订阅

[整体架构](../main.md) | [Tailscale 基础网络](../tailscale/setup.md)

## 接入与授权

同一个 HY2 实例提供 Tailnet 和公网接入. 内网订阅返回 21 个节点, 公网订阅按 token 返回对应范围. 每次请求直接返回完整 YAML, 不拉取下级订阅.

| 入口 | 返回节点 | 换代与额度 |
| --- | --- | --- |
| Tailnet 订阅 | `tailscale`, 4 个 large, 16 个 small | `tailscale` 长期有效, 仅允许 Tailnet 来源 |
| large-token | 对应 large 和四个 small | large 每周换代, 每个 70 GB/周 |
| small-token | 对应 small | small 每天换代, 每个 2 GB/天 |

四组授权名为 `large-A` 至 `large-D`, 下级分别为 `small-A1` 至 `small-D4`. 授权名保持稳定, 订阅 token 和 HY2 密码分别生成.

节点使用固定名称 `tailscale`, `large-A` 和 `small-A1` 等. token 与密码换代不改变节点名.

时间使用 Asia/Singapore, UTC+8. 每天 00:00 更新 small, 每周一 00:00 同时更新 large 和全部 small. 首次签发使用当前周期的剩余时间. 不设重叠期.

刷新只读取配置, 不延长授权或重置额度. large-token 在本周内可以获取四个 small 的当日配置, 不能取得下一周的 large. small-token 不能取得下一天的授权.

旧 token 到期后返回 404, 旧 HY2 密码拒绝认证. 服务每 5 秒检查已有会话, 断开到期或额度耗尽的连接, 失败后重试. large 到期或撤销时, 对应四个 small 一并失效.

额度按凭据身份独立累计, 经 Tailnet 使用 large 或 small 也不豁免. 上传与下载合计. 1 GB = 1,000,000,000 字节. small 按日重置, large 按周重置; 刷新, 恢复授权和服务重启不清零当期用量. 暂不限速. large 额度用尽不扣减下级额度, 其有效 token 仍可读取下级配置.

流量统计来自 HY2 转发数据, 与服务商计费口径不同. 配额及会话撤销存在轮询延迟, 不能作为逐字节的账单硬上限. 统计超过 15 秒未成功时, 暂停公网新认证并清理已有公网会话; Tailnet 认证不依赖额度统计.

## 服务职责

- `subscriptions.py`: 本机订阅与认证服务. SQLite 保存唯一授权状态, 完整事务统一协调换代与记账. 统计与会话清理独立执行. 每轮根据在线身份与有效授权清理旧会话, 失败后重新计算并重试.
- `build-config.py`: 共用 DNS 与分流生成逻辑, 支持导出订阅快照.
- Tailscale Serve: 将内网订阅映射到本机服务的 `/all.yaml`.
- Caddy: 公网 HTTPS 只转发 `/s/<token>/clash.yaml`, 其余路径返回 404.
- Certbot: 自动续期可信 IP 证书. 证书更新后重新加载 Caddy, HY2 在握手时读取证书.

公网 HTTPS 与 HY2 共用一份可信 IP 证书. 客户端正常验证证书, 不使用 `skip-cert-verify`. `tailscale` 节点连接 Tailnet IP, 使用公网 IP 作为证书验证名称.

| 端口 | 范围 | 用途 |
| --- | --- | --- |
| UDP 8443 | 公网与 Tailnet IPv4 | HY2 |
| TCP 443 | 公网 IPv4 | token 订阅 |
| TCP 80 | 公网 IPv4 | IP 证书验证路径, 其他请求返回 404 |
| TCP 18080 | Tailnet | 完整内网订阅 |
| TCP 9091 | 本机回环 | 订阅与认证 |
| TCP 9092 | 本机回环 | HY2 统计与会话管理 |

真实配置位于 `/etc/hysteria`, `/etc/hy2-subscriptions` 和 `/etc/caddy`. 授权状态, 证书, ACME 状态和 Certbot 环境位于 `/data/network`, 均在仓库外.

## 首次部署

环境为 Ubuntu 22.04, systemd, Linux amd64. 先完成 Tailscale 部署. 以下命令使用 root Bash, 在本文件所在的 `hy2` 目录依次执行.

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
tailnet = subprocess.check_output(['tailscale', 'ip', '-4'], text=True).strip()
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
             if ipaddress.ip_address(a['local']).is_global]
replacements = {'CHANGE_PUBLIC_IP': address, 'CHANGE_TAILNET_IP': tailnet,
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

按订阅入口导入配置:

- 内网: 连接 Tailscale, 添加 `tailscale serve status` 返回的 `/clash.yaml` 地址. 在 `Overseas` 中选择分组, 再选节点.
- 公网: 直接添加收到的 HTTPS 分享链接, 无需连接 Tailscale. large 在 `Overseas` 中直接列出五个节点, small 只列出自身.

下载并激活配置后, 按需开启 TUN. 关闭 TUN 时使用系统或应用代理.

内网配置保留 `Tailscale` 和 `Large A` 至 `Large D` 五组. 每个 Large 组包含自身及四个 small.

YAML 顶部按 Large 分组列出分享链接, small 缩进显示. 内网包含全部链接, large 包含自身及四个 small, small 仅包含自身. 复制完整链接即可分享. 配置各节之间留空行.

订阅只返回所属范围的信息. 公网 HTTP 不提供订阅, HTTPS 仅接受正确的 token 路径. 响应禁止缓存, 请求路径不写入访问日志.

支持自动刷新并应用配置的客户端可开启定时更新. small 到期后从有效 large 或内网重新获取链接; large 到期后从内网重新获取. 旧链接不能自行续期.

| 使用模式 | TUN | Tailscale |
| --- | --- | --- |
| `tailscale` HY2 | 按需 | 保持连接 |
| large 或 small HY2 | 按需 | 使用期间无需连接 |
| 获取完整内网订阅 | 不依赖 | 需要连接 |
| exit node | 关闭 | 选择服务器作为出口 |

配置不指定 `tun.enable`. 直接运行 Mihomo 内核时自行设置开关, 使用 `mihomo -t -f clash.yaml` 校验.

DNS 和分流共用一套规则: Tailnet 和内网直连, `.ts.net` 使用 Tailscale DNS. 国内域名使用国内 DNS 并直连; 其余域名经所选 HY2 查询 DNS, 再匹配国内 IP, 未命中则走 HY2.

`cn-domain` 同时用于域名分流和 DNS, `cn-ip` 用于 IP 分流. 两个 [MetaCubeX 规则集](https://github.com/MetaCubeX/meta-rules-dat/tree/meta/geo) 通过所选 HY2 下载, 每 24 小时更新.

## 更新与管理

在 `hy2` 目录更新代码, 不初始化授权状态:

```bash
python3 -B test_subscriptions.py
install -m 0644 subscriptions.py build-config.py /usr/local/lib/hysteria/
systemctl restart hy2-subscriptions
systemctl start hysteria
```

查看授权状态, 输出不包含密码和 token:

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

需要本地文件时导出当前内网快照. token 到期后快照不会自动更新:

```bash
install -d -m 0700 /root/client-config-export
python3 -B /usr/local/lib/hysteria/build-config.py \
  /etc/hy2-subscriptions/config.yaml /root/client-config-export/clash.yaml
```

导出一个 large 或 small 时, 在命令末尾加 `--scope large-A` 或对应名称.

## 验收

```bash
systemctl is-active hysteria hy2-subscriptions caddy hy2-certificate.timer
systemctl is-enabled hysteria hy2-subscriptions caddy hy2-certificate.timer
tailscale serve status
ss -ltnup
```

确认服务正常, 监听范围与端口表一致. 配额统计和旧会话清理错误记录在 `journalctl -u hy2-subscriptions`, 不记录 token 和密码.

在客户端确认:

- 内网, large 和 small 订阅分别返回 21, 5, 1 个节点, 仅包含授权范围内的节点和凭据.
- 内外网 HY2 均正常校验证书并可访问小型网页, 永久 Tailnet 凭据不能从公网认证.
- 国内网站命中 `DIRECT`, 其余流量按所选 HY2 节点处理.
- 换代后旧订阅失效, 旧连接被清理, 累计额度不会被刷新或重启清零.

证书续期演练不替换生产证书:

```bash
/data/network/certbot/bin/certbot renew --dry-run --reuse-key --cert-name hy2-ip \
  --no-random-sleep-on-renew --config-dir /data/network/acme/config \
  --work-dir /data/network/acme/work --logs-dir /data/network/acme/logs
```

## 官方资料

- [Hysteria2 认证和服务端配置](https://v2.hysteria.network/docs/advanced/Full-Server-Config/)
- [Hysteria2 统计与会话撤销](https://v2.hysteria.network/docs/advanced/Traffic-Stats-API/)
- [Hysteria2 ACL](https://v2.hysteria.network/docs/advanced/ACL/)
- [可信 IP 证书与 Certbot](https://letsencrypt.org/2026/03/11/shorter-certs-certbot)
- [Tailscale Serve](https://tailscale.com/docs/reference/tailscale-cli/serve)
