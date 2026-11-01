# SPDX-License-Identifier: GPL-3.0-only
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import struct
import zlib

BACKEND = Path(__file__).resolve().parents[1] / "runtime/0.1.0/backend.py"
def png(rgb=(255,0,0)):
    def chunk(kind, data):
        return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',2,2,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+bytes(rgb)*2)*2))+chunk(b'IEND',b'')

class BackendTests(unittest.TestCase):
    def test_restore_last_survives_missing_applied_theme_row_but_rejects_other_id(self):
        self.theme('before');after=self.theme('after');self.current_theme('before');self.theme_setter()
        self.assertTrue(self.b.action('theme:after','apply',True)['ok'])
        (after/'colors.toml').unlink();after.rmdir()
        value=self.b.catalog();self.assertNotIn('theme:after',[r['id'] for r in value['rows']]);self.assertIn('restore',value['actions'])
        self.assertFalse(self.b.action('theme:before','restore',True)['ok'])
        restored=self.b.action('restore:last','restore',True);self.assertTrue(restored['ok'],restored)
        self.assertEqual(self.b.current_theme()[0],'before')

    def test_theme_provider_budget_fits_parent_30_second_watchdog(self):
        self.theme('before');self.theme('after');self.current_theme('before');self.theme_setter()
        actual=self.b.run;budgets=[]
        def capture(argv,**kwargs):
            budgets.append(kwargs['timeout']);return actual(argv,**kwargs)
        with patch.object(self.b,'run',capture):self.assertTrue(self.b.action('theme:after','apply',True)['ok'])
        self.assertEqual(budgets,[22])

    def test_deeply_nested_journal_fails_closed(self):
        with self.b.State() as state:state.put('restore.json',b'['*2000+b']'*2000)
        self.assertEqual(self.b.journal(),{})
    def test_empty_catalog_is_honest(self):
        result=self.b.catalog()
        self.assertEqual(result['rows'],[])
        self.assertIn('no ',result['message'].lower())
    def test_cli_real_action_and_restore_round_trip(self):
        if self.b.PLUGIN=='theme-gallery':
            self.theme('before',True);self.theme('after',True);self.current_theme('before');self.theme_setter()
            selected,verb='theme:after','apply'
        elif self.b.PLUGIN=='wallpaper-browser':
            old=self.image('old.png',(0,255,0));new=self.image();self.current_background(old);self.wallpaper_setters()
            selected=next(r['id'] for r in self.b.catalog()['rows'] if r['preview']==str(new));verb='apply'
        else:
            self.image();selected=self.b.catalog()['rows'][0]['id'];verb='export'
        def cli(v,confirm=True):
            args=['/usr/bin/python3',str(BACKEND),'action','--id',selected,'--verb',v]+(['--apply'] if confirm else [])
            r=subprocess.run(args,capture_output=True,text=True,timeout=10,cwd='/')
            self.assertNotIn('Traceback',r.stderr)
            return r,json.loads(r.stdout)
        r,value=cli(verb,False);self.assertNotEqual(r.returncode,0);self.assertFalse(value['ok'])
        r,value=cli(verb);self.assertEqual(r.returncode,0,value);self.assertTrue(value['ok'])
        r,value=cli('restore');self.assertEqual(r.returncode,0,value);self.assertTrue(value['ok'])
    def test_sandbox_has_no_live_ipc_home_or_network(self):
        self.assertFalse(Path('/srv/projects').exists());self.assertFalse(Path('/home/leviathan').exists())
        self.assertFalse(Path('/run/user').exists())
        interfaces=[line.split(':')[0].strip() for line in Path('/proc/net/dev').read_text().splitlines()[2:]]
        self.assertEqual(interfaces,['lo'])
        for key in ('WAYLAND_DISPLAY','DISPLAY','HYPRLAND_INSTANCE_SIGNATURE','DBUS_SESSION_BUS_ADDRESS'):
            self.assertNotIn(key,os.environ)

    def test_noop_setter_is_not_confirmation(self):
        self.theme('before');self.current_theme('before');self.shim('omarchy-theme-set','sys.exit(0)\n')
        result=self.b.action('theme:before','apply',True)
        self.assertFalse(result['ok'],result)
    def test_theme_tree_symlink_refuses_before_setter(self):
        self.theme('before');p=self.theme('after');self.current_theme('before');self.theme_setter()
        outside=self.home/'outside.conf';outside.write_text('private')
        (p/'shell.toml').symlink_to(outside)
        result=self.b.action('theme:after','apply',True)
        self.assertFalse(result['ok'],result)
        self.assertFalse((self.home/'calls').exists())
    def test_malformed_restore_receipt_is_ignored(self):
        self.theme()
        with self.b.State() as state:state.save({'before':[],'after':{},'selected':0})
        result=self.b.catalog()
        self.assertNotIn('restore',result['actions'])
    def test_restore_refuses_external_theme_generation(self):
        self.theme('before');self.theme('after');self.current_theme('before');self.theme_setter()
        self.assertTrue(self.b.action('theme:after','apply',True)['ok'])
        (self.home/'.local/state/omarchy/current/theme.generation').write_text('external')
        self.assertFalse(self.b.action('theme:after','restore',True)['ok'])
        self.assertEqual(len((self.home/'calls').read_text().splitlines()),1)
    def test_deleted_theme_is_not_fresh_membership(self):
        p=self.theme();(p/'colors.toml').unlink();p.rmdir();self.theme_setter()
        self.assertFalse(self.b.action('theme:test-theme','apply',True)['ok'])
        self.assertFalse((self.home/'calls').exists())
    def test_warning_and_nonzero_failures_are_reported(self):
        self.theme('before');self.theme('after');self.current_theme('before');self.theme_setter()
        setter=self.home/'bin/omarchy-theme-set'
        with setter.open('a') as f:f.write("sys.stderr.write('retint failed')\n")
        result=self.b.action('theme:after','apply',True)
        self.assertFalse(result['ok']);self.assertIn('warnings',result['message'].lower())
        self.assertIn('restore',self.b.catalog()['actions'])
        self.shim('omarchy-theme-set','sys.exit(4)\n')
        self.assertFalse(self.b.action('theme:before','apply',True)['ok'])
    def test_full_membership_beyond_display_cap(self):
        for i in range(270):self.theme('theme-%03d'%i)
        self.current_theme('theme-000');self.theme_setter()
        self.assertEqual(len(self.b.catalog()['rows']),256)
        self.assertTrue(self.b.action('theme:theme-269','apply',True)['ok'])
    def test_current_and_overlay_preview_are_local(self):
        stock=self.theme();(stock/'preview.png').write_bytes(png())
        user=self.theme(user=True);(user/'preview.png').write_bytes(png((0,255,0)))
        self.current_theme('test-theme')
        row=self.b.catalog()['rows'][0]
        self.assertEqual(row['preview'],str(user/'preview.png'));self.assertIn('Current',row['detail'])

    def test_catalog_is_read_only(self):
        self.image();self.theme()
        before={str(p.relative_to(self.home)):p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        self.b.catalog()
        after={str(p.relative_to(self.home)):p.read_bytes() for p in self.home.rglob('*') if p.is_file()}
        self.assertEqual(after,before)
    def test_catalog_rejects_symlink_ancestor(self):
        outside=self.home/'external';outside.mkdir();(outside/'escape').mkdir()
        parent=self.home/'.config/omarchy';parent.mkdir(parents=True)
        (parent/'themes').symlink_to(outside,target_is_directory=True)
        self.assertEqual(self.b.catalog()['rows'],[])
    def test_fifo_and_oversized_image_are_not_previews(self):
        theme=self.theme();root=theme/'backgrounds';root.mkdir()
        p=root/'valid.png';p.write_bytes(png())
        os.mkfifo(root/'pipe.png')
        with (root/'large.png').open('wb') as stream: stream.truncate(self.b.MAX_IMAGE+1)
        self.assertEqual(self.b.catalog()['rows'][0]['preview'],str(p))
    def test_theme_runtime_does_not_ship_test_only_wallpaper_discovery(self):
        for name in ('image_roots','image_files','token','MAX_FILES','hashlib'):
            self.assertFalse(hasattr(self.b,name),name+' is not part of theme runtime')
    def test_racing_file_read_fails_closed(self):
        p=self.image();actual=self.b.opened
        import contextlib
        @contextlib.contextmanager
        def racing(path,limit):
            with actual(path,limit) as (stream,info):
                class Proxy:
                    def read(inner,n):
                        data=stream.read(n);p.write_bytes(png((0,255,0)));return data
                    def fileno(inner):return stream.fileno()
                yield Proxy(),info
        with patch.object(self.b,'opened',racing):
            with self.assertRaises(ValueError):self.b.read(p,self.b.MAX_IMAGE)
    def test_subprocess_output_is_bounded(self):
        with self.assertRaises(self.b.OperationError):
            self.b.run(['/usr/bin/python3','-c','import sys;sys.stdout.write("x"*1000000)'],timeout=1)
    def test_action_lock_is_exclusive(self):
        with self.b.State():
            with self.assertRaises(OSError):
                with self.b.State():pass
    def test_state_symlink_is_rejected(self):
        dest=self.home/'untouched';dest.mkdir()
        state=self.b.state_path();state.parent.mkdir(parents=True,exist_ok=True);state.symlink_to(dest,target_is_directory=True)
        result=self.b.action('unknown','restore',True)
        self.assertFalse(result['ok']);self.assertEqual(list(dest.iterdir()),[])
    def test_literal_invalid_ids_cannot_execute(self):
        for value in ('../../escape','$(touch injected)',';touch injected','--help','bad\nname','x'*200):
            self.assertFalse(self.b.action(value,'apply',True)['ok'])
            self.assertFalse(self.b.action(value,'restore',True)['ok'])
        self.assertFalse((self.home/'injected').exists())
    def test_scan_budget_does_not_read_overfull_preview_directory(self):
        theme=self.theme();root=theme/'backgrounds';root.mkdir()
        (root/'wall.png').write_bytes(png())
        for i in range(self.b.MAX_ENTRIES): (root/str(i)).touch()
        self.assertNotIn('preview',self.b.catalog()['rows'][0])
    def test_png_dimension_bomb_is_rejected(self):
        p=self.image();data=bytearray(p.read_bytes());data[16:24]=struct.pack('!II',100000,100000);p.write_bytes(data)
        self.assertFalse(self.b.valid_image(p))

    def test_timeout_kills_descendant_after_parent_exits(self):
        script=self.home/'fork.py'
        script.write_text("import os,time,signal\nfrom pathlib import Path\npid=os.fork()\nif pid: Path(os.environ['HOME']+'/child.pid').write_text(str(pid));os._exit(0)\nsignal.signal(signal.SIGTERM,signal.SIG_IGN)\ntime.sleep(30)\n")
        with self.assertRaises(self.b.OperationError): self.b.run(['/usr/bin/python3',str(script)],timeout=0.15)
        pid=int((self.home/'child.pid').read_text())
        import time,signal
        time.sleep(0.05)
        status=Path('/proc')/str(pid)/'stat'
        state=status.read_text().split()[2] if status.exists() else 'gone'
        if state not in ('gone','Z'):
            os.kill(pid,signal.SIGKILL)
        self.assertIn(state,('gone','Z'),'provider descendant survived timeout')

    def shim(self,name,body):
        p=self.home/'bin'/name
        p.write_text('#!/usr/bin/python3\nimport os,json,sys\nfrom pathlib import Path\nh=Path(os.environ["HOME"])\n'+body)
        p.chmod(0o755)
    def theme_setter(self):
        self.shim('omarchy-theme-set',"with (h/'calls').open('a') as f: f.write(json.dumps(sys.argv[1:])+'\\n')\np=h/'.local/state/omarchy/current';p.mkdir(parents=True,exist_ok=True)\n(p/'theme.name').write_text(sys.argv[1]+'\\n')\n(p/'theme.generation').write_text(sys.argv[1]+'-generation')\n")
    def current_theme(self,name):
        p=self.home/'.local/state/omarchy/current';p.mkdir(parents=True,exist_ok=True)
        (p/'theme.name').write_text(name+'\n');(p/'theme.generation').write_text('before')
    def test_apply_and_compensating_restore(self):
        self.theme('before');self.theme('after');self.current_theme('before');self.theme_setter()
        self.assertFalse(self.b.action('theme:after','apply')['ok'])
        self.assertTrue(self.b.action('theme:after','apply',True)['ok'])
        self.assertEqual(json.loads((self.home/'calls').read_text().splitlines()[0]),['after'])
        self.assertIn('restore',self.b.catalog()['actions'])
        self.assertTrue(self.b.action('theme:after','restore',True)['ok'])
        self.assertEqual((self.home/'.local/state/omarchy/current/theme.name').read_text().strip(),'before')
        self.assertNotIn('restore',self.b.catalog()['actions'])

    def test_cli_catalog_and_explicit_action_gate(self):
        self.image()
        self.theme()
        def cli(*args):
            return subprocess.run(['/usr/bin/python3',str(BACKEND),*args],capture_output=True,text=True,timeout=8,cwd='/')
        r=cli('catalog');self.assertEqual(r.returncode,0)
        value=json.loads(r.stdout);self.assertIn('rows',value);self.assertIn('actions',value)
        self.assertLess(len(r.stdout.encode()),1048576)
        for args in [('action','--id','../../no','--verb','apply'),('action','--id','bad','--verb','exec','--apply')]:
            r=cli(*args);self.assertNotEqual(r.returncode,0)
            value=json.loads(r.stdout);self.assertFalse(value['ok']);self.assertNotIn('Traceback',r.stderr)

    def test_catalog_rejects_unsafe_members(self):
        self.theme('../escape')
        self.theme('BAD NAME')
        good=self.theme('good')
        (good/'preview.png').symlink_to(self.image())
        (good/'colors.toml').write_text('invalid [toml')
        outside=self.home/'outside';outside.mkdir()
        (self.b.STOCK/'link').symlink_to(outside,target_is_directory=True)
        user=self.home/'.config/omarchy/themes';user.mkdir(parents=True)
        self.theme('shadowed')
        (user/'shadowed').symlink_to(outside,target_is_directory=True)
        rows=self.b.catalog()['rows']
        self.assertEqual([r['id'] for r in rows],['theme:good'])
        self.assertNotIn('preview',rows[0])
        self.assertNotIn('color',rows[0])

    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.home=Path(self.tmp.name)
        self.env=patch.dict(os.environ,{'HOME':str(self.home),'PATH':str(self.home/'bin')+':/usr/bin'},clear=True)
        self.env.start()
        (self.home/'bin').mkdir()
        spec=importlib.util.spec_from_file_location('backend',BACKEND)
        self.b=importlib.util.module_from_spec(spec);spec.loader.exec_module(self.b)
        self.b.STOCK=self.home/'stock'
        self.b.STOCK.mkdir()
    def tearDown(self):
        self.env.stop();self.tmp.cleanup()
    def theme(self,name='test-theme',user=False):
        root=self.home/'.config/omarchy/themes' if user else self.b.STOCK
        p=root/name;p.mkdir(parents=True,exist_ok=True)
        (p/'colors.toml').write_text('accent = "#123456"\n')
        return p
    def image(self,name='red.png',rgb=(255,0,0)):
        p=self.home/'Pictures/Wallpapers'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(png(rgb));return p

    def test_catalog_merges_stock_and_user_overlay(self):
        stock=self.theme();(stock/'preview.png').write_bytes(png())
        user=self.theme(user=True)
        self.assertTrue(callable(getattr(self.b,'catalog',None)), 'catalog behavior missing')
        rows=self.b.catalog()['rows']
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['id'],'theme:test-theme')
        self.assertEqual(rows[0]['color'],'#123456')
        self.assertEqual(rows[0]['preview'],str(stock/'preview.png'))
        self.assertIn('overlay',rows[0]['detail'].lower())
