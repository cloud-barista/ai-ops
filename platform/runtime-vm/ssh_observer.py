"""Bounded passive diagnostics in one newly owned guest; no packet payloads."""
import hashlib
import json
import os
import select
import socket
import struct
import subprocess
import sys
import time
from pathlib import Path


def journal_events(raw):
    events = []
    for line in raw.splitlines():
        try:
            record = json.loads(line)
            text = record.get('MESSAGE', '').lower()
            kind = None
            if 'timeout before authentication' in text:
                kind = 'PREAUTH_TIMEOUT'
            elif 'privilege separation' in text:
                kind = 'SSHD_PRIVSEP'
            elif any(word in text for word in ('maxstartups', 'drop connection', 'refusing connection', 'penalty')):
                kind = 'SERVER_RATE_LIMIT'
            elif 'fatal:' in text:
                kind = 'SSHD_FATAL_OTHER'
            elif 'kex_exchange_identification' in text:
                kind = 'KEX_RESET' if 'reset' in text else 'KEX_CLOSED' if 'closed' in text else 'KEX_OTHER'
            elif 'connection reset' in text:
                kind = 'PREAUTH_RESET' if '[preauth]' in text else 'CONNECTION_RESET'
            elif 'connection closed' in text:
                kind = 'PREAUTH_CLOSED' if '[preauth]' in text else 'CONNECTION_CLOSED'
            elif 'error:' in text:
                kind = 'SSHD_ERROR_OTHER'
            stamp = int(record.get('__REALTIME_TIMESTAMP', '0')) // 1000000
            if kind and 0 < stamp < 4102444800:
                events.append({'at': stamp, 'kind': kind})
        except (ValueError, TypeError, AttributeError):
            continue
    return events[-12:]


def packet_metadata(frame, salt, stamp):
    # Ethernet + unfragmented IPv4 TCP. Never copy payload, addresses or ports
    # into a record; a salted tag correlates the two directions of a connection.
    if len(frame) < 54 or frame[12:14] != b'\x08\x00' or frame[23] != 6:
        return None
    ihl = (frame[14] & 15) * 4
    if ihl < 20 or len(frame) < 14 + ihl + 20:
        return None
    if struct.unpack('!H', frame[20:22])[0] & 0x3fff:
        return None
    tcp = 14 + ihl
    source, destination = struct.unpack('!HH', frame[tcp:tcp + 4])
    if 22 not in (source, destination):
        return None
    incoming = destination == 22
    peer = frame[26:30] if incoming else frame[30:34]
    port = source if incoming else destination
    flags = frame[tcp + 13]
    header = (frame[tcp + 12] >> 4) * 4
    total = struct.unpack('!H', frame[16:18])[0]
    payload = max(0, total - ihl - header)
    kind = 'RST' if flags & 4 else 'SYN_ACK' if flags & 2 and flags & 16 else 'SYN' if flags & 2 else 'FIN' if flags & 1 else 'DATA' if payload else None
    if not kind:
        return None
    return {'at_ms': int(stamp * 1000), 'connection': hashlib.sha256(salt + peer + struct.pack('!H', port)).hexdigest()[:12],
            'direction': 'IN' if incoming else 'OUT', 'kind': kind,
            'ttl': frame[22], 'payload_bytes': payload}


def emit(packets, capture):
    try:
        service = subprocess.run(['systemctl', 'is-active', 'ssh.service'], capture_output=True, text=True, timeout=3).stdout.strip()
        logs = subprocess.run(['journalctl', '-u', 'ssh.service', '-u', 'ssh.socket', '--since', '-30sec', '--no-pager', '-n', '100', '--output=json'], capture_output=True, text=True, timeout=4)
        payload = {'stage': 'GUARD_WATCH', 'at': int(time.time()),
                   'ssh_service': service if service in ('active', 'inactive', 'failed', 'activating') else 'UNKNOWN',
                   'journal_available': logs.returncode == 0, 'signals': {},
                   'events': journal_events(logs.stdout) if logs.returncode == 0 else [],
                   'packet_capture': capture, 'packets': packets[-32:]}
        with open('/dev/ttyS0', 'w') as serial:
            serial.write('KHU_BOOTSTRAP_DIAG=' + json.dumps(payload, separators=(',', ':')) + '\n')
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def main():
    root = Path(sys.argv[1])
    if root.parent != Path('/var/tmp') or not root.name.startswith('khu-poc-') or root.is_symlink():
        raise ValueError('owned_guest_root_required')
    guard = json.loads((root / 'guard.json').read_text())
    deadline = min(float(guard['deadline']), time.time() + 1200)
    if guard.get('armed') is not True or not 0 < deadline - time.time() <= 1200:
        raise ValueError('armed_guard_required')
    try:
        fd = os.open(root / 'serial-observer.lock', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except FileExistsError:
        return
    capture = None
    try:
        capture = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(0x0003))
        capture.setblocking(False)
    except OSError:
        capture = None
    ready = emit([], capture is not None)
    (root / 'ssh-observer-ready.json').write_text(json.dumps({'ready': ready, 'packet_capture': capture is not None}))
    packets, salt, next_emit = [], os.urandom(16), time.time() + 5
    while time.time() < deadline:
        if time.time() >= next_emit:
            emit(packets, capture is not None)
            packets, next_emit = [], time.time() + 5
        if capture is None:
            time.sleep(0.5)
        elif select.select([capture], [], [], 0.5)[0]:
            record = packet_metadata(capture.recv(54), salt, time.time())
            if record:
                packets.append(record)
                packets = packets[-32:]
    if capture:
        capture.close()


if __name__ == '__main__':
    main()
