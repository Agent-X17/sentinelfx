"""Deterministic redaction/status tests; no DNS, sockets or HTTP are used."""
import io
import json
import socket
import ssl
from contextlib import redirect_stdout
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
import unittest
from unittest.mock import patch

from engine.clock_diagnostics import (HTTPS_SOURCES, NTP_SOURCES, diagnose, dumps,
    environment_probe, probe_https, probe_ntp)
from engine.domain import InvalidData
from scripts import diagnose_hfm_clock
from scripts import verify_mt5_readonly


class Clock:
    def __init__(self, start=1000, step=.01): self.value=start-step;self.step=step
    def __call__(self): self.value += self.step;return self.value


class FakeSocket:
    def __init__(self, failure=None): self.failure=failure
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def settimeout(self, value): pass
    def connect(self, value): pass
    def send(self, value): pass
    def recv(self, value):
        if self.failure: raise self.failure
        return b'x'*48


class Response:
    def __init__(self, source='www.cloudflare.com', status=200, date='Thu, 01 Jan 1970 00:16:40 GMT'):
        self.source=source;self.status=status;self.headers={} if date is None else {'Date':date}
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def geturl(self): return 'https://' + self.source + '/fixed'


def resolver(*args, **kwargs):
    return [(socket.AF_INET, socket.SOCK_DGRAM, 17, '', ('192.0.2.1', args[1]))]


class NTPStatusTests(unittest.TestCase):
    def probe(self, **kwargs):
        return probe_ntp('time.windows.com', resolver=kwargs.pop('resolver', resolver),
                          socket_factory=kwargs.pop('socket_factory', lambda *a: FakeSocket()),
                          wall=Clock(), monotonic=Clock(), **kwargs)

    def test_pass(self):
        with patch('engine.clock_diagnostics.decode', return_value=(0, .01)):
            self.assertEqual(self.probe()['status'], 'PASS')

    def test_dns_failure(self):
        def fail(*args, **kwargs): raise socket.gaierror('private DNS detail')
        self.assertEqual(self.probe(resolver=fail)['status'], 'DNS_FAILURE')

    def test_timeout(self):
        self.assertEqual(self.probe(socket_factory=lambda *a: FakeSocket(socket.timeout()))['status'], 'TIMEOUT')

    def test_socket_failure(self):
        def fail(*args): raise OSError('private socket detail')
        self.assertEqual(self.probe(socket_factory=fail)['status'], 'SOCKET_FAILURE')

    def test_response_invalid(self):
        with patch('engine.clock_diagnostics.decode', side_effect=InvalidData('secret invalid detail')):
            self.assertEqual(self.probe()['status'], 'RESPONSE_INVALID')

    def test_clock_disagreement_from_local_bound(self):
        with patch('engine.clock_diagnostics.decode',
                   side_effect=InvalidData('HFM_HOST_CLOCK_UNCERTAIN')):
            self.assertEqual(self.probe()['status'], 'CLOCK_DISAGREEMENT')

    def test_clock_drift_from_local_bound(self):
        with patch('engine.clock_diagnostics.decode',
                   side_effect=InvalidData('HFM_HOST_CLOCK_DRIFT_OR_DELAY')):
            self.assertEqual(self.probe()['status'], 'CLOCK_DISAGREEMENT')

    def test_clock_disagreement(self):
        offsets=iter((0, 1))
        def ntp(source): return {'source':source,'method':'NTP_UDP','status':'PASS',
            'elapsed_seconds':.1,'_offset':next(offsets),'_uncertainty':.01}
        def https(source,url): return {'source':source,'method':'HTTPS_DATE','status':'DNS_FAILURE',
            'http_status':None,'elapsed_seconds':.1}
        result=diagnose(ntp,https,Clock())
        self.assertEqual([x['status'] for x in result['sources'][:2]], ['CLOCK_DISAGREEMENT']*2)


class HTTPSStatusTests(unittest.TestCase):
    source,url=HTTPS_SOURCES[0]
    def probe(self, opener=lambda *a,**k: Response(), resolver_value=resolver, proxy=lambda:{}):
        return probe_https(self.source,self.url,resolver=resolver_value,opener=opener,
                           proxy_detector=proxy,wall=Clock(),monotonic=Clock())

    def test_pass(self):
        with patch('engine.clock_diagnostics.decode_https_date', return_value=(0,.5)):
            value=self.probe()
        self.assertEqual((value['status'],value['http_status']),('PASS',200))

    def test_dns_failure(self):
        def fail(*a,**k): raise socket.gaierror('secret DNS')
        self.assertEqual(self.probe(resolver_value=fail)['status'],'DNS_FAILURE')

    def test_proxy_failure(self):
        def fail(*a,**k): raise HTTPError(self.url,407,'secret proxy',{},None)
        self.assertEqual(self.probe(opener=fail,proxy=lambda:{'https':'secret'})['status'],'PROXY_FAILURE')

    def test_tls_failure(self):
        def fail(*a,**k): raise ssl.SSLError('secret tls')
        self.assertEqual(self.probe(opener=fail)['status'],'TLS_FAILURE')

    def test_certificate_failure(self):
        def fail(*a,**k): raise ssl.SSLCertVerificationError(1,'secret certificate')
        self.assertEqual(self.probe(opener=fail)['status'],'CERTIFICATE_FAILURE')

    def test_timeout(self):
        def fail(*a,**k): raise socket.timeout('secret timeout')
        self.assertEqual(self.probe(opener=fail)['status'],'TIMEOUT')

    def test_http_status(self):
        def fail(*a,**k): raise HTTPError(self.url,503,'secret body',{},None)
        value=self.probe(opener=fail)
        self.assertEqual((value['status'],value['http_status']),('HTTP_STATUS',503))

    def test_date_header_missing(self):
        value=self.probe(opener=lambda *a,**k:Response(date=None))
        self.assertEqual(value['status'],'DATE_HEADER_MISSING')

    def test_date_header_invalid(self):
        value=self.probe(opener=lambda *a,**k:Response(date='secret invalid date'))
        self.assertEqual(value['status'],'DATE_HEADER_INVALID')

    def test_clock_disagreement_from_local_bound(self):
        with patch('engine.clock_diagnostics.decode_https_date',
                   side_effect=InvalidData('HFM_HOST_CLOCK_UNCERTAIN')):
            self.assertEqual(self.probe()['status'],'CLOCK_DISAGREEMENT')

    def test_clock_drift_from_local_bound(self):
        with patch('engine.clock_diagnostics.decode_https_date',
                   side_effect=InvalidData('HFM_HOST_CLOCK_DRIFT_OR_DELAY')):
            self.assertEqual(self.probe()['status'],'CLOCK_DISAGREEMENT')

    def test_clock_disagreement_between_sources(self):
        def ntp(source): return {'source':source,'method':'NTP_UDP','status':'TIMEOUT','elapsed_seconds':.1}
        offsets=iter((0,2))
        def https(source,url): return {'source':source,'method':'HTTPS_DATE','status':'PASS',
            'http_status':200,'elapsed_seconds':.1,'_offset':next(offsets),'_uncertainty':.5}
        result=diagnose(ntp,https,Clock())
        self.assertEqual([x['status'] for x in result['sources'][2:]], ['CLOCK_DISAGREEMENT']*2)


