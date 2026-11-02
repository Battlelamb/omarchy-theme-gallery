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

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / Path(json.loads((ROOT / 'manifest.json').read_text())['entryPoints']['overlay']).parent / 'backend.py'
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

    def test_native_snapshot_hardlink_unlink_ctime_only_allows_apply_and_restore(self):
        import hashlib
        images=[]
        for name in ('before','after'):
            for user in (False,True):
                root=self.theme(name,user)/'backgrounds';root.mkdir()
                p=root/'wall.png';p.write_bytes(png());images.append(p)
        self.current_theme('before');self.theme_setter()
        setter=self.home/'bin/omarchy-theme-set'
        with setter.open('a') as stream:
            stream.write("import time\ntime.sleep(0.002)\nfor root in (h/'stock',h/'.config/omarchy/themes'):\n p=root/sys.argv[1]/'backgrounds/wall.png'\n snapshot=h/'snapshot.png'\n os.link(p,snapshot)\n snapshot.unlink()\n")
        baseline={p:(p.stat(),hashlib.sha256(p.read_bytes()).digest()) for p in images}
        for verb in ('apply','restore'):
            result=self.b.action('theme:after',verb,True)
            self.assertTrue(result['ok'],result)
        for p,(before,digest) in baseline.items():
            after=p.stat()
            self.assertEqual(self.b.fingerprint(before)[:-1],self.b.fingerprint(after)[:-1])
            self.assertNotEqual(before.st_ctime_ns,after.st_ctime_ns)
            self.assertEqual(before.st_nlink,after.st_nlink)
            self.assertEqual(digest,hashlib.sha256(p.read_bytes()).digest())
        self.assertEqual(self.b.current_theme()[0],'before')
        self.assertEqual(self.b.journal(),{})
        self.assertEqual([json.loads(line) for line in (self.home/'calls').read_text().splitlines()],[['after'],['before']])

    def test_provider_same_size_background_edit_with_restored_mtime_is_rejected(self):
        for verb in ('apply','restore'):
            for index,extension in enumerate(('.png','.JPG','.jpeg','.gif','.webp','.bmp')):
                with self.subTest(verb=verb,extension=extension):
                    before_name='before-'+str(index);after_name='after-'+str(index)
                    self.theme(before_name);self.theme(after_name);self.current_theme(before_name);self.theme_setter()
                    if verb=='restore':self.assertTrue(self.b.action('theme:'+after_name,'apply',True)['ok'])
                    target=after_name if verb=='apply' else before_name
                    root=self.b.STOCK/target/'backgrounds';root.mkdir(exist_ok=True)
                    p=root/('wall'+extension);p.write_bytes(png())
                    before=p.stat();original=p.read_bytes()
                    with (self.home/'bin/omarchy-theme-set').open('a') as stream:
                        stream.write("p=h/'stock'/sys.argv[1]/'backgrounds'/"+repr(p.name)+"\ns=p.stat()\nwith p.open('r+b') as f:\n f.seek(-1,2)\n f.write(b'X')\nos.utime(p,ns=(s.st_atime_ns,s.st_mtime_ns))\n")
                    result=self.b.action('theme:'+after_name,verb,True)
                    self.assertFalse(result['ok'],result)
                    self.assertIn('sources changed',result['message'])
                    self.assertEqual(self.b.fingerprint(before)[:-1],self.b.fingerprint(p.stat())[:-1])
                    self.assertNotEqual(original,p.read_bytes())
                    self.assertTrue(self.b.journal())

    def test_background_hash_total_budget_spans_stock_and_overlay_before_provider(self):
        paths=[]
        for user in (False,True):
            root=self.theme('after',user)/'backgrounds';root.mkdir()
            (root/'wall.png').write_bytes(png());paths.append(root.parent)
        size=len(png());self.current_theme('before');self.theme_setter()
        with patch.object(self.b,'MAX_BACKGROUND_BYTES',2*size,create=True):
            self.assertTrue(self.b.theme_signature(paths))
        with patch.object(self.b,'MAX_BACKGROUND_BYTES',2*size-1,create=True):
            result=self.b.action('theme:after','apply',True)
        self.assertFalse(result['ok'],result)
        self.assertIn('total byte limit',result['message'])
        self.assertFalse((self.home/'calls').exists())

    def test_background_signature_rejects_chmod_even_with_unchanged_bytes(self):
        root=self.theme()/'backgrounds';root.mkdir();p=root/'wall.png';p.write_bytes(png())
        before=self.b.theme_signature([root.parent]);p.chmod(0o600)
        self.assertNotEqual(before,self.b.theme_signature([root.parent]))

    def test_only_direct_native_still_backgrounds_tolerate_ctime_only_changes(self):
        root=self.theme();(root/'backgrounds').mkdir();(root/'backgrounds/nested').mkdir()
        for relative in ('colors.toml','preview.png','backgrounds/wall.mp4','backgrounds/unknown.dat','backgrounds/nested/wall.png'):
            with self.subTest(path=relative):
                p=root/relative;p.write_bytes(png());before=self.b.theme_signature([root])
                os.link(p,self.home/'snapshot');(self.home/'snapshot').unlink()
                self.assertNotEqual(before,self.b.theme_signature([root]))

    def test_background_signature_retains_directory_identity_and_membership(self):
        root=self.theme();backgrounds=root/'backgrounds';backgrounds.mkdir();p=backgrounds/'wall.png';p.write_bytes(png())
        original=self.b.theme_signature([root]);saved=backgrounds.stat()
        extra=backgrounds/'extra.png';extra.write_bytes(png());os.utime(backgrounds,ns=(saved.st_atime_ns,saved.st_mtime_ns))
        self.assertNotEqual(original,self.b.theme_signature([root]));extra.unlink()
        original=self.b.theme_signature([root]);backgrounds.rename(self.home/'old-backgrounds');backgrounds.mkdir()
        p.write_bytes(png());os.utime(backgrounds,ns=(saved.st_atime_ns,saved.st_mtime_ns))
        self.assertNotEqual(original,self.b.theme_signature([root]))

    def test_background_signature_rejects_replacement_between_stat_and_open(self):
        import contextlib
        root=self.theme()/'backgrounds';root.mkdir();p=root/'wall.png';p.write_bytes(png());actual=self.b.opened
        @contextlib.contextmanager
        def replaced(path,limit=self.b.MAX_IMAGE):
            replacement=self.home/'replacement';replacement.write_bytes(png());os.replace(replacement,p)
            with actual(path,limit) as value:yield value
        with patch.object(self.b,'opened',replaced):
            with self.assertRaisesRegex(self.b.OperationError,'changed before reading'):self.b.theme_signature([root.parent])

    def test_background_signature_rejects_same_size_restored_mtime_edit_during_fd_read(self):
        import contextlib
        root=self.theme()/'backgrounds';root.mkdir();p=root/'wall.png';p.write_bytes(png());actual=self.b.opened
        @contextlib.contextmanager
        def racing(path,limit=self.b.MAX_IMAGE):
            with actual(path,limit) as (stream,info):
                class Proxy:
                    def read(inner,n):
                        data=stream.read(n)
                        with p.open('r+b') as changed:changed.seek(-1,2);changed.write(b'X')
                        os.utime(p,ns=(info.st_atime_ns,info.st_mtime_ns))
                        return data
                    def fileno(inner):return stream.fileno()
                yield Proxy(),info
        with patch.object(self.b,'opened',racing):
            with self.assertRaisesRegex(self.b.OperationError,'changed while reading'):self.b.theme_signature([root.parent])

    def test_background_signature_rejects_growing_fd_read(self):
        import contextlib
        root=self.theme()/'backgrounds';root.mkdir();p=root/'wall.png';p.write_bytes(png());actual=self.b.opened
        @contextlib.contextmanager
        def growing(path,limit=self.b.MAX_IMAGE):
            with actual(path,limit) as (stream,info):
                with p.open('ab') as changed:changed.write(b'X')
                yield stream,info
        with patch.object(self.b,'opened',growing):
            with self.assertRaisesRegex(self.b.OperationError,'changed while reading'):self.b.theme_signature([root.parent])

    def test_background_signature_rejects_symlinks_special_files_and_existing_tree_limits(self):
        root=self.theme();backgrounds=root/'backgrounds';backgrounds.mkdir();p=backgrounds/'wall.png'
        for kind in ('symlink','fifo','oversized'):
            with self.subTest(kind=kind):
                if kind=='symlink':p.symlink_to(self.image())
                elif kind=='fifo':os.mkfifo(p)
                else:
                    with p.open('wb') as stream:stream.truncate(self.b.MAX_IMAGE+1)
                with self.assertRaises(self.b.OperationError):self.b.theme_signature([root])
                p.unlink()
        deep=backgrounds
        for _ in range(5):deep=deep/'nested';deep.mkdir()
        with self.assertRaisesRegex(self.b.OperationError,'depth'):self.b.theme_signature([root])
        import shutil
        shutil.rmtree(backgrounds);backgrounds.mkdir()
        for index in range(2049):(backgrounds/str(index)).touch()
        with self.assertRaisesRegex(self.b.OperationError,'entry limit'):self.b.theme_signature([root])

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
