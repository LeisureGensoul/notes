"""Scoped HY2 subscriptions, expiring credentials and persistent traffic budgets."""
import argparse
import contextlib
import datetime as dt
import hmac
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import runpy
import secrets
import sqlite3
import subprocess
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from zoneinfo import ZoneInfo

import yaml

build_nodes = runpy.run_path(str(Path(__file__).with_name('build-config.py')))['build_nodes']
TAILNETS = (ipaddress.ip_network('100.64.0.0/10'),
            ipaddress.ip_network('fd7a:115c:a1e0::/48'))


def tailnet_address(address):
    try:
        host = address[1:address.index(']')] if address.startswith('[') else address.rsplit(':', 1)[0]
        ip = ipaddress.ip_address(host)
        ip = getattr(ip, 'ipv4_mapped', None) or ip
        return any(ip in network for network in TAILNETS)
    except (ValueError, IndexError):
        return False


class Store:
    def __init__(self, config):
        self.config = config
        self.zone = ZoneInfo(config.get('timezone', 'Asia/Singapore'))
        self.path = Path(config['state'])

    @contextlib.contextmanager
    def database(self):
        if not self.path.exists():
            raise ValueError('Initialize the state before starting')
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA secure_delete=ON')
            with db:
                # Serialize the complete read-modify-write operation across processes.
                db.execute('BEGIN IMMEDIATE')
                yield db
        finally:
            db.close()

    def initialize(self, now):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.exists():
            raise ValueError('State already exists')
        fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
        with self.database() as db:
            db.execute('''
                CREATE TABLE grants (
                    name TEXT PRIMARY KEY, tier TEXT NOT NULL, parent TEXT,
                    epoch INTEGER NOT NULL, expires INTEGER NOT NULL,
                    identity TEXT NOT NULL, token TEXT NOT NULL,
                    password TEXT NOT NULL, used INTEGER NOT NULL DEFAULT 0,
                    counter INTEGER NOT NULL DEFAULT 0,
                    enabled INTEGER NOT NULL DEFAULT 1
                )
            ''')
            db.execute('CREATE TABLE meta (name TEXT PRIMARY KEY, value TEXT NOT NULL)')
            self._issue(db, 'tailscale', 'tailscale', None, 0, 0)
            self._rotate(db, now)

    def window(self, tier, now):
        day = dt.datetime.fromtimestamp(now, self.zone).replace(hour=0, minute=0, second=0, microsecond=0)
        if tier == 'large':
            day -= dt.timedelta(days=day.weekday())
        end = day + dt.timedelta(days=7 if tier == 'large' else 1)
        return int(day.timestamp()), int(end.timestamp())

    def _issue(self, db, name, tier, parent, epoch, expires):
        old = db.execute('SELECT * FROM grants WHERE name=?', (name,)).fetchone()
        used = old['used'] if old and old['epoch'] == epoch else 0
        enabled = old['enabled'] if old else 1
        identity = name + ':' + secrets.token_hex(12)
        db.execute('INSERT OR REPLACE INTO grants VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                   (name, tier, parent, epoch, expires, identity,
                    '' if tier == 'tailscale' else secrets.token_urlsafe(32),
                    secrets.token_urlsafe(32), used, 0, enabled))

    def _rotate(self, db, now):
        latest = db.execute('SELECT MAX(epoch) FROM grants').fetchone()[0]
        if latest is not None and now < latest:
            raise ValueError('Clock precedes the current credential period')
        for letter in 'ABCD':
            name = 'large-' + letter
            epoch, expires = self.window('large', now)
            parent = db.execute('SELECT * FROM grants WHERE name=?', (name,)).fetchone()
            changed = not parent or parent['epoch'] != epoch
            if changed:
                self._issue(db, name, 'large', None, epoch, expires)
            for number in range(1, 5):
                child = 'small-' + letter + str(number)
                child_epoch, child_end = self.window('small', now)
                old = db.execute('SELECT epoch FROM grants WHERE name=?', (child,)).fetchone()
                if changed or not old or old['epoch'] != child_epoch:
                    self._issue(db, child, 'small', name, child_epoch, min(expires, child_end))

    def _available(self, db, grant, now):
        if not grant['enabled'] or (grant['expires'] and now >= grant['expires']):
            return False
        if grant['parent']:
            parent = db.execute('SELECT * FROM grants WHERE name=?', (grant['parent'],)).fetchone()
            return bool(parent and parent['enabled'] and now < parent['expires'])
        return True

    def quota(self, grant):
        return int(self.config.get('limits', {}).get(grant['tier'], 0))

    def _within_budget(self, db, grant, now):
        quota = self.quota(grant)
        if quota and grant['used'] >= quota:
            return False
        if quota:
            sample = db.execute("SELECT value FROM meta WHERE name='sample_time'").fetchone()
            if not sample or now - float(sample[0]) > 3 * self.config.get('poll_seconds', 5):
                return False
        return True

    def authenticate(self, password, address, now):
        with self.database() as db:
            self._rotate(db, now)
            for grant in db.execute('SELECT * FROM grants').fetchall():
                if not isinstance(password, str) or not hmac.compare_digest(grant['password'], password):
                    continue
                allowed = self._available(db, grant, now) and self._within_budget(db, grant, now)
                if grant['tier'] == 'tailscale':
                    allowed = allowed and tailnet_address(address)
                return {'ok': bool(allowed), 'id': grant['identity'] if allowed else ''}
        return {'ok': False, 'id': ''}

    def selected(self, token, now):
        with self.database() as db:
            self._rotate(db, now)
            grants = db.execute('SELECT * FROM grants ORDER BY CASE tier WHEN \'tailscale\' THEN 0 WHEN \'large\' THEN 1 ELSE 2 END,name').fetchall()
            if token is None:
                return [dict(g) for g in grants if self._available(db, g, now)]
            selected = next((g for g in grants if g['token'] and hmac.compare_digest(g['token'], token)), None)
            if not selected or not self._available(db, selected, now):
                raise LookupError('Subscription not found')
            return [dict(g) for g in grants if self._available(db, g, now)
                    and (g['name'] == selected['name'] or g['parent'] == selected['name'])]

    def render(self, token, now):
        grants = self.selected(token, now)
        host = self.config['public_host']
        nodes = []
        for g in grants:
            node = {'name': g['name'], 'type': 'hysteria2',
                    'server': self.config['tailnet_ip'] if g['tier'] == 'tailscale' else host,
                    'port': int(self.config.get('port', 8443)), 'password': g['password'],
                    'obfs': 'gecko', 'obfs-password': self.config['obfs_password']}
            if g['tier'] == 'tailscale':
                node['interface-name'] = 'Tailscale'
                node['sni'] = host
            nodes.append(node)
        comments, family = [], None
        for g in sorted((g for g in grants if g['token']),
                        key=lambda g: (g['parent'] or g['name'], g['name'])):
            group = g['parent'] or g['name']
            if group != family:
                comments.append('# Large ' + group.rsplit('-', 1)[1])
                family = group
            indent = '  ' if g['parent'] else ''
            expires = dt.datetime.fromtimestamp(g['expires'], self.zone).isoformat()
            comments.extend([
                f"# {indent}{g['name']} expires={expires} used={g['used']}/{self.quota(g)} B",
                f"# {indent}  https://{host}/s/{g['token']}/clash.yaml",
                '',
            ])
        body = yaml.safe_dump(build_nodes(nodes, grants), sort_keys=False, allow_unicode=True)
        body = re.sub(r'\n(?=[a-z][a-z-]*:\n)', '\n\n', body)
        return '\n'.join(comments) + '\n' + body

    def sample(self, traffic, invocation, now):
        if not isinstance(traffic, dict) or not invocation:
            raise ValueError('Invalid traffic sample')
        for value in traffic.values():
            if not isinstance(value, dict) or any(type(value.get(k)) is not int or value[k] < 0 for k in ('tx', 'rx')):
                raise ValueError('Invalid traffic counters')
        with self.database() as db:
            self._rotate(db, now)
            old = db.execute("SELECT value FROM meta WHERE name='invocation'").fetchone()
            restarted = not old or old[0] != invocation
            for grant in db.execute('SELECT * FROM grants').fetchall():
                value = traffic.get(grant['identity'])
                if value is None:
                    if restarted:
                        db.execute('UPDATE grants SET counter=0 WHERE name=?', (grant['name'],))
                    continue
                total = value['tx'] + value['rx']
                previous = 0 if restarted or total < grant['counter'] else grant['counter']
                db.execute('UPDATE grants SET used=used+?,counter=? WHERE name=?',
                           (total - previous, total, grant['name']))
            for key, value in (('invocation', invocation), ('sample_time', str(now))):
                db.execute('INSERT OR REPLACE INTO meta VALUES (?,?)', (key, value))

    def invalid_sessions(self, online, now):
        with self.database() as db:
            self._rotate(db, now)
            allowed = {g['identity'] for g in db.execute('SELECT * FROM grants').fetchall()
                       if self._available(db, g, now) and self._within_budget(db, g, now)}
            return sorted(identity for identity, count in online.items()
                          if count > 0 and identity not in allowed)

    def revoke(self, name, now):
        with self.database() as db:
            self._rotate(db, now)
            grants = db.execute('SELECT * FROM grants WHERE name=? OR parent=?', (name, name)).fetchall()
            if not grants:
                raise LookupError('Grant not found')
            for g in grants:
                db.execute('UPDATE grants SET enabled=0 WHERE name=?', (g['name'],))

    def restore(self, name, now):
        with self.database() as db:
            self._rotate(db, now)
            grants = db.execute('SELECT * FROM grants WHERE name=? OR parent=?', (name, name)).fetchall()
            if not grants:
                raise LookupError('Grant not found')
            for g in grants:
                self._issue(db, g['name'], g['tier'], g['parent'], g['epoch'], g['expires'])
                db.execute('UPDATE grants SET enabled=1 WHERE name=?', (g['name'],))

    def status(self, now):
        with self.database() as db:
            return [dict(row) for row in db.execute('SELECT name,tier,parent,expires,used,enabled FROM grants ORDER BY name')]


