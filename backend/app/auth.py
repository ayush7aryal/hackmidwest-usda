"""Short-lived signed demo sessions without adding a sessions table."""

import hashlib
import hmac
import time

from fastapi import HTTPException

SESSION_SECONDS = 8 * 60 * 60


def issue_token(emp_id: int, secret: str) -> str:
    """Sign a manager ID and expiry; no passwords or personal data enter tokens."""
    payload = f"{emp_id}:{int(time.time()) + SESSION_SECONDS}"
    signature = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def read_token(token: str, secret: str) -> int:
    """Reject modified and expired tokens before querying current manager access."""
    try:
        employee, expiry, signature = token.split(":")
        payload = f"{employee}:{expiry}"
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, expected) or int(expiry) <= time.time():
            raise ValueError("Invalid session")
        return int(employee)
    except (ValueError, TypeError):
        raise HTTPException(status_code=401, detail="Invalid or expired session") from None
