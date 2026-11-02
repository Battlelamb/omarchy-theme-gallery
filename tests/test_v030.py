# SPDX-License-Identifier: GPL-3.0-only
import json
import subprocess
import unittest
import test_backend

class FeatureTests(unittest.TestCase):
    setUp = test_backend.BackendTests.setUp
    tearDown = test_backend.BackendTests.tearDown
    theme = test_backend.BackendTests.theme
    image = test_backend.BackendTests.image
    shim = test_backend.BackendTests.shim
    current_theme = test_backend.BackendTests.current_theme
    theme_setter = test_backend.BackendTests.theme_setter

    def cli(self, *args):
        return subprocess.run(['/usr/bin/python3', str(test_backend.BACKEND), *args], capture_output=True, text=True, timeout=8, cwd='/')

    def test_preferences_cli_strict_patch_read_and_reset(self):
        result = self.cli('preferences')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(json.loads(result.stdout)['preferences']['version'], 1)
        for args in [('catalog','--apply'), ('catalog','--id','x'), ('catalog','--payload','{}'), ('preferences','--id','x'), ('preferences','--payload','{}'), ('preferences','--apply'), ('action','--id','theme:x','--verb','favorite','--apply','--payload','{}')]:
            self.assertNotEqual(self.cli(*args).returncode, 0, args)
        for raw in ('[]','{"options":{},"options":{}}','{"options":{"appearance":NaN}}','{"reset":true,"options":{}}','{"version":1}','{"options":{"unknown":true}}','{"options":{"appearance":"bogus"}}','{"options":{"appearance":"'+'界'*6000+'"}}'):
            result = self.cli('preferences','--apply','--payload',raw)
            self.assertNotEqual(result.returncode, 0, raw[:60])
            self.assertFalse(json.loads(result.stdout)['ok'])
        result = self.cli('preferences','--apply','--payload','{"options":{"appearance":"dark"}}')
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual(json.loads(result.stdout)['preferences']['options']['appearance'], 'dark')
        result = self.cli('preferences','--apply','--payload','{"reset":true}')
        self.assertEqual(json.loads(result.stdout)['preferences']['options']['appearance'], 'all')

    def test_swatches_and_light_dark_filter_use_background_not_accent(self):
        light=self.theme('sun');dark=self.theme('night');self.theme('unknown')
        (light/'colors.toml').write_text('background = "#FFFFFF"\nforeground = "#000000"\naccent = "#123456"\ncolor1 = "#ff0000"\n')
        (dark/'colors.toml').write_text('background = "#000000"\nforeground = "#FFFFFF"\naccent = "#ABCDEF"\n')
        rows={r['id']:r for r in self.b.catalog()['rows']}
        self.assertEqual(rows['theme:sun'].get('category'), 'light')
        self.assertEqual(rows['theme:night'].get('category'), 'dark')
        self.assertEqual(rows['theme:unknown'].get('category'), 'unknown')
        self.assertEqual(rows['theme:sun'].get('swatches'), ['#FFFFFF','#000000','#123456','#FF0000'])
        self.b.preferences(True, {'options':{'appearance':'dark'}})
        self.assertEqual([r['id'] for r in self.b.catalog()['rows']], ['theme:night'])
        self.b.action('theme:sun','favorite',True)
        self.b.preferences(True, {'options':{'appearance':'all','favoritesOnly':True}})
        self.assertEqual([r['id'] for r in self.b.catalog()['rows']], ['theme:sun'])

    def test_capture_installed_theme_local_wallpaper_pair_is_persistent_and_safe(self):
        self.theme('night');wall=self.image()
        result=self.b.action('theme:night','capture',True,{'name':'Reading','wallpaper':str(wall)})
        self.assertTrue(result['ok'],result)
        row=next(r for r in self.b.catalog()['rows'] if r['id'].startswith('pair:'))
        self.assertEqual(row['label'],'Reading');self.assertEqual(row['preview'],str(wall))
        self.assertEqual(row['collection'],'Saved combinations')
        self.assertIn('capture',self.b.catalog()['actions'])
        outside=self.home/'outside.png';outside.write_bytes(wall.read_bytes())
        link=wall.parent/'link.png';link.symlink_to(wall)
        for payload in ({'name':'No','wallpaper':str(outside)}, {'name':'No','wallpaper':str(link)}, {'name':'No','wallpaper':str(wall),'exec':'bad'}, {'name':'Reading','wallpaper':str(wall)}, {'name':'bad\nname','wallpaper':str(wall)}):
            self.assertFalse(self.b.action('theme:night','capture',True,payload)['ok'],payload)
        self.assertFalse((self.home/'calls').exists())
        self.assertEqual(len([r for r in self.b.catalog()['rows'] if r['id'].startswith('pair:')]),1)

    def wallpaper_setters(self):
        self.shim('omarchy-theme-bg-set', "p=h/'.local/state/omarchy/current/background'\np.parent.mkdir(parents=True,exist_ok=True)\np.unlink(missing_ok=True);p.symlink_to(sys.argv[1])\n")
        self.shim('omarchy-shell', "if sys.argv[1:]==['shell','ping']: print('ok')\nelif sys.argv[1:3]!=['background','set']: sys.exit(1)\n")

    def test_pair_apply_and_compensating_restore_freeze_wallpaper_bytes(self):
        self.theme('before');self.theme('night');self.current_theme('before');self.theme_setter();self.wallpaper_setters()
        old=self.image('old.png',(0,0,255));wall=self.image()
        link=self.home/'.local/state/omarchy/current/background';link.symlink_to(old)
        self.assertTrue(self.b.action('theme:night','capture',True,{'name':'Reading','wallpaper':str(wall)})['ok'])
        selected=next(r['id'] for r in self.b.catalog()['rows'] if r['id'].startswith('pair:'))
        result=self.b.action(selected,'apply',True)
        self.assertTrue(result['ok'],result)
        frozen=self.b.current_background();self.assertNotEqual(frozen,str(wall))
        self.assertEqual(self.b.read(test_backend.Path(frozen)),wall.read_bytes())
        self.assertEqual(self.b.current_theme()[0],'night')
        self.assertEqual(result['preferences']['recent'][0],selected)
        restored=self.b.action('restore:last','restore',True)
        self.assertTrue(restored['ok'],restored)
        self.assertEqual(self.b.current_theme()[0],'before')
        self.assertEqual(self.b.read(test_backend.Path(self.b.current_background())),old.read_bytes())
        self.assertFalse(self.b.journal())
        wall.write_bytes(test_backend.png((0,255,0)))
        self.assertFalse(self.b.action(selected,'apply',True)['ok'])
        self.assertEqual(self.b.current_theme()[0],'before')

    def test_pair_restore_after_unacknowledged_wallpaper_dispatch(self):
        self.theme('before');self.theme('night');self.current_theme('before');self.theme_setter();self.wallpaper_setters()
        old=self.image('old.png',(0,0,255));wall=self.image()
        (self.home/'.local/state/omarchy/current/background').symlink_to(old)
        self.assertTrue(self.b.action('theme:night','capture',True,{'name':'Partial','wallpaper':str(wall)})['ok'])
        selected=next(r['id'] for r in self.b.catalog()['rows'] if r['id'].startswith('pair:'))
        self.shim('omarchy-shell','sys.exit(3)\n')
        failed=self.b.action(selected,'apply',True)
        self.assertFalse(failed['ok']);self.assertEqual(self.b.current_theme()[0],'night')
        self.assertNotIn(selected,self.b.preferences()['preferences']['recent'])
        self.wallpaper_setters()
        result=self.b.action('restore:last','restore',True)
        self.assertTrue(result['ok'],result)
        self.assertEqual(self.b.current_theme()[0],'before')
        self.assertEqual(self.b.read(test_backend.Path(self.b.current_background())),old.read_bytes())

    def test_pair_provider_budgets_leave_time_for_recent_preference_lock(self):
        self.theme('before');self.theme('night');self.current_theme('before');self.theme_setter();self.wallpaper_setters()
        wall=self.image();(self.home/'.local/state/omarchy/current/background').symlink_to(wall)
        self.b.action('theme:night','capture',True,{'name':'Budget'})
        selected=next(r['id'] for r in self.b.catalog()['rows'] if r['id'].startswith('pair:'))
        actual=self.b.run;budgets=[]
        def capture(argv,**kwargs): budgets.append(kwargs['timeout']);return actual(argv,**kwargs)
        with test_backend.patch.object(self.b,'run',capture): self.assertTrue(self.b.action(selected,'apply',True)['ok'])
        self.assertLessEqual(sum(budgets),25)

    def test_documented_cli_pair_capture_apply_restore_roundtrip(self):
        self.theme('before',True);self.theme('night',True);self.current_theme('before');self.theme_setter();self.wallpaper_setters()
        wall=self.image();(self.home/'.local/state/omarchy/current/background').symlink_to(wall)
        result=self.cli('action','--id','theme:night','--verb','capture','--payload',json.dumps({'name':'CLI','wallpaper':str(wall)}),'--apply')
        self.assertEqual(result.returncode,0,result.stdout)
        rows=json.loads(self.cli('catalog').stdout)['rows'];selected=next(r['id'] for r in rows if r['id'].startswith('pair:'))
        for verb in ('favorite','apply'):
            result=self.cli('action','--id',selected,'--verb',verb,'--apply');self.assertEqual(result.returncode,0,result.stdout)
        result=self.cli('action','--id','restore:last','--verb','restore','--apply');self.assertEqual(result.returncode,0,result.stdout)
        self.assertEqual(self.b.current_theme()[0],'before')
        self.assertEqual(json.loads(self.cli('preferences').stdout)['preferences']['recent'][0],selected)

    def test_default_config_existing_omarchy_directory_without_preferences_remains_usable(self):
        self.theme('night',True)
        root=self.home/'.config/omarchy';root.chmod(0o755)
        with test_backend.patch.dict(test_backend.os.environ):
            test_backend.os.environ.pop('XDG_CONFIG_HOME',None)
            result=self.cli('catalog')
        self.assertEqual(result.returncode,0,result.stdout)
        self.assertIn('theme:night',[r['id'] for r in json.loads(result.stdout)['rows']])
        self.assertFalse((root/'lego').exists())
        self.assertEqual(root.stat().st_mode&0o777,0o755)

    def test_capture_uses_action_lock_to_serialize_collection_read_modify_write(self):
        self.theme('night');wall=self.image()
        with self.b.State():
            result=self.b.action('theme:night','capture',True,{'name':'Busy','wallpaper':str(wall)})
        self.assertFalse(result['ok'],result)
        self.assertEqual(self.b.preferences()['preferences']['collections'],{})

    def test_pair_restore_refuses_external_wallpaper_and_snapshot_edits(self):
        self.theme('before');self.theme('night');self.current_theme('before');self.theme_setter();self.wallpaper_setters()
        wall=self.image();link=self.home/'.local/state/omarchy/current/background';link.symlink_to(wall)
        self.b.action('theme:night','capture',True,{'name':'Guard'})
        selected=next(r['id'] for r in self.b.catalog()['rows'] if r['id'].startswith('pair:'))
        self.assertTrue(self.b.action(selected,'apply',True)['ok']);frozen=self.b.current_background()
        link.unlink();link.symlink_to(wall)
        self.assertFalse(self.b.action('restore:last','restore',True)['ok'])
        link.unlink();link.symlink_to(frozen);test_backend.Path(frozen).write_bytes(test_backend.png((0,255,0)))
        self.assertFalse(self.b.action('restore:last','restore',True)['ok'])
        self.assertEqual(self.b.current_theme()[0],'night')

    def test_favorite_round_trip_requires_fresh_membership(self):
        self.theme('night')
        result = self.b.action('theme:night', 'favorite', True)
        self.assertTrue(result['ok'], result)
        self.assertEqual(result['preferences']['favorites'], ['theme:night'])
        self.assertTrue(self.b.catalog()['rows'][0]['favorite'])
        self.assertFalse(self.b.action('theme:missing', 'favorite', True)['ok'])
        self.assertFalse(self.b.action('theme:night', 'favorite')['ok'])
        self.assertEqual(self.b.action('theme:night', 'favorite', True)['preferences']['favorites'], [])
