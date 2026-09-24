"""Best-effort credential filtering; arbitrary free text is not a secret vault."""
import re


def redact(value, secrets=()):
    known = tuple(s for s in secrets if isinstance(s, str) and s)
    def walk(item):
        if isinstance(item, dict):
            result = {}
            for key, child in item.items():
                normalized = re.sub(r'[^a-z0-9]', '', str(key).lower())
                if any(word in normalized for word in ('secret', 'password', 'token', 'authorization', 'apikey', 'privatekey', 'credential')):
                    result[key] = '[REDACTED]'
                else:
                    result[key] = walk(child)
            return result
        if isinstance(item, (list, tuple)):
            return [walk(child) for child in item]
        if isinstance(item, str):
            for secret in known:
                item = item.replace(secret, '[REDACTED]')
        return item
    return walk(value)
