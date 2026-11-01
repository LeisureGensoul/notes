"""Build a Mihomo configuration from the scoped authorization state."""
import argparse
import ipaddress
import os
import sqlite3
import tempfile
from pathlib import Path

import yaml


def build_nodes(nodes, providers=None, devices=None, tailnet_interface='Tailscale'):
    """Build routing and DNS around inline weekly nodes and daily providers."""
    groups = []
    if providers and (len(providers) > 1 or any(n['name'] == 'tailscale' for n in nodes)):
        groups = [{'name': 'Large ' + name.rsplit('-', 1)[1], 'type': 'select',
                   'proxies': [provider['proxy']], 'use': [name]}
                  for name, provider in providers.items()]
        if any(n['name'] == 'tailscale' for n in nodes):
            groups.append({'name': 'Tailscale', 'type': 'select', 'proxies': ['tailscale']})
    overseas = {'name': 'Overseas', 'type': 'select',
                'proxies': [g['name'] for g in groups] if groups else [n['name'] for n in nodes]}
    if providers and not groups:
        overseas['use'] = list(providers)
    device_rules, hosts = [], {}
    # Preserve the physical default route for Tailscale's Windows transport.
    routes = ['0.0.0.0/1', '128.0.0.0/1', '::/1', '8000::/1']
    for device in (devices or {}).values():
        addresses = [ipaddress.ip_address(value) for value in device['addresses']]
        matches = ['DOMAIN,' + name for name in device['names']]
        for address in addresses:
            prefix = str(address) + ('/32' if address.version == 4 else '/128')
            routes.append(prefix)
            matches.append(('IP-CIDR,' if address.version == 4 else 'IP-CIDR6,') + prefix)
        for name in device['names']:
            hosts[name] = [str(address) for address in addresses]
        for match in matches:
            for port in device.get('direct_tcp_ports', []):
                device_rules.append(f'AND,((DST-PORT,{int(port)}),(NETWORK,TCP),({match})),tailnet-direct')
            device_rules.append(match + ',Overseas' + (',no-resolve' if match.startswith('IP-') else ''))
    # Keep the explicit host routes: route-exclude-address would merge overlapping ranges.
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
            'iproute2-rule-index': 5000,
            'dns-hijack': ['any:53', 'tcp://any:53'],
            'route-address': routes,
        },
        **({'hosts': hosts} if hosts else {}),
        'dns': {
            'enable': True,
            'ipv6': True,
            'enhanced-mode': 'fake-ip',
            'fake-ip-range': '198.18.0.1/16',
            'fake-ip-filter': ['*.lan', '*.local', '*.ts.net'],
            'default-nameserver': ['223.5.5.5'],
            'nameserver': ['https://1.1.1.1/dns-query#Overseas'],
            'nameserver-policy': {
                '+.lan': ['system'],
                '+.local': ['system'],
                '+.ts.net': ['100.100.100.100#tailnet-direct'],
                'rule-set:cn-domain': ['223.5.5.5'],
            },
        },
        'proxies': nodes + [{'name': 'tailnet-direct', 'type': 'direct',
                             'interface-name': tailnet_interface}],
        **({'proxy-providers': providers} if providers else {}),
        'proxy-groups': [overseas] + groups,
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
            'PROCESS-NAME,tailscale.exe,DIRECT',
            'PROCESS-NAME,tailscaled,DIRECT',
            *device_rules,
            'DOMAIN-SUFFIX,ts.net,tailnet-direct',
            'DOMAIN-SUFFIX,lan,DIRECT',
            'DOMAIN-SUFFIX,local,DIRECT',
            'IP-CIDR,100.64.0.0/10,tailnet-direct,no-resolve',
            'IP-CIDR6,fd7a:115c:a1e0::/48,tailnet-direct,no-resolve',
            'IP-CIDR,127.0.0.0/8,DIRECT,no-resolve',
            'IP-CIDR,10.0.0.0/8,DIRECT,no-resolve',
            'IP-CIDR,172.16.0.0/12,DIRECT,no-resolve',
            'IP-CIDR,192.168.0.0/16,DIRECT,no-resolve',
            'IP-CIDR6,::1/128,DIRECT,no-resolve',
            'IP-CIDR6,fc00::/7,DIRECT,no-resolve',
            'IP-CIDR6,fe80::/10,DIRECT,no-resolve',
            'RULE-SET,cn-domain,DIRECT',
            'DOMAIN-REGEX,.+,Overseas',
            'RULE-SET,cn-ip,DIRECT,no-resolve',
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
    parser.add_argument('--snapshot', action='store_true', help='Generate from a temporary copy without changing authorization state')
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text())
    with tempfile.TemporaryDirectory(prefix='hy2-export-') as staging:
        if args.snapshot:
            state = Path(staging) / 'state.sqlite3'
            with sqlite3.connect(Path(config['state']).as_uri() + '?mode=ro', uri=True) as source:
                with sqlite3.connect(state) as target:
                    source.backup(target)
            state.chmod(0o600)
            config['state'] = str(state)
        store = Store(config)
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
