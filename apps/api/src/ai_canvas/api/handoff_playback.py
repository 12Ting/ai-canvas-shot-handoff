"""Narrow browser grants for native playback and streamed ZIP downloads."""

import hmac
import re
import secrets
import time

from fastapi import Request, Response

_PATH = re.compile(r"/api/v1/projects/[0-9a-f-]{36}/handoff/(?:play/[0-9a-f]{64}|export)")
_COOKIE = "handoff_play"
_TTL = 4 * 60 * 60


class PlaybackAccess:
    def __init__(self):
        self.secret = secrets.token_bytes(32)

    def _sign(self, path, deadline):
        return hmac.digest(self.secret, f"{path}|{deadline}".encode(), "sha256").hex()

    def grant(self, request: Request, response: Response, *, ttl=_TTL):
        path = request.url.path
        target = path + ("?" + request.url.query if request.url.query else "")
        deadline = str(int(time.time()) + ttl)
        response.set_cookie(
            _COOKIE,
            deadline + "." + self._sign(target, deadline),
            max_age=ttl,
            path=path,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )
        return {"url": target}

    def allows(self, request: Request):
        path = request.url.path
        if request.method != "GET" or not _PATH.fullmatch(path):
            return False
        value = request.cookies.get(_COOKIE, "")
        if not re.fullmatch(r"[0-9]{1,12}\.[0-9a-f]{64}", value):
            return False
        deadline, signature = value.split(".")
        target = path + ("?" + request.url.query if request.url.query else "")
        return int(deadline) > time.time() and hmac.compare_digest(signature, self._sign(target, deadline))
