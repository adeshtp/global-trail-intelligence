"""Run the whole test suite with every outbound socket denied.

Proves that normal tests make zero Gemini, Tavily, SearXNG, Open-Meteo,
Nominatim or Postpass calls. This file is a verification tool, not a test
module: it is named offline_guard (no test_ prefix) so unittest discovery
does not collect it.

Usage:
    cd backend && .venv/bin/python tests/offline_guard.py
"""
import os
import socket
import sys

OUTBOUND = []


class _Blocked(RuntimeError):
    pass


def _deny(*args, **kwargs):
    host = args[1] if len(args) > 1 else "?"
    OUTBOUND.append(str(host))
    raise _Blocked(f"outbound network call attempted to {host}")


_real_socket = socket.socket
_real_create = socket.create_connection
_real_getaddrinfo = socket.getaddrinfo


class CountingSocket(_real_socket):
    def connect(self, address):  # noqa: ANN001
        OUTBOUND.append(str(address))
        raise _Blocked(f"outbound connect to {address}")


socket.socket = CountingSocket
socket.create_connection = _deny
socket.getaddrinfo = _deny

import unittest  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.dirname(HERE))          # app package
sys.path.insert(0, os.path.join(REPO, "backend"))  # for `app.*` imports

loader = unittest.TestLoader()
suite = loader.discover(HERE)
runner = unittest.TextTestRunner(verbosity=0)
result = runner.run(suite)

print()
print("=" * 62)
print("OFFLINE PROVIDER-CALL GUARD")
print("=" * 62)
print(f"  tests run      : {result.testsRun}")
print(f"  failures       : {len(result.failures)}")
print(f"  errors         : {len(result.errors)}")
print(f"  outbound calls : {len(OUTBOUND)}")
if OUTBOUND:
    print("  ATTEMPTED      :")
    for host in OUTBOUND[:20]:
        print(f"    - {host}")
verdict = (
    "PASS - suite is fully offline"
    if result.wasSuccessful() and not OUTBOUND
    else "FAIL"
)
print(f"  VERDICT        : {verdict}")
sys.exit(0 if (result.wasSuccessful() and not OUTBOUND) else 1)
