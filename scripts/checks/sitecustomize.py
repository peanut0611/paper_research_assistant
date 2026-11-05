"""Loaded only when the regression runner adds this directory to PYTHONPATH."""
import os

if os.environ.get('PAPER_TEST_GUARD') == '1':
    from offline_guard import install
    install()
