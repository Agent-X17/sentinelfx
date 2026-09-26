"""Bounded public-clock measurements for diagnostics; never adjusts the clock.

NTP is attempted first. When UDP/123 is unavailable, two HTTPS services provide
TLS-authenticated, whole-second Date headers with conservative uncertainty.
"""
from email.utils import parsedate_to_datetime
import math
import socket
import struct
import time
from urllib.request import Request, urlopen

from .domain import InvalidData

SOURCES = ('time.windows.com', 'time.cloudflare.com')
HTTPS_SOURCES = (
    ('www.cloudflare.com', 'https://www.cloudflare.com/cdn-cgi/trace'),
    ('www.microsoft.com', 'https://www.microsoft.com/favicon.ico'),
)
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


def _measure_ntp():
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


def decode_https_date(value, before, after, elapsed):
    try:
        server = parsedate_to_datetime(value)
        if server.tzinfo is None or server.utcoffset() is None:
            raise ValueError()
        server_epoch = server.timestamp()
    except (TypeError, ValueError, OverflowError) as exc:
        raise InvalidData('HFM_CLOCK_RESPONSE_INVALID') from exc
    if after < before or not 0 <= elapsed <= 2 or abs(after - before - elapsed) > .25:
        raise InvalidData('HFM_HOST_CLOCK_DRIFT_OR_DELAY')
    # HTTP Date has whole-second precision. The true server time represented by
    # the header is conservatively within [D, D+1) during [before, after].
    low = server_epoch - after
    high = server_epoch + 1 - before
    offset = (low + high) / 2
    uncertainty = (high - low) / 2
    if not all(map(math.isfinite, (offset, uncertainty))) or abs(offset) + uncertainty > 2:
        raise InvalidData('HFM_HOST_CLOCK_UNCERTAIN')
    return offset, uncertainty


def _measure_https():
    observations = []
    for host, url in HTTPS_SOURCES:
        request = Request(url + '?sentinelfx_clock=' + str(time.time_ns()),
                          headers={'Cache-Control': 'no-cache', 'Pragma': 'no-cache',
                                   'User-Agent': 'SentinelFX-read-only-clock/1'}, method='GET')
        before, mono = time.time(), time.monotonic()
        try:
            with urlopen(request, timeout=3) as response:
                if response.geturl().split('/')[2].split(':')[0].lower() != host:
                    raise InvalidData('HFM_CLOCK_RESPONSE_MISMATCH')
                header = response.headers.get('Date')
        except OSError as exc:
            raise InvalidData('HFM_CLOCK_SOURCE_UNAVAILABLE') from exc
        after = time.time()
        observations.append(decode_https_date(header, before, after, time.monotonic() - mono))
    if max(v[0] for v in observations) - min(v[0] for v in observations) > 1:
        raise InvalidData('HFM_CLOCK_SOURCES_DISAGREE')
    return {'status': 'MEASURED', 'sources': [item[0] for item in HTTPS_SOURCES],
            'utc': time.time(), 'monotonic': time.monotonic(),
            'uncertainty_seconds': max(abs(offset) + error for offset, error in observations),
            'authentication': 'TLS_HTTPS_DATE_TWO_SOURCES'}


def measure():
    try:
        return _measure_ntp()
    except InvalidData as exc:
        if str(exc) != 'HFM_CLOCK_SOURCE_UNAVAILABLE':
            raise
        return _measure_https()


if __name__ == '__main__':
    import json
    try:
        print(json.dumps(measure(), allow_nan=False))
    except InvalidData as exc:
        print(json.dumps({'error': str(exc)}))
