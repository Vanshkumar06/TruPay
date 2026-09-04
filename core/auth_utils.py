import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote


def generate_totp_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode('utf-8').replace('=', '')


def _totp_at(secret, for_time, digits=6, interval=30):
    normalized = secret.upper()
    padding = '=' * ((8 - len(normalized) % 8) % 8)
    key = base64.b32decode(normalized + padding, casefold=True)
    counter = int(for_time // interval)
    msg = struct.pack('>Q', counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10 ** digits)).zfill(digits)


def verify_totp(secret, code, window=1):
    if not secret or not code:
        return False
    now = time.time()
    code = str(code).strip()
    for step in range(-window, window + 1):
        if hmac.compare_digest(_totp_at(secret, now + (step * 30)), code):
            return True
    return False


def build_otpauth_uri(username, secret, issuer='TruPay'):
    label = quote(f'{issuer}:{username}')
    return f'otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}'


def generate_backup_codes(count=6):
    return [secrets.token_hex(4).upper() for _ in range(count)]
