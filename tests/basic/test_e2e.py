import requests
from urllib.parse import urlparse

REQUEST_TIMEOUT = 5

def test_get_request(server):
    resp = requests.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("Content-Type", "")

def test_head_request(server):
    resp = requests.head(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
    assert resp.status_code == 200
    assert len(resp.content) == 0
    assert "Content-Length" in resp.headers

def test_post_not_allowed(server):
    resp = requests.post(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
    assert resp.status_code == 405
    assert "Allow" in resp.headers

def test_not_found(server):
    resp = requests.get(f"{server}/nonexistent.html", timeout=REQUEST_TIMEOUT)
    assert resp.status_code == 404

def test_host_header_required(server):
    import socket
    parsed = urlparse(server)
    host = parsed.hostname
    port = parsed.port
    s = socket.socket()
    s.connect((host, port))
    s.settimeout(REQUEST_TIMEOUT)  # タイムアウト設定
    s.send(b"GET / HTTP/1.1\r\n\r\n")
    resp = s.recv(1024)
    s.close()
    assert b"400" in resp

def test_keep_alive(server):
    with requests.Session() as session:
        resp1 = session.get(f"{server}/index.html", timeout=REQUEST_TIMEOUT)
        resp2 = session.get(f"{server}/test.txt", timeout=REQUEST_TIMEOUT)
        assert resp1.status_code == 200
        assert resp2.status_code == 200

def test_directory_traversal(server):
    resp = requests.get(f"{server}/../../../etc/passwd", timeout=REQUEST_TIMEOUT)
    assert resp.status_code in [400, 403, 404]

def test_encoded_traversal(server):
    resp = requests.get(f"{server}/%2e%2e%2f%2e%2e%2fetc%2fpasswd", timeout=REQUEST_TIMEOUT)
    assert resp.status_code in [400, 403, 404]

def test_null_byte(server):
    resp = requests.get(f"{server}/test.txt%00.jpg", timeout=REQUEST_TIMEOUT)
    assert resp.status_code in [400, 404]

def test_large_header(server):
    headers = {"X-Large": "A" * 100000}
    try:
        resp = requests.get(f"{server}/", headers=headers, timeout=REQUEST_TIMEOUT)
        assert resp.status_code in [400, 431]
    except requests.exceptions.RequestException:
        pass

def test_invalid_method(server):
    import socket
    parsed = urlparse(server)
    host = parsed.hostname
    port = parsed.port
    s = socket.socket()
    s.connect((host, port))
    s.settimeout(REQUEST_TIMEOUT)  # タイムアウト設定
    s.send(b"INVALID / HTTP/1.1\r\nHost: localhost\r\n\r\n")
    resp = s.recv(1024)
    s.close()
    assert b"400" in resp or b"405" in resp or b"501" in resp

def test_crlf_injection(server):
    try:
        requests.get(f"{server}/test.txt\r\nX-Injected: value", timeout=REQUEST_TIMEOUT)
    except requests.exceptions.InvalidURL:
        pass
