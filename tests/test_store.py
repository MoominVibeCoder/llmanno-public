import sqlite3

import pytest

from llmanno.core import store as store_module
from llmanno.core.store import CallRecord, Store, Unit


def test_log_roundtrip(tmp_path):
    store = Store.create(tmp_path / "s.sqlite")
    store.log("ereignis", wert=1, text="äöü")
    [entry] = store.audit()
    assert entry.event == "ereignis"
    assert entry.details == {"wert": 1, "text": "äöü"}
    assert entry.ts.tzinfo is not None
    store.close()


@pytest.mark.parametrize("sql", ["UPDATE audit SET event = 'x'", "DELETE FROM audit"])
def test_audit_is_append_only(tmp_path, sql):
    store = Store.create(tmp_path / "s.sqlite")
    store.log("ereignis")
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        store._con.execute(sql)
    store.close()


def test_open_missing_store_does_not_create_one(tmp_path):
    path = tmp_path / "fehlt.sqlite"
    with pytest.raises(FileNotFoundError):
        Store.open(path)
    assert not path.exists()


def test_open_rejects_foreign_or_newer_schema(tmp_path):
    path = tmp_path / "fremd.sqlite"
    sqlite3.connect(path).close()
    with pytest.raises(ValueError, match=r"kein llmanno-Store \(Schema 0\)"):
        Store.open(path)
    con = sqlite3.connect(path)
    con.execute("PRAGMA user_version = 99")
    con.close()
    with pytest.raises(ValueError, match=r"neueren llmanno-Version \(Schema 99"):
        Store.open(path)


def test_create_refuses_existing_file(tmp_path):
    path = tmp_path / "s.sqlite"
    Store.create(path).close()
    with pytest.raises(FileExistsError):
        Store.create(path)


def _schema_v1(path):
    con = sqlite3.connect(path)
    con.executescript(store_module._MIGRATIONS[0] + "PRAGMA user_version = 1;")
    con.close()


def test_open_migrates_old_store_and_logs_it(tmp_path):
    path = tmp_path / "alt.sqlite"
    _schema_v1(path)
    store = Store.open(path)
    assert store._con.execute("PRAGMA user_version").fetchone()[0] == store_module.SCHEMA_VERSION
    [entry] = store.audit()
    assert entry.event == "store_migrated"
    assert entry.details == {
        "from_version": 1,
        "to_version": store_module.SCHEMA_VERSION,
        "backup": "alt.vor-migration-1.sqlite",
    }
    store.close()
    # Roadmap 40: Sicherung im alten Stand
    backup = sqlite3.connect(tmp_path / "alt.vor-migration-1.sqlite")
    assert backup.execute("PRAGMA user_version").fetchone()[0] == 1
    backup.close()


def _unit(uid, row):
    return Unit(uid, row, "T1", float(row), None, f"Text {uid}", {}, {"A": "K1"}, {"ID": uid})


def _call(status="ok", run=0):
    return CallRecord(
        item_id="1", model_key="ollama/m", run=run, config_hash="h", seed=1,
        system_sha="s", user_sha="u", status=status, answer={"x": 1} if status == "ok" else None,
        raw=["{}"], error=None if status == "ok" else "kaputt", flags=[], request={}, meta={},
    )  # fmt: skip


@pytest.fixture
def store(tmp_path):
    store = Store.create(tmp_path / "s.sqlite")
    yield store
    store.close()


def test_units_roundtrip_in_row_order(store):
    store.replace_units([_unit("b", 3), _unit("a", 2)])
    assert [u.unit_id for u in store.units()] == ["a", "b"]
    assert store.units()[0] == _unit("a", 2)


def test_texts_are_content_addressed(store):
    sha = store.put_text("Codebuch")
    assert store.put_text("Codebuch") == sha
    assert store.get_text(sha) == "Codebuch"


def _with_texts(store):
    store._con.executemany("INSERT INTO texts VALUES (?, ?)", [("s", "sys"), ("u", "usr")])
    store._con.commit()


def test_error_call_can_be_retried_but_final_calls_are_not_overwritten(store):
    _with_texts(store)
    store.record_call(_call("error"))
    store.record_call(_call("ok"))
    [call] = store.calls()
    assert call.status == "ok" and call.answer == {"x": 1}
    with pytest.raises(RuntimeError, match="existiert bereits"):
        store.record_call(_call("invalid"))


def test_units_cannot_be_replaced_after_coding(store):
    _with_texts(store)
    store.record_call(_call())
    with pytest.raises(RuntimeError, match="neuen Projekt"):
        store.replace_units([_unit("a", 2)])


def test_calls_filter_by_model_and_hash(store):
    _with_texts(store)
    store.record_call(_call(run=0))
    store.record_call(_call(run=1))
    assert len(store.calls(model_key="ollama/m", config_hash="h")) == 2
    assert store.calls(config_hash="anders") == []
