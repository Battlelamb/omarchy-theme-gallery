# SPDX-License-Identifier: GPL-3.0-only
"""Independent bounded local adapter; no daemon, network or shell interpolation."""
import argparse
import contextlib
import fcntl
from hashlib import sha256
import itertools
import json
import os
from pathlib import Path
import re
import selectors
import signal
import stat
import struct
import subprocess
import sys
import time
import tomllib

STOCK = Path('/usr/share/omarchy/themes')
MAX_ROWS = 256
MAX_ENTRIES = 1024
MAX_IMAGE = 32 * 1024 * 1024
MAX_BACKGROUND_BYTES = 128 * 1024 * 1024
SLUG = re.compile(r'[a-z0-9][a-z0-9-]{0,63}\Z')
HEX = re.compile(r'#[0-9a-fA-F]{6}\Z')

def home():
    p = Path(os.environ['HOME'])
    if not p.is_absolute(): raise ValueError('HOME must be absolute')
    return p

def text(value, limit=160):
    return isinstance(value,str) and 0 < len(value) <= limit and not any(ord(c)<32 or ord(c)==127 or 0xd800<=ord(c)<=0xdfff for c in value)

def directory(path, create=False):
    path=Path(path)
    if not path.is_absolute() or '..' in path.parts: raise ValueError('Unsafe local path')
    fd=os.open('/',os.O_RDONLY|os.O_DIRECTORY)
    try:
        for part in path.parts[1:]:
            if create:
                try: os.mkdir(part,0o700,dir_fd=fd)
                except FileExistsError: pass
            nxt=os.open(part,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW,dir_fd=fd)
            os.close(fd);fd=nxt
        return fd
    except BaseException:
        os.close(fd);raise

def names(path):
    try:
        fd=directory(path)
        try:
            with os.scandir(fd) as entries:
                out=[e.name for e in itertools.islice(entries,MAX_ENTRIES+1)]
            if len(out)>MAX_ENTRIES: return []
            return sorted(n for n in out if text(n) and not n.startswith('.'))
        finally: os.close(fd)
    except OSError: return []

def is_directory(path):
    try: fd=directory(path);os.close(fd);return True
    except OSError: return False

@contextlib.contextmanager
def opened(path,limit=MAX_IMAGE):
    fd=directory(Path(path).parent)
    try:
        filefd=os.open(Path(path).name,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK,dir_fd=fd)
        try:
            info=os.fstat(filefd)
            if not stat.S_ISREG(info.st_mode) or info.st_size>limit: raise ValueError('File is not regular or exceeds size limit')
            with os.fdopen(filefd,'rb',closefd=False) as stream: yield stream,info
        finally: os.close(filefd)
    finally: os.close(fd)

def fingerprint(info):
    return (info.st_dev,info.st_ino,info.st_size,info.st_mtime_ns,info.st_ctime_ns)

def read(path,limit=65536):
    with opened(path,limit) as (stream,before):
        data=stream.read(limit+1)
        if len(data)>limit or fingerprint(os.fstat(stream.fileno()))!=fingerprint(before): raise ValueError('File changed while reading')
        return data

def optional_read(path,limit=65536):
    try: return read(path,limit)
    except (OSError,ValueError): return b''

def themes():
    result={}
    user=home()/'.config/omarchy/themes'
    for root in (STOCK,user):
        for name in names(root):
            if SLUG.fullmatch(name) and is_directory(root/name): result.setdefault(name,[]).append(root/name)
    # A user symlink would still be followed by the stock setter: block its stock slug too.
    for name in list(result):
        p=user/name
        if os.path.lexists(p) and not is_directory(p): del result[name]
    return dict(sorted(result.items()))

def image_type(data):
    if data.startswith(b'\x89PNG\r\n\x1a\n') and len(data)>=24:
        width,height=struct.unpack('!II',data[16:24])
        if width and height and width<=16384 and height<=16384 and width*height<=64000000: return 'PNG'
    if data.startswith(b'\xff\xd8\xff'): return 'JPEG'
    if len(data)>12 and data[:4]==b'RIFF' and data[8:12]==b'WEBP': return 'WEBP'
    if data.startswith(b'BM'): return 'BMP'
    raise ValueError('Unsupported or oversized image header')

def valid_image(path):
    if not text(str(path),4096) or path.suffix.lower() not in ('.png','.jpg','.jpeg','.webp','.bmp'): return False
    try:
        with opened(path) as (stream,info): image_type(stream.read(64))
        return True
    except (OSError,ValueError): return False