class API:
    def __init__(self, config):
        self.config = config
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, path, body=None):
        request = urllib.request.Request(self.config['stats_url'] + path,
                                         data=json.dumps(body).encode() if body is not None else None,
                                         headers={'Authorization': self.config['stats_secret'],
                                                  'Content-Type': 'application/json'})
        with self.opener.open(request, timeout=3) as response:
            payload = response.read(4 * 1024 * 1024 + 1)
            if len(payload) > 4 * 1024 * 1024:
                raise ValueError('Oversized management response')
            return json.loads(payload) if payload and path != '/kick' else None

    def invocation(self):
        return subprocess.check_output(['systemctl', 'show', 'hysteria', '-p', 'InvocationID', '--value'],
                                       text=True, timeout=3).strip()


def maintain(store, api, stop):
    interval = store.config.get('poll_seconds', 5)
    while not stop.is_set():
        try:
            invocation = api.invocation()
            traffic = api.request('/traffic')
            if invocation != api.invocation():
                raise RuntimeError('HY2 restarted during accounting')
            now = time.time()
            store.sample(traffic, invocation, now)
        except Exception:
            # Credentials and management responses must not enter logs.
            logging.error('HY2 accounting failed; will retry')
        try:
            online = api.request('/online')
            if not isinstance(online, dict):
                raise ValueError('Invalid online response')
            invalid = store.invalid_sessions(online, time.time())
            if invalid:
                api.request('/kick', invalid)
        except Exception:
            logging.error('HY2 session cleanup failed; will retry')
        stop.wait(interval)


