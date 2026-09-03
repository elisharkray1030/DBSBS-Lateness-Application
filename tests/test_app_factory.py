"""App factory + explicit database initialization (#129).

Importing the application module must never touch the database; each
factory-built application carries its own database location, seed list
location, and session key, so two instances stay isolated.
"""

import os
import subprocess
import sys

import pytest

import app as app_module
import storage
from records import Boarder

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _unchecked_client(db_path, namelist_path=None):
    """Builds a factory application without running database init."""
    config = {"DB_PATH": str(db_path), "SECRET_KEY": "test-secret-key", "TESTING": True}
    if namelist_path is not None:
        config["NAMELIST_PATH"] = str(namelist_path)
    return app_module.create_app(config)


class TestImportHasNoSideEffects:
    def test_importing_app_creates_no_database_file(self, tmp_path):
        db_path = tmp_path / "untouched.db"
        env = dict(os.environ, DB_PATH=str(db_path), SECRET_KEY="test-secret-key")
        result = subprocess.run(
            [sys.executable, "-c", "import app"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        assert not db_path.exists()


class TestFactoryConfig:
    def test_inline_config_beats_environment(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DB_PATH", str(tmp_path / "env.db"))
        monkeypatch.setenv("SECRET_KEY", "env-secret")
        inline = tmp_path / "inline.db"
        app = app_module.create_app(
            {"DB_PATH": str(inline), "SECRET_KEY": "inline-secret"}
        )
        assert app.config["DB_PATH"] == str(inline)
        assert app.secret_key == "inline-secret"

    def test_environment_beats_builtin_defaults(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DB_PATH", str(tmp_path / "env.db"))
        monkeypatch.setenv("SECRET_KEY", "env-secret")
        app = app_module.create_app()
        assert app.config["DB_PATH"] == str(tmp_path / "env.db")
        assert app.config["NAMELIST_PATH"] == "namelist.csv"
        assert app.secret_key == "env-secret"

    def test_missing_secret_aborts_startup(self, tmp_path, monkeypatch):
        monkeypatch.delenv("SECRET_KEY", raising=False)
        with pytest.raises(SystemExit, match="SECRET_KEY"):
            app_module.create_app({"DB_PATH": str(tmp_path / "x.db")})

    def test_foreign_app_context_falls_back_to_default(self, monkeypatch):
        from flask import Flask

        monkeypatch.delenv("DB_PATH", raising=False)
        foreign = Flask("foreign")
        with foreign.app_context():
            assert app_module._db_path() == "lateness_history.db"

    def test_two_applications_stay_isolated(self, tmp_path):
        first = tmp_path / "first.db"
        second = tmp_path / "second.db"
        app_a = _unchecked_client(first, tmp_path / "missing-a.csv")
        app_b = _unchecked_client(second, tmp_path / "missing-b.csv")
        app_module.init_db(app_a)
        with app_a.app_context():
            with app_module.connect() as conn:
                storage.replace_boarders(
                    conn, [Boarder("ALICE", "Alice", "601A")]
                )
        app_module.init_db(app_b)
        with app_b.app_context():
            with app_module.connect() as conn:
                assert storage.list_boarders(conn) == []
        with app_a.app_context():
            with app_module.connect() as conn:
                assert [b.normalized_name for b in storage.list_boarders(conn)] == [
                    "ALICE"
                ]


class TestInitDbCommand:
    def test_init_db_cli_prepares_schema_and_seed(self, tmp_path):
        db_path = tmp_path / "cli.db"
        namelist = tmp_path / "namelist.csv"
        namelist.write_text("Bed,Name\n601A,ALICE\n", encoding="utf-8")
        app = app_module.create_app(
            {"DB_PATH": str(db_path), "NAMELIST_PATH": str(namelist), "SECRET_KEY": "test-secret-key"}
        )
        runner = app.test_cli_runner()
        result = runner.invoke(args=["init-db"])
        assert result.exit_code == 0, result.output
        with app.app_context():
            with app_module.connect() as conn:
                assert storage.boarder_master_list(conn) != {}

    def test_init_db_is_idempotent(self, tmp_path):
        db_path = tmp_path / "cli.db"
        namelist = tmp_path / "namelist.csv"
        namelist.write_text("Bed,Name\n601A,ALICE\n", encoding="utf-8")
        app = app_module.create_app(
            {"DB_PATH": str(db_path), "NAMELIST_PATH": str(namelist), "SECRET_KEY": "test-secret-key"}
        )
        runner = app.test_cli_runner()
        assert runner.invoke(args=["init-db"]).exit_code == 0
        assert runner.invoke(args=["init-db"]).exit_code == 0
        with app.app_context():
            with app_module.connect() as conn:
                assert len(storage.list_boarders(conn)) == 1
