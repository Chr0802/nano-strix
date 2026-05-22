# Authentication & JWT Analysis Guide

## Detection
- Check JWT signature verification (alg:none attacks)
- Look for hardcoded secrets/keys
- Check session token generation randomness
- Verify password hashing strength (bcrypt, argon2)

## Common Patterns
- jwt.decode(token, verify=False)
- SECRET_KEY = "hardcoded-secret"
- hashlib.md5(password).hexdigest()

## Recommendations
- Always verify JWT signatures
- Use strong secret keys from env vars
- Use bcrypt/argon2 for password hashing
