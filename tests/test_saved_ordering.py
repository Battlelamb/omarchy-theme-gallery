# SPDX-License-Identifier: GPL-3.0-only
import unittest
import test_backend


class SavedOrderingTests(unittest.TestCase):
    setUp = test_backend.BackendTests.setUp
    tearDown = test_backend.BackendTests.tearDown
    theme = test_backend.BackendTests.theme
    image = test_backend.BackendTests.image
    shim = test_backend.BackendTests.shim
    current_theme = test_backend.BackendTests.current_theme
    theme_setter = test_backend.BackendTests.theme_setter

    def test_favorite_beyond_real_cap_is_promoted_without_limiting_fresh_actions(self):
        for index in range(257): self.theme('z%03d' % index)
        expected = ['theme:z%03d' % index for index in range(257)]
        self.assertEqual(self.b.MAX_ROWS, 256)
        self.assertEqual([r['id'] for r in self.b.catalog()['rows']], expected[:256])
        self.assertTrue(self.b.action(expected[-1], 'favorite', True)['ok'])
        self.b.preferences(True, {'options': {'sortOrder': 'favorites-first'}})
        self.assertEqual([r['id'] for r in self.b.catalog()['rows']], [expected[-1]] + expected[:255])
        self.assertFalse((self.home/'calls').exists())
        self.current_theme('z000'); self.theme_setter()
        result = self.b.action(expected[255], 'apply', True)
        self.assertTrue(result['ok'], result)
        (self.b.STOCK/'z255/colors.toml').unlink(); (self.b.STOCK/'z255').rmdir()
        self.assertFalse(self.b.action(expected[255], 'favorite', True)['ok'])
        self.assertFalse(self.b.action(expected[255], 'apply', True)['ok'])

    def test_name_beyond_real_cap_is_sorted_before_truncation(self):
        for index in range(256): self.theme('z%03d' % index)
        wall = self.image()
        result = self.b.action('theme:z000', 'capture', True, {'name': 'aardvark', 'wallpaper': str(wall)})
        self.assertTrue(result['ok'], result)
        pair = next(iter(self.b.saved_pairs(self.b.preferences()['preferences'])))
        self.assertNotIn(pair, [r['id'] for r in self.b.catalog()['rows']])
        self.b.preferences(True, {'options': {'sortOrder': 'name'}})
        self.assertEqual([r['id'] for r in self.b.catalog()['rows']], [pair] + ['theme:z%03d' % index for index in range(255)])
        self.assertFalse((self.home/'calls').exists())

    def test_default_and_lowercase_ordinal_ties_preserve_original_order(self):
        self.theme('zulu'); wall = self.image()
        # Deliberately differ from locale/casefold order; supplementary UTF-16 sorts before BMP private use.
        labels = ['Zulu', 'zulu', 'ss', 'ß', '\ue000', '\U00010000']
        for label in labels:
            result = self.b.action('theme:zulu', 'capture', True, {'name': label, 'wallpaper': str(wall)})
            self.assertTrue(result['ok'], result)
        original = self.b.catalog()['rows']
        self.assertEqual([r['label'] for r in original], ['Zulu'] + labels)
        self.b.preferences(True, {'options': {'sortOrder': 'name'}})
        ordered = self.b.catalog()['rows']
        self.assertEqual([r['label'] for r in ordered], ['ss', 'Zulu', 'Zulu', 'zulu', 'ß', '\U00010000', '\ue000'])
        self.assertEqual([r['id'] for r in ordered[1:4]], [r['id'] for r in original[:3]])
        favorites = [original[2]['id'], original[0]['id']]
        self.b.preferences(True, {'favorites': favorites, 'options': {'sortOrder': 'favorites-first'}})
        self.assertEqual([r['id'] for r in self.b.catalog()['rows']], [original[0]['id'], original[2]['id']] + [r['id'] for r in original if r['id'] not in favorites])
        self.b.preferences(True, {'options': {'sortOrder': 'default'}})
        self.assertEqual([r['id'] for r in self.b.catalog()['rows']], [r['id'] for r in original])
        self.assertFalse((self.home/'calls').exists())
