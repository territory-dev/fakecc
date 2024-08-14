#!/usr/bin/env python3
'''
fakecc is a build sniffing tool similar to Bear, focusing on generating
`compile_commands.json` with minimal work.
'''

__version__ = '0.1'

import atexit
from fnmatch import fnmatch
from pathlib import Path
from sys import argv, stdin, exit
import json
import os
import socket
import signal
import subprocess
from tempfile import mkdtemp
import time

if os.getenv('FAKECC'):
    exit('recursive call')
os.environ['FAKECC'] = 'yes'


sock_path = os.getenv('FAKECC_SOCK', os.getcwd() + '/fakecc.sock')
pass_pattern = os.getenv('FAKECC_PASS')
pass_pattern_rec = os.getenv('FAKECC_PASS_REC')

compiler_bins = { 'cc', 'clang', 'clang++' }
all_noop_bins = { 'ar', 'ld', 'objcopy', 'objtool' }
try:
    enabled_noop_progs = os.environ['FAKECC_NOOP_PROGS'].split(',')
except KeyError:
    enabled_noop_progs = []


def fake_bin_path() -> Path:
    try:
        return Path(os.environ['FAKECC_BIN_PATH'])
    except KeyError:
        exit('FAKECC_BIN_PATH not set')


this_dir = Path(__file__).parent.resolve()
def find_exec_in_base_path(prog_name) -> Path | None:
    for p in os.environ['PATH'].split(':'):
        p = Path(p)
        bin_path = Path(p, prog_name)
        if bin_path.exists() and p.resolve() != this_dir:
            return p


def self_path() -> Path:
    return Path(__file__).resolve()


base_clang_dir = os.getenv('FAKECC_CLANG_PATH')
if base_clang_dir:
    base_clang_dir = Path(base_clang_dir)
else:
    base_clang_dir = find_exec_in_base_path('clang')


def daemon_loop(prog_name):
    print(f'{prog_name}: daemon running with PID {os.getpid()}, listening on {sock_path}', flush=True)
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.bind(sock_path)
    s.listen()

    capture = []

    while True:
        con, addr = s.accept()
        with con:
            sf = con.makefile('r')
            msg = sf.readline()
            if not msg:
                continue
            try:
                data = json.loads(msg)
            except Exception as e:
                print(f'{prog_name}: message decode failed: {e}')
                continue

            try:
                match data['cmd']:
                    case 'stop':
                        s.close()
                        daemon_shutdown()
                        return
                    case 'cap':
                        capture.append(data['body'])
                    case 'dump':
                        with Path(data['path']).open('w') as f:
                            json.dump(capture, f, indent=4)
            except Exception as e:
                print(f'{prog_name}: {e}')



def daemon_shutdown(*_):
    Path(sock_path).unlink(missing_ok=True)
def daemon_term(*_):
    daemon_shutdown()
    exit(0)


