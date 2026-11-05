import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest


@pytest.mark.parametrize('host', ['api.openai.com', 'arxiv.org', '8.8.8.8', '2001:4860:4860::8888'])
def test_external_network_fails_before_connection(host):
    with pytest.raises(PermissionError, match='Offline tests'):
        socket.getaddrinfo(host, 443)


def test_direct_ip_cannot_bypass_dns_guard():
    with socket.socket() as client:
        with pytest.raises(PermissionError, match='Offline tests'):
            client.connect(('8.8.8.8', 443))


def test_real_dotenv_is_blocked_without_reading_it():
    root = Path(__file__).resolve().parents[2]
    with pytest.raises(PermissionError, match='project .env'):
        (root / '.env').read_bytes()


def test_python_child_with_replaced_pythonpath_keeps_guard(tmp_path):
    command = "import sys,socket; assert sys._paper_offline_guard; socket.getaddrinfo('api.openai.com',443)"
    result = subprocess.run([sys.executable, '-c', command], cwd=tmp_path,
                            env={**os.environ, 'PYTHONPATH': str(tmp_path)}, capture_output=True, text=True, timeout=10)
    assert result.returncode != 0
    assert 'Offline tests block external DNS' in result.stderr
