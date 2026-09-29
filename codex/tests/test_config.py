import json

import pytest

from certificate_analyzer.runtime.codex.config import load_config


def test_load_config_resolves_relative_paths(tmp_path):
    path = tmp_path / "runtime.json"
    path.write_text(
        json.dumps(
            {
                "state_path": "state/runtime.json",
                "report_output_dir": "reports",
            }
        ),
        encoding="utf-8",
    )

    config = load_config(path)

    assert config.state_path == str(tmp_path / "state" / "runtime.json")
    assert config.report_output_dir == str(tmp_path / "reports")


def test_non_loopback_api_requires_token(tmp_path, monkeypatch):
    path = tmp_path / "runtime.json"
    path.write_text(json.dumps({"api_host": "0.0.0.0"}), encoding="utf-8")
    monkeypatch.delenv("CERTIFICATE_ANALYZER_RUNTIME_TOKEN", raising=False)

    with pytest.raises(ValueError, match="токен"):
        load_config(path)
