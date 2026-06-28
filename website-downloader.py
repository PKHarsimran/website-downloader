#!/usr/bin/env python3
"""Backward-compatible wrapper for the packaged CLI."""

from __future__ import annotations

from website_downloader.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