def catalog():
    rows=[]
    current=optional_read(home()/'.local/state/omarchy/current/theme.name',128).decode(errors='replace').strip()
    for name,paths in themes().items():
        row={'id':'theme:'+name,'label':name.replace('-',' ').title(),'detail':('Stock + user overlay' if len(paths)>1 else ('Stock theme' if paths[0].parent==STOCK else 'User theme'))+(' · Current' if current==name else '')}
        for p in paths:
            try:
                value=tomllib.loads(read(p/'colors.toml').decode()).get('accent','')
                if isinstance(value,str) and HEX.fullmatch(value): row['color']=value
            except (OSError,ValueError,UnicodeError): pass
            candidates=[p/('preview'+ext) for ext in ('.png','.jpg','.jpeg','.webp','.bmp')]
            candidates += [p/'backgrounds'/n for n in names(p/'backgrounds')]
            for candidate in candidates:
                if valid_image(candidate): row['preview']=str(candidate);break
        rows.append(row)
    return {'rows':rows[:MAX_ROWS],'message':('Installed themes; explicit Apply runs normal Omarchy hooks. Symlinked themes excluded; display cap 256, per-root entry cap 1024.' if rows else 'No safe installed themes found in the stock/user roots; symlinks and over-budget directories are excluded.'),'actions':['apply']+(['restore'] if journal().get('before') in themes() else []),'actionLabel':'Apply'}


class OperationError(ValueError): pass

def run(argv, payload=None, timeout=12, env=None):
    """Bound both collection and process group lifetime, including pipe-holding children."""
    source=None
    if payload is not None:
        source=os.fdopen(os.memfd_create('palette-input',os.MFD_CLOEXEC),'w+b')
        source.write(payload);source.seek(0)
    try:
        proc=subprocess.Popen(argv,stdin=source if source else subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.PIPE,start_new_session=True,env=env)
    finally:
        if source: source.close()
    complete=False
    output=[bytearray(),bytearray()];deadline=time.monotonic()+timeout
    try:
        with selectors.DefaultSelector() as selector:
            for index,pipe in enumerate((proc.stdout,proc.stderr)):
                os.set_blocking(pipe.fileno(),False);selector.register(pipe,selectors.EVENT_READ,index)
            while selector.get_map():
                remaining=deadline-time.monotonic()
                if remaining<=0: raise OperationError('Provider timed out; state may be partially changed. Refresh before retrying.')
                for key,_ in selector.select(min(remaining,0.1)):
                    block=os.read(key.fd,8192)
                    if not block: selector.unregister(key.fileobj);continue
                    output[key.data].extend(block)
                    if len(output[key.data])>65536: raise OperationError('Provider output exceeded limit; state may be partially changed.')
            try: code=proc.wait(timeout=max(0.01,deadline-time.monotonic()))
            except subprocess.TimeoutExpired: raise OperationError('Provider timed out; state may be partially changed.') from None
        complete=True
        return code,bytes(output[0]),bytes(output[1])
    finally:
        # Do not leave descendants holding an interrupted operation alive.
        if not complete or proc.poll() is None:
            try: os.killpg(proc.pid,signal.SIGKILL)
            except ProcessLookupError: pass
        for pipe in (proc.stdout,proc.stderr): pipe.close()
        proc.wait()

def state_path(): return home()/'.local/state'/('omarchy-'+PLUGIN)

class State:
    def __enter__(self):
        self.fd=directory(state_path(),True)
        try:
            self.lock=os.open('lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW|os.O_NONBLOCK,0o600,dir_fd=self.fd)
            if not stat.S_ISREG(os.fstat(self.lock).st_mode) or os.fstat(self.lock).st_nlink!=1: raise OperationError('Unsafe action lock')
            fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BaseException:
            if hasattr(self,'lock'): os.close(self.lock)
            os.close(self.fd);raise
        return self
    def __exit__(self,*_):
        os.close(self.lock);os.close(self.fd)
    def put(self,name,data):
        temp='.write-'+os.urandom(12).hex()
        fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600,dir_fd=self.fd)
        try:
            with os.fdopen(fd,'wb') as stream: stream.write(data);stream.flush();os.fsync(stream.fileno())
            os.replace(temp,name,src_dir_fd=self.fd,dst_dir_fd=self.fd)
            os.fsync(self.fd)
        finally:
            try: os.unlink(temp,dir_fd=self.fd)
            except FileNotFoundError: pass
    def save(self,value): self.put('restore.json',json.dumps(value).encode())
    def clear(self):
        try: os.unlink('restore.json',dir_fd=self.fd)
        except FileNotFoundError: pass

def journal():
    try:
        value=json.loads(read(state_path()/'restore.json',8192))
        if not isinstance(value,dict): return {}
        if not all(isinstance(value.get(k),str) and len(value[k])<=160 for k in ('selected','before','after','generation')): return {}
        return value
    except (OSError,ValueError): return {}

PLUGIN='theme-gallery'

