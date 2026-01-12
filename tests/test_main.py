import pytest
from main import make_response


def test_make_response_index():
    content, length, content_type, status = make_response("/")
    assert status == 200
    assert content_type == "text/html; charset=utf-8"
    assert isinstance(content, str)
    assert length == len(content)


def test_make_response_404():
    content, length, content_type, status = make_response("/nonexistent.html")
    assert status == 404
    assert content == "404 Not Found"
    assert content_type == "text/plain; charset=utf-8"
    assert length == len(content)