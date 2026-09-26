#!/usr/bin/env python3
"""Redacted environment facts for the fixed clock endpoints."""
import argparse
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from engine.clock_diagnostics import dumps, environment_probe


def main():
    parser = argparse.ArgumentParser(description='Redacted clock environment probe')
    parser.add_argument('--report-file')
    args = parser.parse_args()
    # environment_probe starts its timer before it runs all four network checks,
    # so this is the elapsed time for the complete probe rather than formatting.
    value = environment_probe(); text = dumps(value)
    print(text, end='')
    if args.report_file:
        destination = Path(args.report_file).resolve(); temporary = None
        try:
            with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=destination.parent,
                                             delete=False) as stream:
                temporary = Path(stream.name); stream.write(text); stream.flush(); os.fsync(stream.fileno())
            os.replace(temporary, destination)
        except OSError:
            print('RESULT: BLOCKED_NO_TRADE')
            print('REASON: CLOCK_ENVIRONMENT_REPORT_SAVE_FAILED')
            return 1
        finally:
            if temporary is not None and temporary.exists():
                temporary.unlink()
    return 0 if value['result'].startswith('PASS') else 1


if __name__ == '__main__':
    raise SystemExit(main())
