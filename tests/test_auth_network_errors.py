"""Supabase connections that drop mid-response must surface as AuthUnavailable, not a raw OSError."""
import socket
import threading

import pytest

from app.auth import AuthUnavailable, SupabaseAuth


@pytest.fixture
def hanging_up_server():
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(8)
    server.settimeout(5)

    def serve():
        while True:
            try:
                conn, _ = server.accept()
            except OSError:
                return
            conn.recv(65536)
            conn.close()  # read the request, then hang up without any response

    threading.Thread(target=serve, daemon=True).start()
    yield f"http://127.0.0.1:{server.getsockname()[1]}"
    server.close()


def _auth(url):
    auth = SupabaseAuth.__new__(SupabaseAuth)
    auth.url, auth.key, auth.secret = url, "publishable", "secret"
    auth._cache, auth._lock = {}, threading.Lock()
    return auth


def test_admin_request_dropped_mid_response_is_auth_unavailable(hanging_up_server):
    with pytest.raises(AuthUnavailable):
        _auth(hanging_up_server)._admin("/rest/v1/user_access?select=user_id")


def test_token_check_dropped_mid_response_is_auth_unavailable(hanging_up_server):
    with pytest.raises(AuthUnavailable):
        _auth(hanging_up_server).user("some-access-token")
