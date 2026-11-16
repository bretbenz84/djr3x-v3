"""Ask the robot's supervisor about telepresence (stage 4): status, start or stop a call.

    .venv/bin/python tools/telepresence_trigger.py status ROBOT_IP:PORT
    .venv/bin/python tools/telepresence_trigger.py start  ROBOT_IP:PORT
    .venv/bin/python tools/telepresence_trigger.py stop   ROBOT_IP:PORT

ROBOT_IP:PORT is the call's address (telepresence_listen); the supervisor answers one port up. The
token comes from this checkout's local/secrets.toml (telepresence_token), or TELEPRESENCE_TOKEN.
start wakes him into a call only while he is asleep; stop ends only a call the supervisor started.
Prints the supervisor's answer as JSON.
"""
from pathlib import Path
import argparse
import json
import os
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / 'src'))

from r3x.settings import ScratchPaths, Secrets  # noqa: E402
from r3x.telepresence.protocol import ProtocolError, valid_token  # noqa: E402
from r3x.telepresence.trigger import request  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('action', choices=('status', 'start', 'stop'))
    parser.add_argument('address', help="ROBOT_IP:PORT, the call's address")
    args = parser.parse_args()
    token = os.environ.get('TELEPRESENCE_TOKEN') or Secrets.load(ScratchPaths.for_project(PROJECT).root).telepresence_token
    if not valid_token(token):
        raise SystemExit('Set telepresence_token in local/secrets.toml (the same line as the robot\'s)')
    try:
        reply = request(args.address, token, {'type': args.action}, client='telepresence-trigger-tool')
    except ProtocolError as exc:
        reply = {'type': 'refused', 'reason': exc.reason}
    except OSError as exc:
        reply = {'type': 'unreachable', 'reason': type(exc).__name__}
    print(json.dumps(reply, indent=2))
    return 0 if reply.get('type') in ('status', 'starting', 'stopping', 'not_running') else 1


if __name__ == '__main__':
    sys.exit(main())
