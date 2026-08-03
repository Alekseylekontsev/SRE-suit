"""CLI smoke tests for the coordinator-based entry point (sre_agent.cli)."""
from sre_agent import cli

CFG_ARGS = ["--config", "/nonexistent-on-purpose"]  # falls back to defaults


def test_no_command_prints_help_exit_0(capsys):
    rc = cli.main([])
    out = capsys.readouterr().out.lower()
    assert rc == 0 and "usage" in out


def test_unknown_command_exits_2():
    # argparse rejects an unregistered subcommand with SystemExit(2)
    import pytest
    with pytest.raises(SystemExit) as e:
        cli.main(["totally-bogus-command"])
    assert e.value.code == 2


def test_bad_payload_exits_2():
    # run with malformed JSON payload → clean exit code 2
    import pytest
    with pytest.raises(SystemExit) as e:
        cli.main(["run", "--operation", "list_vms", "--payload", "{not json"])
    assert e.value.code == 2


def test_metrics_command_exit_0(capsys):
    rc = cli.main(["metrics"])
    assert rc == 0
    # Prometheus exposition output (empty counters still render header lines or nothing)
    assert isinstance(capsys.readouterr().out, str)


def test_parse_payload_helper():
    assert cli._parse_payload(None) == {}
    assert cli._parse_payload('{"a": 1}') == {"a": 1}
