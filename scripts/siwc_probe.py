"""Isolated ChatGPT plan sign-in probe. Never submits inference or API-key requests.

Run with --discovery-only for a non-interactive endpoint check. Normal mode opens
the system browser and requires the person to sign in and approve permissions.
Tokens stay in memory; a received refresh token is revoked after the model query.
This is a temporary diagnostic, not Manga Blendy's production auth implementation.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import html
import json
import os
import secrets
import socket
import sys
import threading
import time
import uuid
import webbrowser
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / ".work" / "siwc-probe"
sys.path.insert(0, str(ROOT / ".work" / "siwc-deps"))
ISSUER = "https://auth.openai.com"
RESOURCE = "https://api.openai.com/v1"
AUTHORIZE = ISSUER + "/api/accounts/authorize"
TOKEN = ISSUER + "/api/accounts/oauth/token"
SCOPES = "openid profile email offline_access resource.invoke chatgpt.tokens.use.direct"
CALLBACK_PATH = "/auth/callback"
SERVICE = "manga-blendy-siwc-probe"
SERVICE_VERSION = 2


def now():
    return datetime.now(timezone.utc).isoformat()


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


class ProbeError(Exception):
    pass


class Probe:
    def __init__(self):
        WORK.mkdir(parents=True, exist_ok=True)
        self.report = {"started_at": now(), "status": "starting", "checks": [],
                       "inference_requests": 0, "api_key_used": False,
                       "tokens_written_to_disk": False, "automatic_retries": 0}
        self.tokens = None
        self.client_id = None
        self.discovery = None
        self.done = False
        self.deadline = time.monotonic() + 600
        self.state = secrets.token_urlsafe(32)
        self.nonce = secrets.token_urlsafe(32)
        self.verifier = secrets.token_urlsafe(48)
        self.cookie = secrets.token_urlsafe(32)
        self.form_token = secrets.token_urlsafe(32)
        self.started_authorization = False

    def persist(self):
        save(WORK / "result.json", self.report)

    def request(self, label, url, *, form=None, bearer=None):
        headers = {"Accept": "application/json"}
        if bearer:
            headers["Authorization"] = "Bearer " + bearer
        data = None
        if form is not None:
            data = urlencode(form).encode("ascii")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        check = {"step": label, "time": now()}
        self.report["checks"].append(check)
        try:
            with urlopen(Request(url, data=data, headers=headers), timeout=30) as response:
                check["http_status"] = response.status
                check["request_id"] = response.headers.get("x-request-id")
                content = response.read(4_000_001)
                if len(content) > 4_000_000:
                    raise ProbeError("response_too_large")
                return json.loads(content) if content else None
        except HTTPError as exc:
            check["http_status"] = exc.code
            check["request_id"] = exc.headers.get("x-request-id")
            # Never record response bodies from authentication endpoints.
            raise ProbeError(label + "_http_" + str(exc.code)) from None
        except (URLError, TimeoutError, ValueError):
            check["error"] = "transport_or_format_error"
            raise ProbeError(label + "_transport_or_format_error") from None

    def preflight(self):
        self.discovery = self.request("oidc_discovery", ISSUER + "/.well-known/openid-configuration")
        if not isinstance(self.discovery, dict) or self.discovery.get("issuer") != ISSUER:
            raise ProbeError("discovery_issuer_invalid")
        for field, expected in (("authorization_endpoint", AUTHORIZE), ("token_endpoint", TOKEN)):
            if self.discovery.get(field) != expected:
                raise ProbeError("discovery_endpoint_changed")
        for field in ("jwks_uri", "revocation_endpoint"):
            value = self.discovery.get(field, "")
            if urlsplit(value).scheme != "https" or urlsplit(value).netloc != "auth.openai.com":
                raise ProbeError("discovery_" + field + "_invalid")
        self.jwks = self.request("public_jwks", self.discovery["jwks_uri"])
        if not isinstance(self.jwks, dict) or not self.jwks.get("keys"):
            raise ProbeError("jwks_invalid")
        self.report["discovery_validated"] = True
        self.persist()

    def authorization_url(self, redirect_uri):
        path = WORK / "host.json"
        host = json.loads(path.read_text("utf-8")) if path.exists() else {"ext_agent_host_id": uuid.uuid4().urn}
        if not path.exists():
            save(path, host)
        # The probe keeps no login tokens or account profile. Existing issued ID
        # is retained only to avoid creating another client on repeat tests.
        registration_path = WORK / "registration.json"
        self.registration = json.loads(registration_path.read_text("utf-8")) if registration_path.exists() else None
        params = {"client_id": self.registration["client_id"] if self.registration else "dynamic_agent_client",
                  "ext_agent_host_id": host["ext_agent_host_id"], "response_type": "code",
                  "redirect_uri": redirect_uri, "scope": SCOPES, "resource": RESOURCE,
                  "state": self.state, "nonce": self.nonce, "code_challenge_method": "S256",
                  "code_challenge": base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode()).digest()).decode().rstrip("=")}
        if not self.registration:
            params["agent_name_hint"] = "Manga Blendy"
        return AUTHORIZE + "?" + urlencode(params)

    def complete(self, params, redirect_uri):
        def single(key):
            values = params.get(key, [])
            if len(values) != 1 or not values[0]:
                raise ProbeError("callback_" + key + "_invalid")
            return values[0]
        if not self.started_authorization or time.monotonic() > self.deadline:
            raise ProbeError("authorization_expired")
        if not secrets.compare_digest(single("state"), self.state):
            raise ProbeError("callback_state_mismatch")
        if "error" in params:
            raise ProbeError("authorization_declined_or_failed")
        issued_id = single("client_id") if "client_id" in params else (self.registration or {}).get("client_id")
        if not issued_id or issued_id == "dynamic_agent_client":
            raise ProbeError("issued_client_missing")
        if self.registration and issued_id != self.registration["client_id"]:
            raise ProbeError("issued_client_mismatch")
        self.client_id = issued_id
        self.tokens = self.request("code_exchange", TOKEN, form={"grant_type": "authorization_code",
                                  "client_id": issued_id, "code": single("code"),
                                  "code_verifier": self.verifier, "redirect_uri": redirect_uri, "resource": RESOURCE})
        import jwt
        header = jwt.get_unverified_header(self.tokens["id_token"])
        if header.get("alg") not in {"RS256", "ES256"}:
            raise ProbeError("id_token_algorithm_invalid")
        keys = [k for k in self.jwks["keys"] if k.get("kid") == header.get("kid")]
        if len(keys) != 1:
            raise ProbeError("id_token_key_not_found")
        key = jwt.PyJWK.from_dict(keys[0], algorithm=header["alg"]).key
        identity = jwt.decode(self.tokens["id_token"], key, algorithms=[header["alg"]],
                              audience=issued_id, issuer=ISSUER, leeway=5,
                              options={"require": ["sub", "exp", "iat", "nonce"]})
        if not isinstance(identity["sub"], str) or not identity["sub"] or not secrets.compare_digest(identity["nonce"], self.nonce):
            raise ProbeError("id_token_identity_or_nonce_invalid")
        identity_hash = hashlib.sha256(identity["sub"].encode()).hexdigest()
        if self.registration and identity_hash != self.registration["subject_sha256"]:
            raise ProbeError("registered_account_mismatch")
        save(WORK / "registration.json", {"client_id": issued_id, "subject_sha256": identity_hash})
        self.report["identity_validated"] = True
        granted = set(self.tokens.get("scope", "").split())
        self.report["direct_scope_granted"] = "chatgpt.tokens.use.direct" in granted
        if not self.report["direct_scope_granted"]:
            raise ProbeError("plan_permission_not_granted")
        models = self.request("account_models", RESOURCE + "/models", bearer=self.tokens["access_token"])
        if not isinstance(models, dict) or not isinstance(models.get("models"), list):
            raise ProbeError("models_contract_invalid")
        self.report["models"] = [{"slug": m["slug"], "display_name": m["display_name"]}
                                  for m in models["models"] if m.get("visibility") == "list"]
        self.report["status"] = "model_list_succeeded"
        self.report["image_inference"] = "not_requested_by_this_probe"

    def revoke(self):
        if self.tokens and self.tokens.get("refresh_token") and self.client_id:
            try:
                self.request("revoke_probe_session", self.discovery["revocation_endpoint"],
                             form={"token": self.tokens["refresh_token"], "token_type_hint": "refresh_token", "client_id": self.client_id})
                self.report["renewable_session_revoked"] = True
            except ProbeError:
                self.report["renewable_session_revoked"] = False
                self.report["disconnect_in_chatgpt_settings_required"] = True
        self.tokens = None

    def finish(self):
        self.revoke()
        self.report["finished_at"] = now()
        self.persist()
        save(WORK / ("run_" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:6] + ".json"), self.report)
        self.done = True
        print(json.dumps({"status": self.report["status"], "result_file": str(WORK / "result.json"),
                          "inference_requests": 0}, ensure_ascii=False), flush=True)


class ProbeServer(ThreadingHTTPServer):
    """Keep transport alive independently of the short-lived OAuth transaction."""
    allow_reuse_address = False
    address_family = socket.AF_INET

    def server_bind(self):
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def handle_error(self, request, client_address):
        # socketserver's default traceback can contain a callback URL. Redact it.
        print("Local request failed; callback details were not logged.", flush=True)

    @property
    def host(self):
        return "127.0.0.1:" + str(self.server_address[1])

    @property
    def local_url(self):
        return "http://" + self.host + "/"

    @property
    def redirect_uri(self):
        return "http://" + self.host + CALLBACK_PATH

    def expire_attempt(self):
        if not self.auth_lock.acquire(blocking=False):
            return
        try:
            p = self.probe
            if p.started_authorization and not p.done and time.monotonic() > p.deadline:
                p.report.update(status="authorization_expired", error="authorization_expired")
                p.finish()
        finally:
            self.auth_lock.release()


def create_server(probe, port=8766):
    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            self.request.settimeout(10)
            super().setup()

        def log_message(self, *_):
            pass  # No request lines, query parameters, headers, or OAuth codes.

        def send_headers(self, status, content_type, *, cookie=False, location=None):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("X-Content-Type-Options", "nosniff")
            if cookie:
                self.send_header("Set-Cookie", "mb_siwc_probe=" + self.server.probe.cookie + "; HttpOnly; SameSite=Lax; Path=/")
            if location:
                self.send_header("Location", location)
            self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'; form-action 'self' https://auth.openai.com; frame-ancestors 'none'")
            self.end_headers()

        def page(self, status, body, cookie=False):
            self.send_headers(status, "text/html; charset=utf-8", cookie=cookie)
            encoded = ("<!doctype html><html lang=ja><meta charset=utf-8><meta name=viewport content='width=device-width'><title>Manga Blendy 接続試験</title><style>body{max-width:720px;margin:60px auto;padding:24px;font:18px/1.7 sans-serif}button{font-size:20px;padding:12px 24px}</style>" + body).encode("utf-8")
            self.wfile.write(encoded)

        def valid_cookie(self):
            try:
                cookies = SimpleCookie(self.headers.get("Cookie", ""))
                value = cookies.get("mb_siwc_probe")
                return value is not None and secrets.compare_digest(value.value, self.server.probe.cookie)
            except Exception:
                return False

        def redirect_home(self):
            self.send_headers(303, "text/html; charset=utf-8", location="/")

        def do_GET(self):
            if self.headers.get("Host") == self.server.host and urlsplit(self.path).path == "/health":
                self.send_headers(200, "application/json")
                self.wfile.write(json.dumps({"service": SERVICE, "version": SERVICE_VERSION,
                                            "port": self.server.server_address[1], "pid": os.getpid(),
                                            "status": self.server.probe.report["status"]}).encode())
                return
            with self.server.auth_lock:
                self.get_page()

        def get_page(self):
            if self.headers.get("Host") != self.server.host:
                self.page(400, "127.0.0.1の試験ページから開いてください。localhostは使用しません。")
                return
            self.server.expire_attempt()
            parsed = urlsplit(self.path)
            p = self.server.probe
            if parsed.path == "/":
                status = p.report["status"]
                body = "<h1>Manga Blendy ChatGPT接続試験</h1>"
                if status == "model_list_succeeded":
                    body += "<h2>モデル一覧を取得しました</h2><p>" + str(len(p.report["models"])) + "件の選択可能なモデルを確認しました。画像解析は実行していません。</p>"
                elif status == "authorization_expired":
                    body += "<p>認証の有効期限が切れました。受信サーバーは起動したままです。下のボタンから新しくログインしてください。</p>"
                elif status == "failed":
                    body += "<p>接続試験を停止しました。診断コード：" + html.escape(p.report.get("error", "unknown")) + "</p>"
                elif status == "awaiting_human_sign_in":
                    body += "<p>OpenAIでログイン・権限確認を進めてください。このサーバーは認証中も起動したままです。</p>"
                if self.server.notice:
                    body += "<p>" + self.server.notice + "</p>"
                body += "<p>モデル一覧の取得だけを試します。画像解析・有料API・クレジット購入は実行しません。試験後に更新セッションを失効させ、トークンは保存しません。</p><p>OpenAIの画面ではご自身でログインし、Manga Blendyの名前と利用権限を確認してください。</p>"
                body += "<form method=post action=/authorize><input type=hidden name=state value='" + p.form_token + "'><button>Continue with ChatGPT</button></form><p>認証後はこのManga Blendyの結果画面へ戻ります。同じブラウザで続けてください。</p>"
                self.page(200, body, cookie=True)
            elif parsed.path == CALLBACK_PATH:
                # A stale or foreign callback never consumes the active transaction.
                params = parse_qs(parsed.query) if len(parsed.query) <= 20000 else {}
                states = params.get("state", [])
                if not self.valid_cookie() or len(states) != 1 or not secrets.compare_digest(states[0], p.state):
                    self.server.notice = "古い認証結果または別のブラウザからの戻り先です。このページのボタンから再ログインしてください。"
                    self.redirect_home()
                    return
                if p.done or not p.started_authorization:
                    self.server.notice = "この認証は終了しています。必要なら下のボタンから再ログインできます。"
                    self.redirect_home()
                    return
                try:
                    p.complete(params, self.server.redirect_uri)
                except Exception as exc:
                    code = str(exc) if isinstance(exc, ProbeError) else type(exc).__name__
                    p.report.update(status="failed", error=code)
                finally:
                    p.finish()
                # Remove callback query parameters before displaying the result.
                self.server.notice = None
                self.redirect_home()
            else:
                self.page(404, "ページが見つかりません。")

        def do_POST(self):
            with self.server.auth_lock:
                self.post_page()

        def post_page(self):
            if self.path != "/authorize" or self.headers.get("Host") != self.server.host or not self.valid_cookie():
                self.page(400, "試験ページを開き直してください。")
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 200:
                    raise ValueError()
                form = parse_qs(self.rfile.read(length).decode("ascii"))
                if form.get("state") != [self.server.probe.form_token]:
                    raise ValueError()
            except (ValueError, UnicodeError):
                self.page(400, "無効なフォームです。")
                return
            previous = self.server.probe
            if previous.started_authorization and not previous.done:
                previous.report["status"] = "superseded_by_user_retry"
                previous.finish()
            # Start the ten-minute transaction only after the person clicks.
            # Expiration changes the transaction, never the server's lifetime.
            p = Probe()
            p.cookie = previous.cookie
            self.server.probe = p
            self.server.notice = None
            try:
                p.preflight()
                url = p.authorization_url(self.server.redirect_uri)
                p.deadline = time.monotonic() + 600
                p.started_authorization = True
                p.report.update(status="awaiting_human_sign_in", local_test_page=self.server.local_url,
                                callback_uri=self.server.redirect_uri)
                p.persist()
                self.send_headers(303, "text/html; charset=utf-8", location=url)
            except Exception as exc:
                code = str(exc) if isinstance(exc, ProbeError) else type(exc).__name__
                p.report.update(status="failed", error=code)
                p.finish()
                self.redirect_home()

    server = ProbeServer(("127.0.0.1", port), Handler)
    server.timeout = 1
    server.probe = probe
    server.notice = None
    server.auth_lock = threading.RLock()
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--discovery-only", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    if args.discovery_only:
        p = Probe()
        try:
            p.preflight()
            p.report["status"] = "discovery_succeeded"
        except Exception as exc:
            p.report.update(status="failed", error=str(exc) if isinstance(exc, ProbeError) else type(exc).__name__)
        p.finish()
        return 0 if p.report["status"] == "discovery_succeeded" else 1

    from filelock import FileLock, Timeout
    import psutil
    WORK.mkdir(parents=True, exist_ok=True)
    lock = FileLock(WORK / "listener.lock")
    try:
        lock.acquire(timeout=0)
    except Timeout:
        print("The Manga Blendy sign-in listener is already running.", flush=True)
        return 0
    server = None
    try:
        p = Probe()
        try:
            server = create_server(p, args.port)
        except OSError as exc:
            if exc.errno not in {98, 10048, 13, 10013}:
                raise
            # Never stop another process. Bind an available IPv4 loopback port.
            server = create_server(p, 0)
        p.report.update(status="awaiting_human_start", local_test_page=server.local_url,
                        callback_uri=server.redirect_uri)
        p.persist()
        save(WORK / "listener.json", {"service": SERVICE, "version": SERVICE_VERSION,
                                     "pid": os.getpid(), "created": psutil.Process().create_time(),
                                     "port": server.server_address[1], "url": server.local_url})
        print("OPEN " + server.local_url + " (listener remains active; no inference)", flush=True)
        if not args.no_browser:
            webbrowser.open(server.local_url)
        # Keep serving after expiration and after a completed callback. The Windows
        # launcher starts this process detached from the chat's execution session.
        while True:
            server.handle_request()
            server.expire_attempt()
    except KeyboardInterrupt:
        if server and not server.probe.done:
            server.probe.report["status"] = "cancelled"
            server.probe.finish()
        return 0
    except Exception as exc:
        print("Listener startup failed: " + type(exc).__name__, flush=True)
        return 1
    finally:
        if server:
            server.server_close()
        lock.release()


if __name__ == "__main__":
    sys.exit(main())
