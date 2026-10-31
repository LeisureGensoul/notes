"""Offline authorization, rollover, quota and subscription boundary checks."""
import ast
import contextlib
import datetime as dt
import importlib.util
import json
import re
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import urllib.error
import urllib.request

import yaml

spec = importlib.util.spec_from_file_location('subscriptions', Path(__file__).with_name('subscriptions.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class Subscriptions(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.now = dt.datetime(2026, 10, 26, 0, 0, tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp()
        self.config = {'state': str(Path(self.temporary.name) / 'state.sqlite3'),
                       'public_host': '203.0.113.5', 'tailnet_ip': '100.64.1.2',
                       'obfs_password': 'test-obfuscation', 'poll_seconds': 5,
                       'limits': {'large': 70_000_000_000, 'small': 2_000_000_000}}
        self.store = m.Store(self.config)
        self.store.initialize(self.now)

    def grants(self, now=None):
        return {g['name']: g for g in self.store.selected(None, self.now if now is None else now)}

    def sample(self, traffic=None, now=None, invocation='first'):
        self.store.sample(traffic or {}, invocation, self.now if now is None else now)

    def test_scopes_and_sharing_links(self):
        all_grants = self.grants()
        self.assertEqual(len(all_grants), 21)
        all_body = self.store.render(None, self.now)
        self.assertEqual(len(yaml.safe_load(all_body)['proxies']), 21)
        for letter in 'ABCD':
            parent = all_grants['large-' + letter]
            selected = self.store.selected(parent['token'], self.now)
            names = {g['name'] for g in selected}
            self.assertEqual(names, {'large-' + letter} | {'small-' + letter + str(n) for n in range(1, 5)})
            body = self.store.render(parent['token'], self.now)
            display_names = {g['name'] for g in selected}
            self.assertEqual({n['name'] for n in yaml.safe_load(body)['proxies']}, display_names)
            for name, grant in all_grants.items():
                if grant['token']:
                    self.assertEqual(grant['token'] in body, name in names)
            for child in selected:
                if child['tier'] == 'small':
                    body = self.store.render(child['token'], self.now)
                    self.assertEqual(len(yaml.safe_load(body)['proxies']), 1)
                    self.assertNotIn(parent['token'], body)
        with self.assertRaises(LookupError):
            self.store.selected('invalid', self.now)

    def test_stable_node_names_and_grouped_sharing_links(self):
        body = self.store.render(None, self.now)
        config = yaml.safe_load(body)
        self.assertEqual({n['name'] for n in config['proxies']}, set(self.grants()))
        for letter in 'ABCD':
            start = body.index('# Large ' + letter + '\n')
            end = body.index('# Large ' + chr(ord(letter) + 1) + '\n') if letter != 'D' else body.index('mixed-port:')
            block = body[start:end]
            self.assertIn('# large-' + letter + ' expires=', block)
            for number in range(1, 5):
                self.assertIn('\n\n#   small-' + letter + str(number) + ' expires=', block)
            self.assertEqual(block.count('https://'), 5)
        self.assertIn('\n\ndns:\n', body)
        self.assertIn('\n\nproxies:\n', body)
        next_day = self.now + 86400
        refreshed = yaml.safe_load(self.store.render(None, next_day))
        self.assertEqual([n['name'] for n in config['proxies']], [n['name'] for n in refreshed['proxies']])
        self.assertNotEqual(config['proxies'][-1]['password'], refreshed['proxies'][-1]['password'])
        for grant in self.grants(next_day).values():
            if grant['token']:
                public = yaml.safe_load(self.store.render(grant['token'], next_day))
                self.assertFalse(any(grant['token'] in n['name'] for n in public['proxies']))
                break

    def test_proxy_groups_follow_authorization_families(self):
        grants = self.grants()
        config = yaml.safe_load(self.store.render(None, self.now))
        groups = {g['name']: g['proxies'] for g in config['proxy-groups']}
        self.assertEqual(groups['Overseas'], ['Tailscale', 'Large A', 'Large B', 'Large C', 'Large D'])
        self.assertEqual(groups['Tailscale'], ['tailscale'])
        for letter in 'ABCD':
            family = 'Large ' + letter
            parent = grants['large-' + letter]
            scoped = yaml.safe_load(self.store.render(parent['token'], self.now))
            scoped_groups = {g['name']: g['proxies'] for g in scoped['proxy-groups']}
            self.assertEqual(set(scoped_groups), {'Overseas'})
            self.assertEqual(len(scoped_groups['Overseas']), 5)
            self.assertEqual(set(scoped_groups['Overseas']), set(groups[family]))
            child = grants['small-' + letter + '1']
            leaf = yaml.safe_load(self.store.render(child['token'], self.now))
            self.assertEqual(len(leaf['proxy-groups']), 1)
            self.assertEqual(leaf['proxy-groups'][0]['proxies'], [leaf['proxies'][0]['name']])
        names = {n['name'] for n in config['proxies']}
        for members in groups.values():
            self.assertTrue(set(members) <= names | set(groups))

    def test_refresh_never_extends_or_rotates_credentials(self):
        before = self.grants()
        for grant in before.values():
            if grant['token']:
                self.store.render(grant['token'], self.now + 60)
        self.assertEqual(before, self.grants(self.now + 60))

    def test_daily_expiry_preserves_large_and_invalidates_small(self):
        before = self.grants()
        next_day = self.now + 86400
        after = self.grants(next_day)
        self.assertEqual(before['tailscale'], after['tailscale'])
        self.assertEqual(before['large-A'], after['large-A'])
        self.assertNotEqual(before['small-A1']['token'], after['small-A1']['token'])
        with self.assertRaises(LookupError):
            self.store.selected(before['small-A1']['token'], next_day)
        fresh = self.store.selected(before['large-A']['token'], next_day)
        self.assertIn(after['small-A1']['token'], [g['token'] for g in fresh])
        old_ids = {g['identity'] for g in before.values() if g['tier'] == 'small'}
        self.sample(now=next_day)
        online = {g['identity']: 1 for g in before.values()}
        self.assertEqual(set(self.store.invalid_sessions(online, next_day)), old_ids)
        self.assertEqual(self.store.invalid_sessions({}, next_day), [])

    def test_weekly_expiry_cascades_once_without_overlap(self):
        before = self.grants()
        next_week = self.now + 7 * 86400
        after = self.grants(next_week)
        self.assertEqual(before['tailscale'], after['tailscale'])
        for name, grant in before.items():
            if grant['tier'] != 'tailscale':
                self.assertNotEqual(grant['identity'], after[name]['identity'])
                with self.assertRaises(LookupError):
                    self.store.selected(grant['token'], next_week)
        self.sample(now=next_week)
        old_ids = {g['identity'] for g in before.values() if g['tier'] != 'tailscale'}
        online = {identity: 1 for identity in old_ids}
        online[after['large-A']['identity']] = 1
        self.assertEqual(set(self.store.invalid_sessions(online, next_week)), old_ids)
        self.assertEqual(self.store.invalid_sessions({}, next_week), [])
        # A late old session is discovered directly from the online snapshot.
        self.assertEqual(self.store.invalid_sessions({before['large-A']['identity']: 1}, next_week), [before['large-A']['identity']])

    def test_concurrent_restore_preserves_complete_usage(self):
        before = self.grants()['small-A1']
        self.sample({before['identity']: {'tx': 100, 'rx': 0}})
        config_path = Path(self.temporary.name) / 'config.yaml'
        config_path.write_text(yaml.safe_dump(self.config))
        children = []
        test = self
        child_code = (
            "from pathlib import Path\n"
            "import runpy, sys, yaml\n"
            "store = runpy.run_path(sys.argv[1])['Store'](yaml.safe_load(Path(sys.argv[2]).read_text()))\n"
            "print('ready', flush=True)\n"
            "store.restore('small-A1', float(sys.argv[3]))\n"
        )

        class Interleaved(m.Store):
            fired = False

            @contextlib.contextmanager
            def database(self):
                with super().database() as db:
                    owner = self

                    class Proxy:
                        def execute(self, sql, args=()):
                            if sql.startswith('UPDATE grants SET used=used+?') and not owner.fired:
                                owner.fired = True
                                child = subprocess.Popen(
                                    [sys.executable, '-B', '-c', child_code,
                                     str(Path(m.__file__)), str(config_path), str(test.now)],
                                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                                children.append(child)
                                test.assertEqual(child.stdout.readline().strip(), 'ready')
                                try:
                                    child.wait(timeout=0.5)
                                except subprocess.TimeoutExpired:
                                    pass
                            return db.execute(sql, args)

                        def __getattr__(self, name):
                            return getattr(db, name)

                    yield Proxy()

        try:
            worker = Interleaved(self.config)
            worker.sample({before['identity']: {'tx': 200, 'rx': 0}}, 'first', self.now)
            self.assertEqual(len(children), 1)
            _, error = children[0].communicate(timeout=5)
            self.assertEqual(children[0].returncode, 0, error)
            after = self.grants()['small-A1']
            self.assertNotEqual(before['identity'], after['identity'])
            self.sample({after['identity']: {'tx': 300, 'rx': 0}})
            self.assertEqual(self.grants()['small-A1']['used'], 500)
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.communicate()

    def test_transaction_rolls_back_all_changes_on_failure(self):
        with self.assertRaises(RuntimeError):
            with self.store.database() as db:
                db.execute("UPDATE grants SET used=999 WHERE name='small-A1'")
                raise RuntimeError('fixture failure')
        self.assertEqual(self.grants()['small-A1']['used'], 0)

    def test_session_cleanup_retries_from_online_state(self):
        grant = self.grants()['small-A1']
        self.store.revoke(grant['name'], self.now)

        class Stop:
            rounds = 0

            def is_set(self):
                return self.rounds >= 2

            def wait(self, interval):
                self.rounds += 1

        class API:
            online = {grant['identity']: 1}

            def __init__(self):
                self.kicks = []

            def invocation(self):
                return 'fixture'

            def request(self, path, body=None):
                if path == '/traffic':
                    return {}
                if path == '/online':
                    return dict(self.online)
                if path == '/kick':
                    self.kicks.append(body)
                    if len(self.kicks) == 1:
                        raise OSError('fixture unavailable')
                    self.online.clear()

        api = API()
        with patch.object(m.time, 'time', return_value=self.now), self.assertLogs(level='ERROR'):
            m.maintain(self.store, api, Stop())
        self.assertEqual(api.kicks, [[grant['identity']], [grant['identity']]])
        self.assertEqual(self.store.invalid_sessions(api.online, self.now), [])

    def test_cleanup_continues_when_accounting_fails(self):
        grants = self.grants()
        revoked = grants['small-A1']
        public = grants['large-B']
        private = grants['tailscale']
        self.sample()
        self.store.revoke(revoked['name'], self.now)
        online = {g['identity']: 1 for g in (revoked, public, private)}

        class Stop:
            done = False

            def is_set(self):
                return self.done

            def wait(self, interval):
                self.done = True

        for failure in ('invocation', 'traffic', 'sample'):
            with self.subTest(failure=failure):
                class API:
                    kicks = []

                    def invocation(self):
                        if failure == 'invocation':
                            raise OSError('fixture unavailable')
                        return 'fixture'

                    def request(self, path, body=None):
                        if path == '/traffic':
                            if failure == 'traffic':
                                raise OSError('fixture unavailable')
                            return {'invalid': {'tx': -1, 'rx': 0}}
                        if path == '/online':
                            return online
                        if path == '/kick':
                            self.kicks.append(body)

                api = API()
                with patch.object(m.time, 'time', return_value=self.now + 16), self.assertLogs(level='ERROR'):
                    m.maintain(self.store, api, Stop())
                self.assertEqual(api.kicks, [sorted([revoked['identity'], public['identity']])])
                self.assertFalse(self.store.authenticate(public['password'], '198.51.100.4:1', self.now + 16)['ok'])
                self.assertTrue(self.store.authenticate(private['password'], '100.64.3.4:1', self.now + 16)['ok'])

    def test_deployment_placeholders_with_and_without_ipv6(self):
        document = Path(__file__).with_name('setup.md').read_text()
        script = document.split("python3 - <<'PY'\n", 1)[1].split('\nPY', 1)[0]
        loop = next(node for node in ast.parse(script).body
                    if isinstance(node, ast.For) and isinstance(node.target, ast.Name)
                    and node.target.id == 'source')
        start = next(i for i, node in enumerate(loop.body)
                     if isinstance(node, ast.Assign) and ast.unparse(node.targets[0]) == 'text')
        end = next(i for i, node in enumerate(loop.body)
                   if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call)
                   and ast.unparse(node.value.func) == 'source.write_text')
        code = compile(ast.Module(body=loop.body[start:end + 1], type_ignores=[]), '<deployment>', 'exec')
        template = Path(__file__).with_name('files') / 'etc/hysteria/config.yaml'
        for ipv6 in (None, '2001:db8::1'):
            with self.subTest(ipv6=ipv6):
                source = Path(self.temporary.name) / 'config.yaml'
                source.write_text(template.read_text())
                replacements = {'CHANGE_PUBLIC_IP': '203.0.113.5', 'CHANGE_TAILNET_IP': '100.64.1.2',
                                'CHANGE_GECKO_PASSWORD': 'fixture-obfs', 'CHANGE_STATS_SECRET': 'fixture-stats'}
                if ipv6:
                    replacements['CHANGE_PUBLIC_IPV6'] = ipv6
                exec(code, {'source': source, 'replacements': replacements, 'yaml': yaml, 're': re})
                text = source.read_text()
                acl = yaml.safe_load(text)['acl']['inline']
                self.assertNotIn('CHANGE_', text)
                self.assertIn('reject(203.0.113.5)', acl)
                self.assertNotIn('reject(203.0.113.5V6)', acl)
                if ipv6:
                    self.assertIn('reject(' + ipv6 + ')', acl)

    def test_session_cleanup_is_recomputed_after_restart(self):
        before = self.grants()
        self.store.revoke('large-A', self.now)
        restored = m.Store(self.config)
        restored.sample({}, 'fixture', self.now)
        family = {g['identity'] for g in before.values()
                  if g['name'] == 'large-A' or g['parent'] == 'large-A'}
        online = {identity: 1 for identity in family}
        online[before['large-B']['identity']] = 1
        self.assertEqual(set(restored.invalid_sessions(online, self.now)), family)
        self.assertEqual(restored.invalid_sessions({}, self.now), [])
        self.assertEqual(restored.invalid_sessions({'disconnected-fixture': 0}, self.now), [])

    def test_authentication_and_tailnet_source_restriction(self):
        grants = self.grants()
        private = grants['tailscale']
        self.sample()
        for address in ('100.64.3.4:1234', '[fd7a:115c:a1e0::2]:1234', '[::ffff:100.64.3.4]:1234'):
            self.assertTrue(self.store.authenticate(private['password'], address, self.now)['ok'])
        for address in ('198.51.100.4:1234', '127.0.0.1:1234', '[::1]:1234', '', 'invalid'):
            self.assertFalse(self.store.authenticate(private['password'], address, self.now)['ok'])
        public = grants['large-A']
        self.assertTrue(self.store.authenticate(public['password'], '198.51.100.4:1234', self.now)['ok'])
        self.assertFalse(self.store.authenticate(public['token'], '198.51.100.4:1234', self.now)['ok'])
        self.assertFalse(self.store.authenticate('bad', '198.51.100.4:1234', self.now)['ok'])

    def test_quota_is_persistent_and_not_reset_by_refresh_or_restart(self):
        grant = self.grants()['small-A1'];identity = grant['identity']
        self.sample({identity: {'tx': 500_000_000, 'rx': 500_000_000}})
        self.assertTrue(self.store.authenticate(grant['password'], '198.51.100.4:1', self.now)['ok'])
        self.store.render(grant['token'], self.now)
        self.sample({identity: {'tx': 500_000_000, 'rx': 500_000_000}})
        self.assertEqual(self.grants()['small-A1']['used'], 1_000_000_000)
        # Backend restart reads the same persistent database.
        self.store = m.Store(self.config)
        # HY2 process restart resets API counters, not the budget.
        self.sample({identity: {'tx': 600_000_000, 'rx': 500_000_000}}, invocation='second')
        self.assertEqual(self.grants()['small-A1']['used'], 2_100_000_000)
        self.assertFalse(self.store.authenticate(grant['password'], '198.51.100.4:1', self.now)['ok'])
        self.assertIn(identity, self.store.invalid_sessions({identity: 1}, self.now))
        self.assertEqual(self.grants(self.now + 86400)['small-A1']['used'], 0)

    def test_stale_accounting_denies_public_authentication(self):
        grant = self.grants()['large-A']
        self.assertFalse(self.store.authenticate(grant['password'], '198.51.100.4:1', self.now)['ok'])
        self.sample()
        self.assertTrue(self.store.authenticate(grant['password'], '198.51.100.4:1', self.now)['ok'])
        self.assertFalse(self.store.authenticate(grant['password'], '198.51.100.4:1', self.now + 16)['ok'])
        self.assertTrue(self.store.authenticate(self.grants()['tailscale']['password'], '100.64.1.3:1', self.now + 16)['ok'])

    def test_revoke_is_scoped_and_persistent_across_rollover(self):
        before = self.grants()
        self.store.revoke('large-A', self.now)
        self.assertEqual(len(self.grants()), 16)
        with self.assertRaises(LookupError):
            self.store.selected(before['large-A']['token'], self.now)
        with self.assertRaises(LookupError):
            self.store.selected(before['small-A1']['token'], self.now)
        self.assertEqual(len(self.store.selected(before['large-B']['token'], self.now)), 5)
        self.assertEqual(len(self.grants(self.now + 7 * 86400)), 16)

    def test_restore_reissues_credentials_without_resetting_budget(self):
        before = self.grants()['small-A1']
        self.sample({before['identity']: {'tx': 123, 'rx': 456}})
        self.store.revoke('small-A1', self.now)
        self.store.restore('small-A1', self.now)
        after = self.grants()['small-A1']
        self.assertNotEqual(before['token'], after['token'])
        self.assertNotEqual(before['password'], after['password'])
        self.assertEqual(after['used'], 579)
        with self.assertRaises(LookupError):
            self.store.selected(before['token'], self.now)

    def test_http_scope_and_no_cache_headers(self):
        now = m.time.time()
        grant = self.grants(now)['small-A1']
        server = m.Server(('127.0.0.1', 0), m.handler_for(self.store))
        worker = threading.Thread(target=server.serve_forever, daemon=True);worker.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        base = 'http://127.0.0.1:' + str(server.server_port)
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(base + '/s/' + grant['token'] + '/clash.yaml') as response:
            self.assertEqual(response.headers['Cache-Control'], 'no-store')
            self.assertEqual(len(yaml.safe_load(response.read())['proxies']), 1)
        for path in ('/s/../all.yaml', '/auth', '/s/invalid/clash.yaml'):
            with self.assertRaises(urllib.error.HTTPError) as error:
                opener.open(base + path)
            self.assertEqual(error.exception.code, 404)
        private = self.grants(now)['tailscale']
        request = urllib.request.Request(base + '/auth',
                 data=json.dumps({'auth': private['password'], 'addr': '100.64.1.3:123'}).encode())
        with opener.open(request) as response:
            self.assertTrue(json.load(response)['ok'])

    def test_output_keeps_shared_dns_rules_and_manual_tun(self):
        config = yaml.safe_load(self.store.render(None, self.now))
        self.assertNotIn('enable', config['tun'])
        self.assertEqual(config['dns']['nameserver-policy']['rule-set:cn-domain'], ['223.5.5.5'])
        self.assertEqual(config['rules'][-3:], ['RULE-SET,cn-domain,DIRECT', 'RULE-SET,cn-ip,DIRECT', 'MATCH,Overseas'])
        for node in config['proxies']:
            self.assertNotIn('skip-cert-verify', node)
            self.assertEqual('interface-name' in node, node['name'] == 'tailscale')


if __name__ == '__main__':
    unittest.main()
