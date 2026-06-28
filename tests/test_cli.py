from __future__ import annotations

import pytest

from website_downloader.cli import load_cookies, parse_args, parse_cookie_header, validate_args


def test_parse_cookie_header_accepts_header_syntax() -> None:
    assert parse_cookie_header("session=abc; csrftoken=xyz") == {
        "session": "abc",
        "csrftoken": "xyz",
    }


def test_parse_cookie_header_rejects_invalid_entries() -> None:
    with pytest.raises(ValueError):
        parse_cookie_header("session")


def test_load_cookies_merges_files_and_cli(tmp_path) -> None:
    cookie_file = tmp_path / "cookies.txt"
    cookie_file.write_text("filecookie=yes", encoding="utf-8")
    assert load_cookies(["session=abc"], [str(cookie_file)]) == {
        "session": "abc",
        "filecookie": "yes",
    }


def test_validate_args_rejects_invalid_limits() -> None:
    args = parse_args(["--url", "https://example.com", "--threads", "0"])
    with pytest.raises(ValueError):
        validate_args(args)
