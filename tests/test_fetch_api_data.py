"""Tests for acmecorp_pipeline.fetch_api_data module."""

from __future__ import annotations

from base64 import b64decode
from unittest.mock import patch

from acmecorp_pipeline.fetch_api_data import VENDOR_CONFIGS, _build_headers


class TestBuildHeaders:
    def test_api_key_auth(self) -> None:
        vcfg = VENDOR_CONFIGS["vendor-a"]
        headers = _build_headers(vcfg, "my-key")
        assert headers["X-Api-Key"] == "my-key"
        assert headers["Content-Type"] == "application/json"

    def test_bearer_auth(self) -> None:
        vcfg = VENDOR_CONFIGS["vendor-b"]
        headers = _build_headers(vcfg, "tok123")
        assert headers["Authorization"] == "Bearer tok123"

    def test_basic_auth_from_env(self) -> None:
        vcfg = VENDOR_CONFIGS["vendor-c"]
        with patch.dict("os.environ", {"VENDOR_C_BASIC_USER": "usr", "VENDOR_C_BASIC_PASS": "pw"}):
            headers = _build_headers(vcfg, "")
        token = headers["Authorization"].split(" ", 1)[1]
        decoded = b64decode(token).decode()
        assert decoded == "usr:pw"

    def test_no_hardcoded_credentials(self) -> None:
        """Ensure vendor-c config no longer contains plaintext credentials."""
        vcfg = VENDOR_CONFIGS["vendor-c"]
        assert "basic_user" not in vcfg
        assert "basic_pass" not in vcfg
