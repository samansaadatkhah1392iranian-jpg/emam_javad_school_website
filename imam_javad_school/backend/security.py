import hashlib, hmac, secrets, base64

def hash_password(password, salt=None):
    salt = salt or secrets.token_bytes(16)
    rounds = 240000
    digest = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, rounds)
    return 'pbkdf2_sha256${}${}${}'.format(rounds, base64.urlsafe_b64encode(salt).decode(), base64.urlsafe_b64encode(digest).decode())

def verify_password(password, encoded):
    try:
        algo, rounds, salt, expected = encoded.split('$')
        if algo != 'pbkdf2_sha256': return False
        salt_b = base64.urlsafe_b64decode(salt.encode())
        got = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt_b, int(rounds))
        return hmac.compare_digest(base64.urlsafe_b64encode(got).decode(), expected)
    except Exception:
        return False

def token_hash(token):
    return hashlib.sha256(token.encode()).hexdigest()
