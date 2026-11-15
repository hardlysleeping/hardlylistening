#!/usr/bin/python3
"""Read-only public deployment smoke check; creates no users or parties."""
import argparse
import json
import urllib.error
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--origin', default='https://music.hardlysleep.ing')
    args = parser.parse_args()
    failures = 0
    checks = [('/api/health', 200), ('/party', 200), ('/party.js', 200),
              ('/party.css', 200), ('/api/party', 401), ('/api/library', 401)]
    for path, expected in checks:
        # Cloudflare rejects Python's generic urllib agent with error 1010.
        req = urllib.request.Request(args.origin.rstrip('/') + path,
                                     headers={'User-Agent': 'HardlyListening-HealthCheck/1.0'})
        try:
            with urllib.request.urlopen(req, timeout=20) as response:
                status = response.status
                body = response.read()
        except urllib.error.HTTPError as error:
            status, body = error.code, error.read()
        except (OSError, urllib.error.URLError) as error:
            print(f'FAIL {path}: {error}')
            failures += 1
            continue
        okay = status == expected
        if path == '/api/health' and okay:
            try:
                health = json.loads(body)
                okay = health.get('ok') is True
                print(f"Health version: {health.get('version', 'unknown')}")
            except (ValueError, AttributeError):
                okay = False
        print(f"{'PASS' if okay else 'FAIL'} {path}: {status} (expected {expected})")
        failures += not okay
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
