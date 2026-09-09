"""Unit tests for Item 15: Deploy smoke test probe utilities and isolation checks."""

import urllib.error
from unittest.mock import MagicMock, patch

from scripts.deploy_smoke_test import request


def test_request_helper_success():
    mock_response = MagicMock()
    mock_response.status = 200
    mock_response.read.return_value = b'{"status":"ok"}'
    mock_response.__enter__.return_value = mock_response

    with patch("urllib.request.urlopen", return_value=mock_response):
        status, body = request("/health")
        assert status == 200
        assert body == b'{"status":"ok"}'


def test_request_helper_http_error():
    mock_error = urllib.error.HTTPError(
        url="http://localhost:8000/documents/123",
        code=404,
        msg="Not Found",
        hdrs={},  # type: ignore
        fp=MagicMock(read=lambda: b'{"detail":"Not Found"}'),
    )

    with patch("urllib.request.urlopen", side_effect=mock_error):
        status, body = request("/documents/123")
        assert status == 404
        assert b"Not Found" in body
