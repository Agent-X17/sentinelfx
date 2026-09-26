"""Redacted, CLI-only external-clock diagnostics.

This module never changes clocks, networking, MT5, policy, or application state.
Only fixed source names and allowlisted status fields leave this boundary.
"""
from datetime import datetime, timezone
import json
import math
import socket
import ssl
import struct
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, getproxies, urlopen

from .domain import InvalidData
from .readonly_clock import EPOCH, decode, decode_https_date

NTP_SOURCES = ('time.windows.com', 'time.cloudflare.com')
HTTPS_SOURCES = (
    ('www.cloudflare.com', 'https://www.cloudflare.com/cdn-cgi/trace'),
    ('www.microsoft.com', 'https://www.microsoft.com/favicon.ico'),
)
NTP_STATUSES = frozenset(('PASS', 'DNS_FAILURE', 'TIMEOUT', 'SOCKET_FAILURE',
                          'RESPONSE_INVALID', 'CLOCK_DISAGREEMENT'))
HTTPS_STATUSES = frozenset(('PASS', 'DNS_FAILURE', 'PROXY_FAILURE', 'TLS_FAILURE',
                            'CERTIFICATE_FAILURE', 'TIMEOUT', 'HTTP_STATUS',
                            'DATE_HEADER_MISSING', 'DATE_HEADER_INVALID',
                            'CLOCK_DISAGREEMENT'))


def _elapsed(start, monotonic):
    try:
        value = monotonic() - start
        return round(value, 6) if math.isfinite(value) and value >= 0 else None
    except Exception:
        return None


def _base(source, method):
    value = {'source': source, 'method': method, 'status': None,
             'elapsed_seconds': None}
    if method == 'HTTPS_DATE':
        value['http_status'] = None
    return value


def _ntp_packet(now):
    seconds = int(now) + EPOCH
    sent = struct.pack('!II', seconds, int((now % 1) * 2**32))
    request = bytearray(48); request[0] = 0x23; request[40:48] = sent
    return request, sent


def probe_ntp(source, resolver=socket.getaddrinfo, socket_factory=socket.socket,
              wall=time.time, monotonic=time.monotonic):
    result = _base(source, 'NTP_UDP'); started = monotonic()
    try:
        # Match the production clock reader's explicit IPv4 UDP transport.
        addresses = resolver(source, 123, family=socket.AF_INET,
                             type=socket.SOCK_DGRAM)
    except socket.gaierror:
        result['status'] = 'DNS_FAILURE'
        result['elapsed_seconds'] = _elapsed(started, monotonic)
        return result
    except OSError:
        result['status'] = 'DNS_FAILURE'
        result['elapsed_seconds'] = _elapsed(started, monotonic)
        return result
    if not addresses:
        result['status'] = 'DNS_FAILURE'
        result['elapsed_seconds'] = _elapsed(started, monotonic)
        return result
    family, socktype, protocol, _, address = addresses[0]
    try:
        with socket_factory(family, socktype, protocol) as connection:
            connection.settimeout(2)
            connection.connect(address)
            before, mono = wall(), monotonic()
            request, sent = _ntp_packet(before)
            connection.send(request)
            packet = connection.recv(512)
            after = wall()
            offset, uncertainty = decode(packet, sent, before, after, monotonic() - mono)
    except (socket.timeout, TimeoutError):
        result['status'] = 'TIMEOUT'
    except InvalidData as exc:
        result['status'] = ('CLOCK_DISAGREEMENT' if str(exc) in
                            ('HFM_HOST_CLOCK_DRIFT_OR_DELAY',
                             'HFM_HOST_CLOCK_UNCERTAIN') else
                            'RESPONSE_INVALID')
    except OSError:
        result['status'] = 'SOCKET_FAILURE'
    except Exception:
        result['status'] = 'SOCKET_FAILURE'
    else:
        result.update(status='PASS', _offset=offset, _uncertainty=uncertainty)
    result['elapsed_seconds'] = _elapsed(started, monotonic)
    return result


def _url_reason_status(reason, proxy_detected):
    if isinstance(reason, socket.gaierror):
        return 'DNS_FAILURE'
    if isinstance(reason, (socket.timeout, TimeoutError)):
        return 'TIMEOUT'
    if isinstance(reason, ssl.SSLCertVerificationError):
        return 'CERTIFICATE_FAILURE'
    if isinstance(reason, ssl.SSLError):
        return 'TLS_FAILURE'
    return 'PROXY_FAILURE' if proxy_detected else 'TLS_FAILURE'


