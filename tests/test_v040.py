# SPDX-License-Identifier: GPL-3.0-only
import json
import subprocess
import unittest
import test_backend


class DisplayOptionsTests(unittest.TestCase):
    setUp = test_backend.BackendTests.setUp
    tearDown = test_backend.BackendTests.tearDown

    theme = test_backend.BackendTests.theme
    image = test_backend.BackendTests.image

    def test_theme_source_uses_admitted_user_precedence_for_themes_and_pairs_before_cap(self):
        self.assertEqual(self.b.PREFERENCE_SCHEMA.get('themeSource', {}).get('choices'), ['all', 'user', 'stock'])
        self.assertEqual(self.b.preferences()['preferences']['options']['themeSource'], 'all')
        self.theme('a-stock')
        self.theme('b-overlay'); overlay = self.theme('b-overlay', True)
        self.theme('z-user', True)
        self.theme('blocked')
        (self.home/'.config/omarchy/themes/blocked').symlink_to(self.b.STOCK/'blocked', target_is_directory=True)
        (overlay/'colors.toml').write_text('background = "#000000"\naccent = "#ABCDEF"\n')
        wall = self.image()
        self.assertTrue(self.b.action('theme:b-overlay', 'capture', True, {'name': 'Reading', 'wallpaper': str(wall)})['ok'])
        self.b.preferences(True, {'options': {'themeSource': 'user'}})
        rows = self.b.catalog()['rows']
        self.assertEqual({row['id'] for row in rows if row['id'].startswith('theme:')}, {'theme:b-overlay', 'theme:z-user'})
        pair = next(row for row in rows if row['id'].startswith('pair:'))
        self.assertEqual({row.get('source') for row in rows}, {'user'})
        self.b.preferences(True, {'options': {'themeSource': 'stock'}})
        self.assertEqual([row['id'] for row in self.b.catalog()['rows']], ['theme:a-stock'])
        self.assertEqual(self.b.catalog()['rows'][0]['source'], 'stock')
        self.b.preferences(True, {'options': {'themeSource': 'user'}})
        with test_backend.patch.object(self.b, 'MAX_ROWS', 1):
            self.assertEqual([row['id'] for row in self.b.catalog()['rows']], ['theme:b-overlay'])
        self.assertTrue(self.b.action(pair['id'], 'favorite', True)['ok'])
        self.b.preferences(True, {'options': {'favoritesOnly': True, 'appearance': 'dark'}})
        self.assertEqual([row['id'] for row in self.b.catalog()['rows']], [pair['id']])
        self.b.preferences(True, {'options': {'themeSource': 'stock'}})
        self.assertEqual(self.b.catalog()['rows'], [])
        self.assertTrue(self.b.action('theme:z-user', 'favorite', True)['ok'])
        self.assertFalse(self.b.action('theme:blocked', 'favorite', True)['ok'])
        before = self.b.preferences()['preferences']
        for bad in (None, True, 1, [], {}, 'USER', 'remote'):
            with self.assertRaises(ValueError): self.b.preferences(True, {'options': {'themeSource': bad}})
            self.assertEqual(self.b.preferences()['preferences'], before)
        self.assertFalse((self.home/'calls').exists())
        self.assertFalse(self.b.state_path().joinpath('restore.json').exists())
        self.assertEqual(self.b.preferences(True, {'reset': True})['preferences']['options']['themeSource'], 'all')

    shim = test_backend.BackendTests.shim
    current_theme = test_backend.BackendTests.current_theme
    theme_setter = test_backend.BackendTests.theme_setter

    def test_source_filter_does_not_remove_restore_or_fresh_hidden_apply(self):
        self.theme('before'); self.theme('user', True)
        self.current_theme('before'); self.theme_setter()
        self.b.preferences(True, {'options': {'themeSource': 'stock'}})
        self.assertNotIn('theme:user', [r['id'] for r in self.b.catalog()['rows']])
        result = self.b.action('theme:user', 'apply', True)
        self.assertTrue(result['ok'], result)
        self.b.preferences(True, {'options': {'themeSource': 'user', 'favoritesOnly': True}})
        value = self.b.catalog()
        self.assertEqual(value['rows'], [])
        self.assertIn('restore', value['actions'])
        result = self.b.action('restore:last', 'restore', True)
        self.assertTrue(result['ok'], result)
        self.assertEqual(self.b.current_theme()[0], 'before')
        (self.home/'.config/omarchy/themes/user/colors.toml').unlink()
        (self.home/'.config/omarchy/themes/user').rmdir()
        self.assertFalse(self.b.action('theme:user', 'apply', True)['ok'])

    def test_legacy_options_gain_defaults_without_read_time_write(self):
        value = self.b.preferences()['preferences']
        for key in ('density', 'layout', 'sortOrder', 'themeSource', 'fileType'):
            value['options'].pop(key, None)
        store = self.home/'preferences-config/omarchy/lego'/self.b.PLUGIN/'preferences.json'
        store.parent.mkdir(parents=True)
        store.parent.parent.chmod(0o700); store.parent.chmod(0o700)
        raw = json.dumps(value).encode(); store.write_bytes(raw); store.chmod(0o600)
        defaults = self.b.preferences()['preferences']['options']
        self.assertEqual(defaults['density'], 'comfortable')
        self.assertEqual(defaults['layout'], 'grid')
        self.assertEqual(defaults['sortOrder'], 'default')
        if self.b.PLUGIN == 'theme-gallery': self.assertEqual(defaults['themeSource'], 'all')
        if self.b.PLUGIN == 'wallpaper-browser': self.assertEqual(defaults['fileType'], 'all')
        self.assertEqual(store.read_bytes(), raw)

    def test_common_display_schema_roundtrip_rejection_is_atomic_and_reset(self):
        expected = {
            'density': ('comfortable', ['comfortable', 'compact']),
            'layout': ('grid', ['grid', 'list']),
            'sortOrder': ('default', ['default', 'name', 'favorites-first']),
        }
        initial = self.b.preferences()
        for key, (default, choices) in expected.items():
            self.assertIn(key, initial['settingsSchema'])
            rule = initial['settingsSchema'][key]
            self.assertEqual(rule['type'], 'enum')
            self.assertEqual(rule['default'], default)
            self.assertEqual(rule['choices'], choices)
            self.assertEqual(initial['preferences']['options'][key], default)
        options = {'density': 'compact', 'layout': 'list', 'sortOrder': 'favorites-first'}
        value = self.b.preferences(True, {'options': options})
        store = self.home/'preferences-config/omarchy/lego'/self.b.PLUGIN/'preferences.json'
        before = store.read_bytes()
        for key in options:
            for bad in (None, True, 1, [], {}, 'invalid', '界'*6000):
                with self.assertRaises(ValueError):
                    self.b.preferences(True, {'options': {key: bad}})
                self.assertEqual(store.read_bytes(), before)
        result = subprocess.run(['/usr/bin/python3', str(test_backend.BACKEND), 'preferences'], capture_output=True, text=True, timeout=8)
        self.assertEqual(result.returncode, 0, result.stdout)
        saved = json.loads(result.stdout)['preferences']['options']
        self.assertEqual({key: saved[key] for key in options}, options)
        reset = self.b.preferences(True, {'reset': True})['preferences']['options']
        for key, (default, _) in expected.items():
            self.assertEqual(reset[key], default)