def theme_signature(paths):
    result=[];count=0;background_bytes=0
    def visit(path,depth=0):
        nonlocal count,background_bytes
        if depth>5: raise OperationError('Theme exceeds safe directory depth')
        fd=directory(path)
        try:
            info=os.fstat(fd);result.append((str(path),fingerprint(info)))
            with os.scandir(fd) as entries:
                children=list(itertools.islice(entries,2049))
            if len(children)>2048: raise OperationError('Theme exceeds safe entry limit')
            for entry in sorted(children,key=lambda e:e.name):
                if entry.name.startswith('.'): continue
                count+=1
                if count>2048 or not text(entry.name): raise OperationError('Theme exceeds safe entry/name limit')
                info=os.stat(entry.name,dir_fd=fd,follow_symlinks=False)
                child=path/entry.name
                if stat.S_ISDIR(info.st_mode): visit(child,depth+1)
                elif stat.S_ISREG(info.st_mode):
                    if info.st_size>MAX_IMAGE: raise OperationError('Theme contains an oversized file')
                    signature=fingerprint(info)
                    if depth==1 and path.name=='backgrounds' and child.suffix.lower() in ('.png','.jpg','.jpeg','.gif','.webp','.bmp'):
                        background_bytes+=info.st_size
                        if background_bytes>MAX_BACKGROUND_BYTES: raise OperationError('Theme backgrounds exceed safe total byte limit')
                        with opened(child) as (stream,before):
                            if fingerprint(before)!=signature: raise OperationError('Theme background changed before reading')
                            data=stream.read(info.st_size+1)
                            if len(data)!=info.st_size or fingerprint(os.fstat(stream.fileno()))!=signature: raise OperationError('Theme background changed while reading')
                        # Native transition snapshots hard-link/unlink stills, changing ctime but not content.
                        signature=signature[:-1]+(info.st_mode,info.st_uid,info.st_gid,sha256(data).digest())
                    result.append((str(child),signature))
                else: raise OperationError('Theme contains a symlink or special file; apply refused')
        finally: os.close(fd)
    for path in paths: visit(path)
    return result

def current_theme():
    base=home()/'.local/state/omarchy/current'
    return (optional_read(base/'theme.name',128).decode(errors='replace').strip(),optional_read(base/'theme.generation',128).decode(errors='replace').strip())

def perform(selected,verb):
    members=themes()
    with State() as state:
        saved=journal()
        before,generation=current_theme()
        if verb=='restore':
            if not saved or selected not in (saved.get('selected'),'restore:last'): raise OperationError('No matching restore receipt')
            if (before,generation)!=(saved.get('after'),saved.get('generation')): raise OperationError('Theme changed externally; restore refused')
            target=saved.get('before')
        elif verb=='apply' and selected.startswith('theme:'):
            target=selected[6:]
        else: raise OperationError('Unsupported action')
        if target not in members: raise OperationError('Theme is no longer installed; refresh')
        signature=theme_signature(members[target])
        if verb=='apply':
            state.save({'selected':selected,'before':before,'after':target,'generation':generation,'pending':True})
        env=dict(os.environ,OMARCHY_PATH='/usr/share/omarchy',OMARCHY_THEME_WAIT='1',OMARCHY_THEME_HEADLESS='0',OMARCHY_THEME_OFFLINE='0')
        env.pop('OMARCHY_THEME_SKIP_BACKGROUND',None)
        code,out,err=run(['omarchy-theme-set',target],timeout=22,env=env)
        after,new_generation=current_theme()
        if verb=='apply':
            state.save({'selected':selected,'before':before,'after':target,'generation':new_generation if after==target else '', 'pending':False})
        if theme_signature(members[target])!=signature: raise OperationError('Theme sources changed during apply; partial changes are possible, refresh before restoring')
        if code or after!=target or not new_generation or new_generation==generation: raise OperationError('Theme command failed or did not confirm the selected theme; partial changes are possible')
        if err: raise OperationError('Theme switched but Omarchy reported warnings; app retints/hooks may be incomplete. Review Omarchy logs; Restore is a compensating action only.')
        if verb=='restore': state.clear()
        return {'ok':True,'message':'Theme selection confirmed. Normal Omarchy hooks ran; restoring a theme is not a full rollback.'}

def action(selected,verb,apply=False):
    if not apply: return {'ok':False,'message':'Explicit --apply is required; nothing changed.'}
    if not text(selected,160) or verb not in ('apply','restore','export'): return {'ok':False,'message':'Invalid selection or action'}
    try: return perform(selected,verb)
    except OperationError as error: return {'ok':False,'message':str(error)[:600]}
    except (OSError,ValueError,KeyError,subprocess.SubprocessError): return {'ok':False,'message':'Local operation failed; check provider, permissions and safe roots. Partial state may remain; refresh.'}


class Parser(argparse.ArgumentParser):
    def error(self,message): raise ValueError('Invalid command arguments; use catalog or action --id ID --verb VERB --apply')

def main():
    try:
        parser=Parser(add_help=False)
        parser.add_argument('command',choices=('catalog','action'))
        parser.add_argument('--id',default='')
        parser.add_argument('--verb',default='')
        parser.add_argument('--apply',action='store_true')
        args=parser.parse_args()
        value=catalog() if args.command=='catalog' else action(args.id,args.verb,args.apply)
        raw=json.dumps(value,ensure_ascii=True,separators=(',',':'))
        if len(raw.encode())>1048576: raise ValueError('Catalog exceeds output limit')
        print(raw)
        return 0 if args.command=='catalog' or value.get('ok') is True else 1
    except (OSError,ValueError,KeyError,subprocess.SubprocessError):
        print(json.dumps({'ok':False,'message':'Local operation failed; check provider availability, file permissions and refresh.'}))
        return 1

if __name__=='__main__': sys.exit(main())