def probe_https(source, url, resolver=socket.getaddrinfo, opener=urlopen,
                proxy_detector=getproxies, wall=time.time, monotonic=time.monotonic):
    result = _base(source, 'HTTPS_DATE'); started = monotonic()
    try:
        addresses = resolver(source, 443, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError):
        result['status'] = 'DNS_FAILURE'
        result['elapsed_seconds'] = _elapsed(started, monotonic)
        return result
    if not addresses:
        result['status'] = 'DNS_FAILURE'
        result['elapsed_seconds'] = _elapsed(started, monotonic)
        return result
    try:
        proxy_detected = bool(proxy_detector())
    except Exception:
        proxy_detected = False
    request = Request(url + '?sentinelfx_clock_diagnostic=' + str(time.time_ns()),
                      headers={'Cache-Control': 'no-cache', 'Pragma': 'no-cache',
                               'User-Agent': 'SentinelFX-read-only-clock-diagnostic/1'},
                      method='GET')
    before, mono = wall(), monotonic()
    try:
        with opener(request, timeout=3) as response:
            result['http_status'] = int(response.status)
            final_host = response.geturl().split('/')[2].split(':')[0].lower()
            if final_host != source or not 200 <= response.status < 400:
                result['status'] = 'HTTP_STATUS'
            else:
                header = response.headers.get('Date')
                if header is None:
                    result['status'] = 'DATE_HEADER_MISSING'
                else:
                    after = wall()
                    try:
                        offset, uncertainty = decode_https_date(
                            header, before, after, monotonic() - mono)
                    except InvalidData as exc:
                        result['status'] = ('CLOCK_DISAGREEMENT'
                                            if str(exc) in
                                            ('HFM_HOST_CLOCK_DRIFT_OR_DELAY',
                                             'HFM_HOST_CLOCK_UNCERTAIN')
                                            else 'DATE_HEADER_INVALID')
                    else:
                        result.update(status='PASS', _offset=offset,
                                      _uncertainty=uncertainty)
    except HTTPError as exc:
        result['http_status'] = int(exc.code) if type(exc.code) is int else None
        result['status'] = 'PROXY_FAILURE' if exc.code == 407 else 'HTTP_STATUS'
    except URLError as exc:
        result['status'] = _url_reason_status(exc.reason, proxy_detected)
    except ssl.SSLCertVerificationError:
        result['status'] = 'CERTIFICATE_FAILURE'
    except ssl.SSLError:
        result['status'] = 'TLS_FAILURE'
    except (socket.timeout, TimeoutError):
        result['status'] = 'TIMEOUT'
    except OSError:
        result['status'] = 'PROXY_FAILURE' if proxy_detected else 'TLS_FAILURE'
    except Exception:
        result['status'] = 'TLS_FAILURE'
    result['elapsed_seconds'] = _elapsed(started, monotonic)
    return result


def _disagreement(results, sources, limit):
    selected = [item for item in results if item['source'] in sources]
    if len(selected) == 2 and all(item['status'] == 'PASS' for item in selected):
        if abs(selected[0]['_offset'] - selected[1]['_offset']) > limit:
            for item in selected:
                item['status'] = 'CLOCK_DISAGREEMENT'


def _safe(item):
    keys = ('source', 'method', 'status', 'http_status', 'elapsed_seconds')
    return {key: item[key] for key in keys if key in item}


def diagnose(ntp_probe=probe_ntp, https_probe=probe_https, monotonic=time.monotonic):
    started = monotonic()
    results = [ntp_probe(source) for source in NTP_SOURCES]
    results.extend(https_probe(source, url) for source, url in HTTPS_SOURCES)
    _disagreement(results, NTP_SOURCES, .25)
    _disagreement(results, tuple(item[0] for item in HTTPS_SOURCES), 1)
    ntp_ok = all(item['status'] == 'PASS' for item in results[:2])
    https_ok = all(item['status'] == 'PASS' for item in results[2:])
    return {'schema': 'sentinelfx.clock-source-diagnostic.v1',
            'generated_at': datetime.now(timezone.utc).isoformat(),
            'sources': [_safe(item) for item in results],
            'result': 'PASS_READ_ONLY_CLOCK_EVIDENCE' if ntp_ok or https_ok
                      else 'BLOCKED_NO_TRADE',
            'normalization_attempted': False, 'order_sent': False,
            'total_elapsed_seconds': _elapsed(started, monotonic)}


def environment_probe(diagnostic=None, resolver=socket.getaddrinfo,
                      proxy_detector=getproxies, monotonic=time.monotonic):
    started = monotonic(); diagnostic = diagnostic or diagnose()
    resolved = {}
    for source in NTP_SOURCES + tuple(item[0] for item in HTTPS_SOURCES):
        try:
            resolved[source] = bool(resolver(source, 0))
        except (socket.gaierror, OSError):
            resolved[source] = False
    try:
        proxy_detected = bool(proxy_detector())
    except Exception:
        proxy_detected = False
    by_source = {item['source']: item for item in diagnostic['sources']}
    sources = []
    for source in NTP_SOURCES:
        sources.append({'source': source, 'dns_resolved': resolved[source],
                        'udp_ntp_attempted': True,
                        'udp_ntp_outcome': by_source[source]['status']})
    for source, _ in HTTPS_SOURCES:
        status = by_source[source]['status']
        sources.append({'source': source, 'dns_resolved': resolved[source],
                        'udp_ntp_attempted': False,
                        'https_proxy_auto_detection_in_use': True,
                        'https_proxy_configuration_detected': proxy_detected,
                        'certificate_validation': ('PASS' if status in
                            ('PASS', 'HTTP_STATUS', 'DATE_HEADER_MISSING',
                             'DATE_HEADER_INVALID', 'CLOCK_DISAGREEMENT') else
                            'FAILED' if status in ('TLS_FAILURE', 'CERTIFICATE_FAILURE')
                            else 'NOT_REACHED'),
                        'https_endpoint_reached': status in
                            ('PASS', 'HTTP_STATUS', 'DATE_HEADER_MISSING',
                             'DATE_HEADER_INVALID', 'CLOCK_DISAGREEMENT'),
                        'https_outcome': status})
    return {'schema': 'sentinelfx.clock-environment-probe.v1',
            'sources': sources, 'result': diagnostic['result'],
            'normalization_attempted': False, 'order_sent': False,
            'total_elapsed_seconds': _elapsed(started, monotonic)}


def dumps(value):
    """Only serialized allowlisted structures; never an exception or environment."""
    return json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n'
