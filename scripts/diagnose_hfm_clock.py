#!/usr/bin/env python3
"""Print and optionally save redacted fixed-source clock diagnostics."""
import argparse
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.clock_diagnostics import diagnose, dumps


def _save(path, text):
    destination = Path(path).resolve(); temporary = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=destination.parent,
                                         delete=False) as stream:
            temporary = Path(stream.name); stream.write(text); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, destination)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()


def run(report_file=None):
    result = diagnose()
    for item in result['sources']:
        print('source:', item['source'])
        print('method:', item['method'])
        print('status:', item['status'])
        if item['method'] == 'HTTPS_DATE':
            print('http_status:', item['http_status'] if item['http_status'] is not None else 'unavailable')
        print('elapsed_seconds:', item['elapsed_seconds'] if item['elapsed_seconds'] is not None else 'unavailable')
    print('normalization_attempted: false')
    print('RESULT:', result['result'])
    print('No order was sent.')
    if report_file:
        try:
            _save(report_file, dumps(result))
            print('Redacted clock report saved: YES')
        except OSError:
            print('RESULT: BLOCKED_NO_TRADE')
            print('REASON: CLOCK_DIAGNOSTIC_REPORT_SAVE_FAILED')
            return 1
    return 0 if result['result'].startswith('PASS') else 1


def main():
    parser = argparse.ArgumentParser(description='Redacted HFM clock-source diagnostics')
    parser.add_argument('--report-file')
    args = parser.parse_args()
    return run(args.report_file)


if __name__ == '__main__':
    raise SystemExit(main())
