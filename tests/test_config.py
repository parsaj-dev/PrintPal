"""Tests for config load/save and its tolerance to bad or old files."""
from __future__ import annotations

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
        assert cfg.detect_dpi == 200
        assert cfg.print_dpi == 300

    def test_roundtrip(self, tmp_config):
        cfg = Config(printer="Test Printer", media_size="2.25x4", detect_dpi=150,
                     print_dpi=400, crop_margin_inches=0.1, copies=3, auto_print=True)
        cfg.save()
        loaded = Config.load()
        assert loaded.printer == "Test Printer"
        assert loaded.media_size == "2.25x4"
        assert loaded.detect_dpi == 150
        assert loaded.print_dpi == 400
        assert loaded.copies == 3
        assert loaded.auto_print is True

    def test_dpi_alias(self, tmp_config):
        cfg = Config()
        cfg.dpi = 175
        assert cfg.detect_dpi == 175
        assert cfg.dpi == 175

    def test_reads_legacy_dpi_key(self, tmp_config):
        tmp_config.write_text('printer = "X"\ndpi = 175\n', encoding="utf-8")
        loaded = Config.load()
        assert loaded.detect_dpi == 175

    def test_clamps_out_of_range(self, tmp_config):
        cfg = Config(detect_dpi=5, print_dpi=99, copies=999,
                     crop_margin_inches=9.0).clamped()
        assert cfg.detect_dpi >= 100
        assert cfg.print_dpi >= cfg.detect_dpi
        assert cfg.copies <= 99
        assert cfg.crop_margin_inches <= 0.5

    def test_tolerates_garbage_file(self, tmp_config):
        tmp_config.write_text("this is not = valid toml {{{", encoding="utf-8")
        cfg = Config.load()  # must not raise
        assert cfg.printer  # falls back to defaults

    def test_printer_name_with_special_chars(self, tmp_config):
        cfg = Config(printer='HP "Office" \\ Jet')
        cfg.save()
        assert Config.load().printer == 'HP "Office" \\ Jet'


def test_new_settings_roundtrip(tmp_config):
    cfg = Config(smart_routing=True, run_in_background=False, learn_crops=False)
    cfg.save()
    loaded = Config.load()
    assert loaded.smart_routing is True
    assert loaded.run_in_background is False
    assert loaded.learn_crops is False


def test_new_settings_default_for_old_files(tmp_config):
    tmp_config.write_text('printer = "X"\n', encoding="utf-8")
    loaded = Config.load()
    assert loaded.smart_routing is False
    assert loaded.run_in_background is True
    assert loaded.learn_crops is True


def test_layout_settings_roundtrip(tmp_config):
    cfg = Config(scale_mode="size", scale_width_in=3.5, scale_height_in=5.0,
                 scale_keep_aspect=False, position="top-left", offset_x_in=0.1,
                 offset_y_in=-0.2, scale_percent=90)
    cfg.save()
    back = Config.load()
    assert (back.scale_mode, back.scale_width_in, back.scale_height_in) == ("size", 3.5, 5.0)
    assert back.scale_keep_aspect is False and back.position == "top-left"
    assert (back.offset_x_in, back.offset_y_in, back.scale_percent) == (0.1, -0.2, 90)


def test_layout_settings_bad_values_fall_back(tmp_config):
    cfg = Config(scale_mode="huge", position="nowhere", scale_percent=5000).clamped()
    assert cfg.scale_mode == "fit" and cfg.position == "center"
    assert cfg.scale_percent == 400
