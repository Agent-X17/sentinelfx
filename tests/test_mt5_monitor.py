import json
import tempfile
import unittest
from pathlib import Path
from engine.mt5_monitor import read_monitor


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'status.json'
        self.data = dict(schema=1, generated_at=1000, demo=True, connected=True,
                         balance=500, equity=500, free_margin=500, positions=0,
                         orders=0, currency='USD', symbol='EURUSD', tick_available=True,
                         bid=1.1, ask=1.1002, tick_server_time=1000, password='must-not-leak')

    def read(self):
        self.path.write_text(json.dumps(self.data))
        return read_monitor(self.path, now=1001)

    def test_valid_telemetry_is_read_only_and_allowlisted(self):
        result = self.read()
        self.assertTrue(result['ok'])
        self.assertFalse(result['order_submission_available'])
        self.assertEqual(result['data']['balance'], 500)
        self.assertNotIn('password', result['data'])

    def test_stale_future_live_disconnected_invalid_fail_closed(self):
        for key, value in [('generated_at', 900), ('generated_at', 1100),
                           ('demo', False), ('connected', False), ('balance', float('nan')),
                           ('bid', -1), ('ask', 0), ('currency', None)]:
            original = self.data[key]
            self.data[key] = value
            self.assertFalse(self.read()['ok'], (key, value))
            self.data[key] = original

    def test_missing_partial_oversized(self):
        self.assertEqual(read_monitor(self.path)['code'], 'MT5_MONITOR_WAITING')
        for text in ('{', '[]', 'x' * 17000):
            self.path.write_text(text)
            self.assertFalse(read_monitor(self.path)['ok'])
