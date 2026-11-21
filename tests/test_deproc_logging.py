import logging

import pytest
from typer.testing import CliRunner

from deputy import app
from deputy.logger import _reset_logging, init_logging

DEPROC_LOGGER = "deproc.plugins.python.utils.packages"


@pytest.fixture(autouse=True)
def clean_logging():
    _reset_logging()
    yield
    _reset_logging()


def test_deproc_warning_is_written_to_the_log_file_and_stderr(tmp_path, capsys):
    log_file = tmp_path / "deputy.log"
    init_logging(level="WARNING", log_file=str(log_file))

    logging.getLogger(DEPROC_LOGGER).warning("package 'acme' is shadowed")

    assert (
        f"{DEPROC_LOGGER}  WARNING  package 'acme' is shadowed" in log_file.read_text()
    )
    assert capsys.readouterr().err == "package 'acme' is shadowed\n"


def test_deproc_debug_reaches_the_log_file_only_when_verbose(tmp_path, capsys):
    log_file = tmp_path / "deputy.log"
    init_logging(level="DEBUG", log_file=str(log_file))

    logging.getLogger(DEPROC_LOGGER).debug("linking acme")

    assert "linking acme" in log_file.read_text()
    assert capsys.readouterr().err == ""


def test_error_level_suppresses_deproc_warnings(tmp_path, capsys):
    log_file = tmp_path / "deputy.log"
    init_logging(level="ERROR", log_file=str(log_file))

    logging.getLogger(DEPROC_LOGGER).warning("package 'acme' is shadowed")

    assert "shadowed" not in log_file.read_text()
    assert capsys.readouterr().err == ""


def test_reset_removes_the_deproc_handlers(tmp_path):
    init_logging(level="WARNING", log_file=str(tmp_path / "deputy.log"))
    _reset_logging()

    assert logging.getLogger("deproc").handlers == []


def test_deputy_warnings_still_go_to_the_log_file_only(tmp_path, capsys):
    log_file = tmp_path / "deputy.log"
    init_logging(level="WARNING", log_file=str(log_file))

    logging.getLogger("deputy.tools.core").warning("deputy warning")

    assert "deputy warning" in log_file.read_text()
    assert capsys.readouterr().err == ""


def test_sync_records_same_named_package_warning_in_log_and_terminal(
    tmp_path, monkeypatch
):
    project = tmp_path / "app"
    shared = tmp_path / "shared"
    for root, module in ((project, "core.py"), (shared, "extra.py")):
        (root / "acme").mkdir(parents=True)
        (root / "acme" / "__init__.py").write_text("")
        (root / "acme" / module).write_text("VALUE = 1\n")
    monkeypatch.chdir(project)
    runner = CliRunner()
    assert runner.invoke(app, ["init"]).exit_code == 0
    with open(project / ".deputyconfig", "a", encoding="utf-8") as config:
        config.write(f"source_roots={shared}\n")

    result = runner.invoke(app, ["sync"])

    assert result.exit_code == 0, result.output
    message = "regular package 'acme' is defined in more than one root"
    assert message in (project / ".deputy" / "deputy.log").read_text()
    assert message in result.stderr
