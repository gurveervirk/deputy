import logging

from typer.testing import CliRunner

from deputy import app


def test_search_logs_its_result_count_at_debug_level(tmp_path, monkeypatch, caplog):
    (tmp_path / "app.py").write_text("class App:\n    pass\n")
    monkeypatch.chdir(tmp_path)
    runner = CliRunner()
    assert runner.invoke(app, ["init"]).exit_code == 0
    assert runner.invoke(app, ["sync"]).exit_code == 0

    with caplog.at_level(logging.DEBUG, logger="deputy"):
        result = runner.invoke(app, ["search", "--exact", "app.App"])

    assert result.exit_code == 0
    messages = [r.getMessage() for r in caplog.records if r.name.endswith("tools.core")]
    assert "search for 'app.App' returned 1 results" in messages