class DiagnosticOutputTests(unittest.TestCase):
    def diagnostic(self):
        sources=[]
        for source in NTP_SOURCES:
            sources.append({'source':source,'method':'NTP_UDP','status':'TIMEOUT','elapsed_seconds':2.0})
        for source,_ in HTTPS_SOURCES:
            sources.append({'source':source,'method':'HTTPS_DATE','status':'CERTIFICATE_FAILURE',
                            'http_status':None,'elapsed_seconds':.2})
        return {'schema':'sentinelfx.clock-source-diagnostic.v1','generated_at':'fixed',
                'sources':sources,'result':'BLOCKED_NO_TRADE','normalization_attempted':False,
                'order_sent':False,'total_elapsed_seconds':4.4}

    def test_redaction_and_exact_allowlist(self):
        text=dumps(self.diagnostic())
        for secret in ('account-login','broker.example-demo','terminal64.exe','proxy.internal',
                       'password','cookie','192.0.2.1','certificate body','raw exception'):
            self.assertNotIn(secret,text)
        allowed={'schema','generated_at','sources','result','normalization_attempted',
                 'order_sent','total_elapsed_seconds'}
        self.assertEqual(set(json.loads(text)),allowed)

    def test_environment_probe_safe_fields(self):
        value=environment_probe(self.diagnostic(),resolver=lambda *a,**k:[('hidden-ip',)],
                                proxy_detector=lambda:{'https':'secret-proxy'},monotonic=Clock())
        self.assertEqual(len(value['sources']),4)
        self.assertTrue(value['sources'][0]['udp_ntp_attempted'])
        self.assertFalse(value['sources'][2]['udp_ntp_attempted'])
        self.assertTrue(value['sources'][2]['https_proxy_configuration_detected'])
        self.assertEqual(value['sources'][2]['certificate_validation'],'FAILED')
        text=dumps(value)
        self.assertNotIn('hidden-ip',text);self.assertNotIn('secret-proxy',text)

    def test_cli_output_is_redacted_and_blocked(self):
        stream=io.StringIO()
        with patch.object(diagnose_hfm_clock,'diagnose',return_value=self.diagnostic()),redirect_stdout(stream):
            code=diagnose_hfm_clock.run()
        self.assertEqual(code,1)
        output=stream.getvalue()
        self.assertIn('RESULT: BLOCKED_NO_TRADE',output)
        self.assertIn('normalization_attempted: false',output)
        self.assertIn('No order was sent.',output)

    def test_verifier_clock_mode_does_not_construct_mt5(self):
        settings = SimpleNamespace(
            demo_trade_proposals_enabled=False,
            demo_trade_proposal_kill_switch=True,
            live_execution_enabled=False,
            mode='SIMULATION',
            mt5_diagnostic_mode='real',
        )
        with patch.object(verify_mt5_readonly.Settings, 'from_env', return_value=settings), \
             patch.object(diagnose_hfm_clock, 'run', return_value=1) as diagnostic_run, \
             patch.object(verify_mt5_readonly, 'IsolatedMT5Service') as mt5_service, \
             patch('sys.argv', ['verify', '--clock-diagnostics']):
            self.assertEqual(verify_mt5_readonly.main(), 1)
        diagnostic_run.assert_called_once_with(None)
        mt5_service.assert_not_called()

    def test_failure_details_are_never_serialized(self):
        secrets = ('private DNS detail', 'private socket detail', 'secret tls',
                   'secret certificate', 'secret proxy')
        def dns_fail(*args, **kwargs):
            raise socket.gaierror(secrets[0])
        def tls_fail(*args, **kwargs):
            raise ssl.SSLError(secrets[2])
        values = [probe_ntp(NTP_SOURCES[0], resolver=dns_fail),
                  probe_https(HTTPS_SOURCES[0][0], HTTPS_SOURCES[0][1],
                              resolver=resolver, opener=tls_fail,
                              proxy_detector=lambda: {'https': secrets[4]})]
        text = dumps({'sources': values})
        for secret in secrets:
            self.assertNotIn(secret, text)
