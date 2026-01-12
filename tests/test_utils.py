import pytest
from utils import get_http_reason_phrase, get_content_type, get_keep_alive, parse_request, HTTPRequest


def test_get_http_reason_phrase():
    assert get_http_reason_phrase(200) == "OK"
    assert get_http_reason_phrase(404) == "Not Found"
    assert get_http_reason_phrase(500) == "Internal Server Error"
    assert get_http_reason_phrase(999) == "Unknown Status Code"


def test_get_content_type():
    content_type, is_binary = get_content_type("test.html")
    assert content_type == "text/html; charset=utf-8"
    assert not is_binary

    content_type, is_binary = get_content_type("test.jpg")
    assert content_type == "image/jpg"
    assert is_binary

    content_type, is_binary = get_content_type("test.txt")
    assert content_type == "text/html; charset=utf-8"
    assert not is_binary


def test_get_keep_alive():
    # HTTP/1.1, no connection header -> True
    request = HTTPRequest("GET", "/", "HTTP/1.1", {}, "")
    assert get_keep_alive(request)

    # HTTP/1.1, connection: close -> False
    request = HTTPRequest("GET", "/", "HTTP/1.1", {"Connection": "close"}, "")
    assert not get_keep_alive(request)

    # HTTP/1.0, no keep-alive -> False
    request = HTTPRequest("GET", "/", "HTTP/1.0", {}, "")
    assert not get_keep_alive(request)

    # HTTP/1.0, keep-alive -> True
    request = HTTPRequest("GET", "/", "HTTP/1.0", {"Connection": "keep-alive"}, "")
    assert get_keep_alive(request)


def test_parse_request():
    request_text = "GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
    request = parse_request(request_text)
    assert request.method == "GET"
    assert request.path == "/"
    assert request.version == "HTTP/1.1"
    assert request.headers == {"Host": "localhost"}
    assert request.body == ""