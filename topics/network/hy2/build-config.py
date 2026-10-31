"""Build a Mihomo configuration from the scoped authorization state."""
import argparse
import os
import tempfile
from pathlib import Path

import yaml


def build_nodes(nodes, grants):
    """Build scoped proxy groups with shared routing and DNS configuration."""
    branches = {}
    if len(nodes) > 1:
        for node, grant in zip(nodes, grants):
            if grant['tier'] == 'tailscale':
                group = 'Tailscale'
            else:
                family = (grant['parent'] or grant['name']).rsplit('-', 1)[1]
                group = 'Large ' + family
            branches.setdefault(group, []).append(node['name'])
    groups = [{'name': group, 'type': 'select', 'proxies': members}
              for group, members in branches.items()] if len(branches) > 1 else []
    choices = list(branches) if groups else [node['name'] for node in nodes]
    return {
        'mixed-port': 7890,
        'allow-lan': False,
        'mode': 'rule',
        'log-level': 'info',
        'ipv6': True,
        'tun': {
            'stack': 'mixed',
            'auto-route': True,
            'auto-detect-interface': True,
            'dns-hijack': ['any:53'],
            'route-exclude-address': ['100.64.0.0/10', 'fd7a:115c:a1e0::/48'],
        },
        'dns': {
            'enable': True,
            'ipv6': True,
            'enhanced-mode': 'fake-ip',
            'fake-ip-range': '198.18.0.1/16',
            'fake-ip-filter': ['*.lan', '*.local', '*.ts.net'],
            'default-nameserver': ['223.5.5.5', '1.1.1.1'],
            'nameserver': ['https://1.1.1.1/dns-query#Overseas'],
            'proxy-server-nameserver': ['223.5.5.5'],
            'nameserver-policy': {
                '+.ts.net': ['100.100.100.100'],
                'rule-set:cn-domain': ['223.5.5.5'],
            },
        },
        'proxies': nodes,
        'proxy-groups': [{'name': 'Overseas', 'type': 'select', 'proxies': choices}] + groups,
        'rule-providers': {
            'cn-domain': {
                'type': 'http',
                'behavior': 'domain',
                'format': 'mrs',
                'url': 'https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geosite/cn.mrs',
                'interval': 86400,
                'proxy': 'Overseas',
            },
            'cn-ip': {
                'type': 'http',
                'behavior': 'ipcidr',
                'format': 'mrs',
                'url': 'https://raw.githubusercontent.com/MetaCubeX/meta-rules-dat/meta/geo/geoip/cn.mrs',
                'interval': 86400,
                'proxy': 'Overseas',
            },
        },
        'rules': [
            'PROCESS-NAME,tailscaled.exe,DIRECT',
            'PROCESS-NAME,tailscale.exe,DIRECT',
            'PROCESS-NAME,tailscaled,DIRECT',
            'DOMAIN-SUFFIX,ts.net,DIRECT',
            'DOMAIN-SUFFIX,lan,DIRECT',
            'DOMAIN-SUFFIX,local,DIRECT',
            'IP-CIDR,100.64.0.0/10,DIRECT,no-resolve',
            'IP-CIDR6,fd7a:115c:a1e0::/48,DIRECT,no-resolve',
            'IP-CIDR,127.0.0.0/8,DIRECT,no-resolve',
            'IP-CIDR,10.0.0.0/8,DIRECT,no-resolve',
            'IP-CIDR,172.16.0.0/12,DIRECT,no-resolve',
            'IP-CIDR,192.168.0.0/16,DIRECT,no-resolve',
            'IP-CIDR6,::1/128,DIRECT,no-resolve',
            'IP-CIDR6,fc00::/7,DIRECT,no-resolve',
            'IP-CIDR6,fe80::/10,DIRECT,no-resolve',
            'RULE-SET,cn-domain,DIRECT',
            'RULE-SET,cn-ip,DIRECT',
            'MATCH,Overseas',
        ],
    }


def main():
    import time
    from subscriptions import Store

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('config', type=Path)
    parser.add_argument('output', type=Path)
    parser.add_argument('--scope', help='Export one large or small grant instead of the Tailnet scope')
    args = parser.parse_args()
    store = Store(yaml.safe_load(args.config.read_text()))
    now = time.time()
    token = None
    if args.scope:
        grant = next((g for g in store.selected(None, now) if g['name'] == args.scope and g['token']), None)
        if grant is None:
            parser.error('Scope must name an enabled large or small grant')
        token = grant['token']
    body = store.render(token, now)
    fd, temporary = tempfile.mkstemp(dir=args.output.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            stream.write(body)
        os.replace(temporary, args.output)
    finally:
        Path(temporary).unlink(missing_ok=True)


if __name__ == '__main__':
    main()
