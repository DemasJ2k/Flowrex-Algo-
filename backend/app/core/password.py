import bcrypt

# 14 rounds is 2027-ready; ~4x slower than default 12 but barely noticeable at login.
# Existing hashes with lower rounds continue to verify (bcrypt stores rounds in the hash).
BCRYPT_ROUNDS = 14


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=BCRYPT_ROUNDS)).decode()


def verify_password(password: str, hashed: str) -> bool:
    return bcrypt.checkpw(password.encode(), hashed.encode())
