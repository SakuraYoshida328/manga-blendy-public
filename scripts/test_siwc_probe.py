"""Offline checks for the isolated sign-in diagnostic; no network or inference."""
import hashlib
import http.client
import io
import json
import socket
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlencode, urlsplit

import siwc_probe as probe_module
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=probe_module.ROOT / ".work")
        self.work_patch = patch.object(probe_module, "WORK", Path(self.temp.name))
        self.work_patch.start()
        self.probe = probe_module.Probe()
        self.redirect = "http://127.0.0.1:8766/auth/callback"
        self.probe.authorization_url(self.redirect)
        self.probe.started_authorization = True
        self.private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        key = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.private.public_key()))
        key.update(kid="test-key", alg="RS256")
        self.probe.jwks = {"keys": [key]}
        self.probe.discovery = {"revocation_endpoint": probe_module.ISSUER + "/revoke"}
        self.calls = []

    def tearDown(self):
        if hasattr(self, "server"):
            self.server.shutdown()
            self.server.server_close()
            self.server_thread.join(timeout=3)
        self.work_patch.stop()
        self.temp.cleanup()

    def start_server(self):
        self.probe.started_authorization = False
        self.probe.report["status"] = "awaiting_human_start"
        self.server = probe_module.create_server(self.probe, 0)
        self.server_thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.server_thread.start()
        status, headers, _ = self.http("GET", "/")
        self.assertEqual(status, 200)
        self.cookie = headers["Set-Cookie"].split(";", 1)[0]

    def http(self, method, path, body=None, headers=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_address[1], timeout=3)
        request_headers = {"Cookie": getattr(self, "cookie", "")}
        request_headers.update(headers or {})
        try:
            connection.request(method, path, body=body, headers=request_headers)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read().decode("utf-8")
        finally:
            connection.close()

    def fake_preflight(self, p):
        p.jwks = self.probe.jwks
        p.discovery = self.probe.discovery

    def authorize(self):
        body = urlencode({"state": self.server.probe.form_token})
        with patch.object(probe_module.Probe, "preflight", lambda p: self.fake_preflight(p)):
            return self.http("POST", "/authorize", body, {"Content-Type": "application/x-www-form-urlencoded"})

    def test_listener_does_not_expire_while_waiting_for_first_click(self):
        self.start_server()
        self.server.probe.deadline = time.monotonic() - 3600
        self.assertEqual(self.http("GET", "/")[0], 200)
        self.assertFalse(self.server.probe.done)
        self.assertEqual(self.http("GET", "/health")[0], 200)

    def test_browser_idle_preconnection_does_not_block_callback_listener(self):
        self.start_server()
        with socket.create_connection(("127.0.0.1", self.server.server_address[1]), timeout=3):
            self.assertEqual(self.http("GET", "/health")[0], 200)
            self.assertEqual(self.http("GET", "/")[0], 200)

    def test_actual_port_matches_authorize_and_token_exchange_and_returns_home(self):
        self.start_server()
        status, headers, _ = self.authorize()
        self.assertEqual(status, 303)
        self.assertIn("form-action 'self' https://auth.openai.com", headers["Content-Security-Policy"])
        params = parse_qs(urlsplit(headers["Location"]).query)
        self.assertEqual(params["redirect_uri"], [self.server.redirect_uri])
        self.assertEqual(urlsplit(self.server.redirect_uri).hostname, "127.0.0.1")
        self.assertEqual(urlsplit(self.server.redirect_uri).port, self.server.server_address[1])
        self.make_tokens(nonce=self.server.probe.nonce)
        callback_path = probe_module.CALLBACK_PATH + "?" + urlencode({k: v[0] for k, v in self.callback(self.server.probe.state).items()})
        captured = io.StringIO()
        with patch.object(probe_module.Probe, "request", side_effect=self.request), redirect_stdout(captured), redirect_stderr(captured):
            status, headers, body = self.http("GET", callback_path)
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/")
        self.assertNotIn("TEST_CODE", body + headers["Location"] + captured.getvalue())
        exchange = next(call for call in self.calls if call[0] == "code_exchange")
        self.assertEqual(exchange[2]["redirect_uri"], params["redirect_uri"][0])
        self.assertEqual(self.server.probe.report["status"], "model_list_succeeded")
        self.assertIn("モデル一覧を取得しました", self.http("GET", "/")[2])
        self.assertEqual(self.http("GET", "/")[0], 200)  # Refresh after successful callback.
        self.assertEqual(self.http("GET", "/health")[0], 200)
        before = len(self.calls)
        self.assertEqual(self.http("GET", callback_path)[0], 303)  # One-time callback.
        self.assertEqual(len(self.calls), before)

    def test_expired_attempt_keeps_listener_and_allows_fresh_retry(self):
        self.start_server()
        self.authorize()
        old_state = self.server.probe.state
        self.server.probe.deadline = time.monotonic() - 1
        with redirect_stdout(io.StringIO()):
            status, _, body = self.http("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn("有効期限が切れました", body)
        self.assertEqual(self.http("GET", "/health")[0], 200)
        self.assertEqual(self.authorize()[0], 303)
        self.assertNotEqual(self.server.probe.state, old_state)
        self.assertFalse(self.server.probe.done)

    def test_ipv6_localhost_host_is_rejected_without_ending_ipv4_listener(self):
        self.start_server()
        self.assertEqual(self.http("GET", "/", headers={"Host": "localhost:" + str(self.server.server_address[1])})[0], 400)
        self.assertEqual(self.http("GET", "/health")[0], 200)

    def test_foreign_callback_does_not_consume_current_attempt_or_log_query(self):
        self.start_server()
        self.authorize()
        captured = io.StringIO()
        with redirect_stderr(captured), redirect_stdout(captured):
            status, headers, body = self.http("GET", "/auth/callback?state=WRONG&code=SECRET_TEST_CODE")
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/")
        self.assertNotIn("SECRET_TEST_CODE", captured.getvalue() + body)
        self.assertFalse(self.server.probe.done)
        self.assertEqual(self.http("GET", "/health")[0], 200)

    def test_cancelled_consent_returns_to_local_result_and_listener_stays(self):
        self.start_server()
        self.authorize()
        with redirect_stdout(io.StringIO()):
            status, headers, _ = self.http("GET", "/auth/callback?" + urlencode({"state": self.server.probe.state, "error": "access_denied"}))
        self.assertEqual(status, 303)
        self.assertEqual(headers["Location"], "/")
        self.assertEqual(self.server.probe.report["error"], "authorization_declined_or_failed")
        self.assertIn("接続試験を停止しました", self.http("GET", "/")[2])
        self.assertEqual(self.http("GET", "/health")[0], 200)

    def request(self, label, url, *, form=None, bearer=None):
        self.calls.append((label, url, form, bearer))
        if label == "code_exchange":
            return self.tokens
        if label == "account_models":
            return {"models": [{"slug": "visible-dynamic-model", "display_name": "Visible model", "visibility": "list"},
                               {"slug": "hidden-model", "display_name": "Hidden", "visibility": "hidden"}]}
        if label == "revoke_probe_session":
            return None
        raise AssertionError("Unexpected endpoint")

    def make_tokens(self, *, nonce=None, scope=None, audience="issued-test-client"):
        identity = {"iss": probe_module.ISSUER, "aud": audience, "sub": "test-person",
                    "iat": int(time.time()), "exp": int(time.time()) + 60,
                    "nonce": nonce if nonce is not None else self.probe.nonce}
        self.tokens = {"id_token": jwt.encode(identity, self.private, algorithm="RS256", headers={"kid": "test-key"}),
                       "access_token": "TEST_ACCESS", "refresh_token": "TEST_REFRESH",
                       "scope": scope if scope is not None else probe_module.SCOPES}

    def callback(self, state=None):
        return {"code": ["TEST_CODE"], "state": [state if state is not None else self.probe.state],
                "client_id": ["issued-test-client"]}

    def test_verified_login_models_and_revocation_without_inference_or_token_files(self):
        self.make_tokens()
        with patch.object(self.probe, "request", side_effect=self.request):
            self.probe.complete(self.callback(), self.redirect)
            self.probe.revoke()
            self.probe.persist()
        self.assertEqual([c[0] for c in self.calls], ["code_exchange", "account_models", "revoke_probe_session"])
        self.assertEqual(self.probe.report["models"], [{"slug": "visible-dynamic-model", "display_name": "Visible model"}])
        self.assertEqual(self.probe.report["inference_requests"], 0)
        self.assertIsNone(self.probe.tokens)
        for file in Path(self.temp.name).glob("*.json"):
            text = file.read_text("utf-8")
            for secret in ("TEST_ACCESS", "TEST_REFRESH", "TEST_CODE", self.tokens["id_token"]):
                self.assertNotIn(secret, text)

    def test_bad_state_stops_before_exchange(self):
        with patch.object(self.probe, "request") as request:
            with self.assertRaisesRegex(probe_module.ProbeError, "state_mismatch"):
                self.probe.complete(self.callback("wrong"), self.redirect)
            request.assert_not_called()

    def test_nonce_mismatch_stops_before_models(self):
        self.make_tokens(nonce="wrong")
        with patch.object(self.probe, "request", side_effect=self.request):
            with self.assertRaisesRegex(probe_module.ProbeError, "nonce_invalid"):
                self.probe.complete(self.callback(), self.redirect)
        self.assertEqual([c[0] for c in self.calls], ["code_exchange"])

    def test_wrong_audience_stops_before_models(self):
        self.make_tokens(audience="another-client")
        with patch.object(self.probe, "request", side_effect=self.request):
            with self.assertRaises(jwt.InvalidAudienceError):
                self.probe.complete(self.callback(), self.redirect)
        self.assertEqual([c[0] for c in self.calls], ["code_exchange"])

    def test_invalid_signature_stops_before_models(self):
        self.make_tokens()
        other_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        identity = {"iss": probe_module.ISSUER, "aud": "issued-test-client", "sub": "test-person",
                    "iat": int(time.time()), "exp": int(time.time()) + 60, "nonce": self.probe.nonce}
        self.tokens["id_token"] = jwt.encode(identity, other_key, algorithm="RS256", headers={"kid": "test-key"})
        with patch.object(self.probe, "request", side_effect=self.request):
            with self.assertRaises(jwt.InvalidSignatureError):
                self.probe.complete(self.callback(), self.redirect)
        self.assertEqual([c[0] for c in self.calls], ["code_exchange"])

    def test_expired_identity_stops_before_models(self):
        self.make_tokens()
        identity = {"iss": probe_module.ISSUER, "aud": "issued-test-client", "sub": "test-person",
                    "iat": int(time.time()) - 120, "exp": int(time.time()) - 60, "nonce": self.probe.nonce}
        self.tokens["id_token"] = jwt.encode(identity, self.private, algorithm="RS256", headers={"kid": "test-key"})
        with patch.object(self.probe, "request", side_effect=self.request):
            with self.assertRaises(jwt.ExpiredSignatureError):
                self.probe.complete(self.callback(), self.redirect)
        self.assertEqual([c[0] for c in self.calls], ["code_exchange"])

    def test_identity_only_permission_stops_before_models(self):
        self.make_tokens(scope="openid profile email")
        with patch.object(self.probe, "request", side_effect=self.request):
            with self.assertRaisesRegex(probe_module.ProbeError, "plan_permission_not_granted"):
                self.probe.complete(self.callback(), self.redirect)
        self.assertEqual([c[0] for c in self.calls], ["code_exchange"])

    def test_registered_account_mismatch_stops_before_models(self):
        self.make_tokens()
        self.probe.registration = {"client_id": "issued-test-client", "subject_sha256": hashlib.sha256(b"another-person").hexdigest()}
        with patch.object(self.probe, "request", side_effect=self.request):
            with self.assertRaisesRegex(probe_module.ProbeError, "registered_account_mismatch"):
                self.probe.complete(self.callback(), self.redirect)
        self.assertEqual([c[0] for c in self.calls], ["code_exchange"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
