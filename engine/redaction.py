"""Best-effort credential filtering; arbitrary free text is not a secret vault."""
import re


def redact(value, secrets=()):
    known = tuple(s for s in secrets if isinstance(s, str) and s)
    def clean(text):
        for secret in sorted(known, key=len, reverse=True):
            text = text.replace(secret, '[REDACTED]')
        return text
    def walk(item, depth=0):
        if depth > 32:
            return '[REDACTED: nesting limit]'
        if isinstance(item, dict):
            result = {}
            for key, child in item.items():
                normalized = re.sub(r'[^a-z0-9]', '', str(key).lower())
                if any(word in normalized for word in ('secret', 'password', 'token', 'authorization', 'apikey', 'privatekey', 'credential', 'accountnumber')) or normalized in ('login','server','brokerserver'):
                    result[clean(str(key))] = '[REDACTED]'
                else:
                    result[clean(str(key))] = walk(child, depth+1)
            return result
        if isinstance(item, (list, tuple)):
            return [walk(child, depth+1) for child in item]
        if isinstance(item, str):
            item = clean(item)
        return item
    return walk(value)
