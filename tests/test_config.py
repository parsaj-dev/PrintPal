"""Tests for config load/save."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from printpal.config import Config


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    monkeypatch.setattr("printpal.config._CONFIG_DIR", tmp_path)
    monkeypatch.setattr("printpal.config.CONFIG_PATH", tmp_path / "config.toml")
    return tmp_path / "config.toml"


class TestConfig:
    def test_creates_default_on_first_load(self, tmp_config):
        cfg = Config.load()
        assert tmp_config.exists()
        assert cfg.printer == "DYMO LabelWriter 450"

    def test_roundtrip(self, tmp_config):
        cfg = Config(printer="Test Printer", media_size="2.25x4", dpi=150, crop_margin_inches=0.1)
        cfg.save()
        loaded = Config.load()
        assert loaded.printer == "Test Printer"
        assert loaded.media_size == "2.25x4"
        assert loaded.dpi == 150
        assert loaded.crop_margin_inches == 0.1
