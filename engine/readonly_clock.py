"""Bounded NTP measurements for diagnostics; never adjusts the system clock.

NTP is not cryptographically authenticated. Two independent public services
corroborate the local clock; this does not certify broker timestamp semantics.
"""
import math
import socket
import struct
import time

from .domain import InvalidData

SOURCES = ('time.windows.com', 'time.cloudflare.com')
EPOCH = 2208988800


def decode(packet, sent, before, after, elapsed):
    if len(packet) < 48 or packet[0] >> 6 == 3 or packet[0] & 7 != 4 or not 1 <= packet[1] <= 15:
        raise InvalidData('HFM_CLOCK_RESPONSE_INVALID')
    if packet[24:32] != sent:
        raise InvalidData('HFM_CLOCK_RESPONSE_MISMATCH')
    if after < before or abs(after - before - elapsed) > .25 or not 0 <= elapsed <= .5:
        raise InvalidData('HFM_HOST_CLOCK_DRIFT_OR_DELAY')
    def stamp(start):
        seconds, fraction = struct.unpack('!II', packet[start:start+8])
        return seconds - EPOCH + fraction / 2**32
    received, transmitted = stamp(32), stamp(40)
    if transmitted < received or received <= 0:
        raise InvalidData('HFM_CLOCK_RESPONSE_INVALID')
    delay = (after - before) - (transmitted - received)
    offset = ((received - before) + (transmitted - after)) / 2
    dispersion = struct.unpack('!I', packet[8:12])[0] / 65536
    root_delay = struct.unpack('!i', packet[4:8])[0] / 65536
    uncertainty = max(0, delay) / 2 + dispersion + abs(root_delay) / 2 + .001
    if delay < -.001 or not math.isfinite(offset) or abs(offset) + uncertainty > .5:
        raise InvalidData('HFM_HOST_CLOCK_UNCERTAIN')
    return offset, uncertainty


def measure():
    observations = []
    try:
        for host in SOURCES:
            for _ in range(2):
                # DNS/socket failures never fall back to the host clock alone.
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.settimeout(2)
                    sock.connect((host, 123))
                    before, mono = time.time(), time.monotonic()
                    seconds = int(before) + EPOCH
                    sent = struct.pack('!II', seconds, int((before % 1) * 2**32))
                    request = bytearray(48); request[0] = 0x23; request[40:48] = sent
                    sock.send(request)
                    packet = sock.recv(512)
                    after = time.time()
                    observations.append(decode(packet, sent, before, after, time.monotonic() - mono))
    except OSError as exc:
        raise InvalidData('HFM_CLOCK_SOURCE_UNAVAILABLE') from exc
    if max(v[0] for v in observations) - min(v[0] for v in observations) > .25:
        raise InvalidData('HFM_CLOCK_SOURCES_DISAGREE')
    return {'status': 'MEASURED', 'sources': list(SOURCES), 'utc': time.time(),
            'monotonic': time.monotonic(),
            'uncertainty_seconds': max(abs(offset) + error for offset, error in observations),
            'authentication': 'UNAUTHENTICATED_NTP_TWO_SOURCES'}


if __name__ == '__main__':
    import json
    try:
        print(json.dumps(measure(), allow_nan=False))
    except InvalidData as exc:
        print(json.dumps({'error': str(exc)}))
