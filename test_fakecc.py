import json
import os
from pathlib import Path
from subprocess import check_call
from unittest.mock import ANY

import pytest


exec_dir = Path(__file__).parent


@pytest.fixture
def exec(tmp_path):
    sock_path = tmp_path / 'fakecc.sock'
    env = {
        'PATH': f'{exec_dir}:{os.environ["PATH"]}',
        'FAKECC_SOCK': str(sock_path),
    }

    def exec_(cmd, env_extra=None):
        if env_extra is not None:
            xenv = { **env_extra, **env }
        else:
            xenv = env
        return check_call(cmd, env=xenv, timeout=5, cwd=tmp_path)
    exec_.sock_path = sock_path

    yield exec_

    if sock_path.exists():
        exec_(['fakecc.py', 'stop'])


def test_daemonization(exec):
    exec(['fakecc.py', 'start'])
    assert exec.sock_path.exists()
    exec(['fakecc.py', 'stop'])
    assert not exec.sock_path.exists()


def test_capture(exec, tmp_path):
    src = tmp_path / 'f.c'
    src.touch()
    out = tmp_path / 'f.o'

    exec(['fakecc.py', 'start'])
    exec(['sh', '-c', f'''
        eval "$( fakecc.py install )"
        clang -o {str(out)} -c {str(src)}
    '''])

    ccp: Path = tmp_path / 'compile_commands.json'
    exec(['fakecc.py', 'dump', str(ccp)])

    with ccp.open('r') as f:
        dumped = json.load(f)

    assert dumped == [
        {
            'arguments': ANY,
            'directory': str(tmp_path),
            'file': str(src),
            'output': str(out),
        }
    ]
    assert '-c' in dumped[0]['arguments']
    assert '-fdriver-only' not in dumped[0]['arguments']
    assert not out.exists()


def test_capture_and_build(exec, tmp_path):
    src = tmp_path / 'f.c'
    src.write_text(r'int f(){}')
    out = tmp_path / 'f.o'

    exec(['fakecc.py', 'start'])
    exec(['sh', '-c', f'''
        export FAKECC_PASS_REC='*'
        eval "$( fakecc.py install )"
        clang -o {str(out)} -c {str(src)}
    '''])

    ccp: Path = tmp_path / 'compile_commands.json'
    exec(['fakecc.py', 'dump', str(ccp)])

    with ccp.open('r') as f:
        dumped = json.load(f)

    assert dumped == [
        {
            'arguments': ANY,
            'directory': str(tmp_path),
            'file': str(src),
            'output': str(out),
        }
    ]
    assert '-c' in dumped[0]['arguments']
    assert out.exists()


def test_version(exec, capfd):
    exec(['sh', '-c', '''
        eval "$( fakecc.py install )"
        clang -v
    '''])
    captured = capfd.readouterr()
    assert 'clang version' in captured.err


def test_passthrough(exec, tmp_path):
    fn: Path = tmp_path / 'tmp1.c'
    fn.touch()
    out = tmp_path / 'f.o'

    d: Path = tmp_path / 'd'
    d.mkdir()
    dfn = d / 'tmp2.c'
    dfn.touch()
    dout = tmp_path / 'f.o'

    exec(['fakecc.py', 'start'])
    exec(['sh', '-c', f'''
        eval "$( fakecc.py install )"
        export FAKECC_PASS='other,tmp*,null'
        clang -o {str(out)} -c {str(fn)}
        clang -o {str(dout)} -c {str(dfn)}
    '''])

    ccp: Path = tmp_path / 'compile_commands.json'
    exec(['fakecc.py', 'dump', str(ccp)])

    with ccp.open('r') as f:
        dumped = json.load(f)

    assert dumped == []
    assert out.exists()
    assert dout.exists()


def test_passthrough_null(exec, tmp_path):
    exec(['fakecc.py', 'start'])
    exec(['sh', '-c', f'''
        eval "$( fakecc.py install )"
        clang -c /dev/null
    '''])

    ccp: Path = tmp_path / 'compile_commands.json'
    exec(['fakecc.py', 'dump', str(ccp)])

    with ccp.open('r') as f:
        dumped = json.load(f)

    assert dumped == []


def test_noop_prog_passthrough(exec, capfd):
    exec(['sh', '-c', f'''
        eval "$( fakecc.py install )"
        ar --help
    '''])
    cap = capfd.readouterr()
    assert 'USAGE:' in cap.out


def test_noop_prog(exec, capfd):
    exec(['sh', '-c', f'''
        export FAKECC_NOOP_PROGS='ar,ld'
        eval "$( fakecc.py install )"
        ar --help
    '''])
    cap = capfd.readouterr()
    assert 'USAGE:' not in cap.out


def test_run(exec, tmp_path):
    src = tmp_path / 'f.c'
    src.touch()
    out = tmp_path / 'f.o'

    exec(['fakecc.py', 'run', 'clang', '-o', str(out), '-c', str(src)])

    ccp: Path = tmp_path / 'compile_commands.json'

    with ccp.open('r') as f:
        dumped = json.load(f)

    assert dumped == [
        {
            'arguments': ANY,
            'directory': str(tmp_path),
            'file': str(src),
            'output': str(out),
        }
    ]
    assert '-c' in dumped[0]['arguments']
    assert str(out) in dumped[0]['arguments']
    assert not out.exists()
    assert not exec.sock_path.exists()
