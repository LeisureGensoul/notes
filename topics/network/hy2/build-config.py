"""Build a Mihomo configuration from the Hysteria2 server configuration."""
import argparse
import os
import tempfile
from pathlib import Path

import yaml


def build(server):
    address, port = server['listen'].rsplit(':', 1)
    node = {
        'name': 'Singapore',
        'type': 'hysteria2',
        'interface-name': 'Tailscale',
        'server': address.strip('[]'),
        'port': int(port),
        'password': server['auth']['password'],
        'skip-cert-verify': True,
        'obfs': server['obfs']['type'],
        'obfs-password': server['obfs']['gecko']['password'],
    }
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
        'proxies': [node],
        'proxy-groups': [{'name': 'Overseas', 'type': 'select', 'proxies': [node['name']]}],
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('server_config', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    config = build(yaml.safe_load(args.server_config.read_text()))
    # Replace the complete file atomically while subscription readers use it.
    fd, temporary = tempfile.mkstemp(dir=args.output.parent)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            yaml.safe_dump(config, stream, sort_keys=False, allow_unicode=True)
        os.replace(temporary, args.output)
    finally:
        Path(temporary).unlink(missing_ok=True)


if __name__ == '__main__':
    main()
