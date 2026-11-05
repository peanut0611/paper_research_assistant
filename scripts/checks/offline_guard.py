"""Accidental-I/O guard for trusted tests, not an OS security sandbox."""
import ipaddress
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
GUARD_DIR = Path(__file__).resolve().parent


def loopback(host):
    if isinstance(host, bytes):
        host = host.decode('ascii', errors='replace')
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def install():
    if getattr(sys, '_paper_offline_guard', False):
        return

    def audit(event, args):
        if event == 'open' and isinstance(args[0], (str, bytes, os.PathLike)):
            if Path(os.fsdecode(args[0])).resolve() == ROOT / '.env':
                raise PermissionError('Offline tests must not access the project .env')
        if event == 'socket.getaddrinfo' and not loopback(args[0]):
            raise PermissionError('Offline tests block external DNS')
        if event in ('socket.connect', 'socket.bind'):
            address = args[1]
            if not isinstance(address, tuple) or not loopback(address[0]):
                raise PermissionError('Offline tests allow only loopback sockets')
        if event == 'socket.sendto' and not loopback(args[-1][0]):
            raise PermissionError('Offline tests block external datagrams')
        if event == 'subprocess.Popen':
            executable, command, cwd, env = args
            # Existing tests explicitly replace PYTHONPATH. Keep the startup
            # guard in Python children, including the isolated PDF parser.
            # Windows reports executable=None and a serialized command line.
            # Add the guard to every child environment; non-Python tools ignore it.
            target = os.environ if env is None else env
            target['PAPER_TEST_GUARD'] = '1'
            target['PYTHONPATH'] = str(GUARD_DIR) + os.pathsep + target.get('PYTHONPATH', '')

    sys.addaudithook(audit)
    sys._paper_offline_guard = True
    os.environ['PAPER_TEST_GUARD'] = '1'
    os.environ['HF_HUB_OFFLINE'] = '1'
    os.environ['TRANSFORMERS_OFFLINE'] = '1'
    os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