def handler_for(store):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def send(self, status, payload=b'', content_type='text/plain; charset=utf-8'):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(payload)))
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Referrer-Policy', 'no-referrer')
            self.end_headers()
            if self.command != 'HEAD':
                self.wfile.write(payload)

        def do_GET(self):
            try:
                if self.path == '/all.yaml':
                    token = None
                else:
                    match = re.fullmatch(r'/s/([A-Za-z0-9_-]{43})/clash\.yaml', self.path)
                    if not match:
                        self.send(404)
                        return
                    token = match[1]
                body = store.render(token, time.time()).encode()
                self.send(200, body, 'application/yaml; charset=utf-8')
            except LookupError:
                self.send(404)
            except Exception:
                self.send(503)

        do_HEAD = do_GET

        def do_POST(self):
            if self.path != '/auth':
                self.send(404)
                return
            try:
                if self.headers.get('Transfer-Encoding'):
                    self.send(400)
                    return
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096:
                    self.send(400)
                    return
                data = json.loads(self.rfile.read(length))
                result = store.authenticate(data.get('auth'), data.get('addr', ''), time.time())
                self.send(200, json.dumps(result).encode(), 'application/json')
            except (ValueError, TypeError, AttributeError):
                self.send(400)
            except Exception:
                self.send(503)

        def setup(self):
            super().setup()
            self.connection.settimeout(5)

    return Handler


class Server(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 32

    def __init__(self, address, handler):
        self.slots = threading.BoundedSemaphore(32)
        super().__init__(address, handler)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()

    def handle_error(self, *_):
        logging.error('Subscription request failed')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=('init', 'serve', 'status', 'revoke', 'restore'))
    parser.add_argument('config', type=Path)
    parser.add_argument('name', nargs='?')
    args = parser.parse_args()
    config = yaml.safe_load(args.config.read_text())
    host = config['public_host']
    if not isinstance(host, str) or not re.fullmatch(r'[A-Za-z0-9.-]+', host) or 'CHANGE_' in host:
        raise SystemExit('Configure public_host before starting')
    if not isinstance(config['obfs_password'], str) or not config['obfs_password']:
        raise SystemExit('Configure obfs_password before starting')
    if any(type(v) is not int or v < 0 for v in config.get('limits', {}).values()):
        raise SystemExit('Limits must be non-negative integer byte counts')
    os.umask(0o077)
    store = Store(config)
    now = time.time()
    if args.action == 'init':
        store.initialize(now)
        print('Created 1 Tailnet, 4 large and 16 small grants without printing credentials.')
    elif args.action == 'status':
        print(json.dumps(store.status(now), indent=2))
    elif args.action in ('revoke', 'restore'):
        if not args.name:
            parser.error(args.action + ' requires a grant name')
        getattr(store, args.action)(args.name, now)
        print('Grant updated; session cleanup will run in the service.')
    else:
        server = Server(('127.0.0.1', 9091), handler_for(store))
        stop = threading.Event()
        threading.Thread(target=maintain, args=(store, API(config), stop), daemon=True).start()
        try:
            server.serve_forever()
        finally:
            stop.set()
            server.server_close()


if __name__ == '__main__':
    main()