class DaemonSender:
    def __init__(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.connect(sock_path)
        self.sf = self.sock.makefile('w')

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        self.sf.close()
        self.sock.close()

    def send(self, data):
        bytes_ = json.dumps(data)
        self.sf.writelines([bytes_])


def start_daemon(prog_name):
    if Path(sock_path).exists():
        exit(f'{prog_name}: daemon socket exists: {sock_path}')

    if os.fork() > 0:
        print(f'{prog_name}: waiting for deamon to start...')
        t0 = time.time()
        while time.time() - t0 < 10.0:
            if Path(sock_path).is_socket():
                return
        exit(f'{prog_name}: timed out')
    else:
        os.setsid()
        if os.fork() > 0:
            exit(0)
        else:
            if not stdin.closed:
                stdin.close()
            atexit.register(daemon_shutdown)
            signal.signal(signal.SIGTERM, daemon_term)
            exit(daemon_loop(prog_name))


def stop_daemon(prog_name):
    with DaemonSender() as ds:
        ds.send({'cmd': 'stop'})

    t0 = time.time()
    while Path(sock_path).exists():
        if time.time() - t0 > 4.0:
            exit(f'{prog_name}: timed out')
        time.sleep(0)


def install():
    self_path_ = self_path()
    fake_bin_dir = Path(mkdtemp())
    for bin_name in [*compiler_bins, *all_noop_bins]:
        (fake_bin_dir / bin_name).symlink_to(self_path_)

    return fake_bin_dir


def dump(dump_path):
    DaemonSender().send({'cmd': 'dump', 'path': dump_path})


def wrap(prog_name, args):
    fake_bin_path = install()
    try:
        base_path = os.environ["PATH"]
        path = f'{fake_bin_path}:{base_path}'
    except KeyError:
        path = fake_bin_path
    env = {
        **os.environ,
        'PATH': path,
        'FAKECC_BIN_PATH': str(fake_bin_path),
    }
    del env['FAKECC']

    start_daemon(prog_name)
    ret = subprocess.call(args, env=env)
    dump('compile_commands.json')
    stop_daemon(prog_name)

    for l in fake_bin_path.glob('*'):
        l.unlink()
    fake_bin_path.rmdir()
    exit(ret)



def cmd_main(prog_name, args):
    cmd = args[0]
    match cmd:
        case 'install':
            fake_bin_dir = install()
            print(f'export PATH="{fake_bin_dir}:$PATH"')
            print(f'export FAKECC_BIN_PATH="{fake_bin_dir}"')
        case 'start':
            start_daemon(prog_name)
        case 'stop':
            stop_daemon(prog_name)
        case 'dump':
            dump(args[1])
        case 'run':
            wrap(prog_name, args[1:])
        case _:
            exit(f'{prog_name}: unrecognized command: {cmd}')


def clang_passthrough(args):
    ret = subprocess.call([str(base_clang_dir / 'clang'), *args])
    exit(ret)


def communicate_clang_compile_command(prog_name, args, do_compile=False):
    d = mkdtemp()
    ccp = Path(d, 'cc')
    pargs = [
        str(base_clang_dir / 'clang'),
        f'-MJ{ccp}',
    ]
    if not do_compile:
        pargs.append('-fdriver-only')
    pargs.extend(args)
    ret = subprocess.call(pargs)
    if ret != 0 or not ccp.exists():
        return None
    s = ccp.read_text()
    j = json.loads(s.rstrip(', \n'))
    if not do_compile:
        j['arguments'].remove('-fdriver-only')
    ccp.unlink(missing_ok=True)
    os.rmdir(d)
    return j


def clang_main(prog_name, args):
    if '-c' not in args:  # TODO: clang x.c
        return clang_passthrough(args)

    j = communicate_clang_compile_command(prog_name, args)
    if not j:
        return clang_passthrough(args)

    fn = j['file']

    for pp in pass_pattern.split(',') if pass_pattern else []:
        if fnmatch(Path(fn).name, pp):
            return clang_passthrough(args)

    with DaemonSender() as ds:
        ds.send(dict(cmd='cap', body=j))

    for pp in pass_pattern_rec.split(',') if pass_pattern_rec else []:
        if fnmatch(Path(fn).name, pp):
            return clang_passthrough(args)


def main(argv=argv):
    prog_name = Path(argv[0]).name
    args = argv[1:]
    if not args:
        exit(f'{prog_name}: missing command')
    elif prog_name in compiler_bins:
        clang_main(prog_name, args)
    elif prog_name in ['fakecc.py', 'fakecc']:
        cmd_main(prog_name, args)
    elif prog_name in all_noop_bins:
        if prog_name not in enabled_noop_progs:
            ep = find_exec_in_base_path(prog_name)
            if not ep:
                print('not found:', prog_name)
                exit(1)
            argv[0] = str(ep / prog_name)
            ret = subprocess.call(argv)
            exit(ret)
        exit(0)
    else:
        exit(f'unrecognized program name: {prog_name}')


if __name__ == '__main__':
    main(argv)
