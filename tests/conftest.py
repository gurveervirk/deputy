from deputy.database.sqlite import open_database, init_schema, upsert_entity
import pytest

@pytest.fixture
def db():
    conn = open_database(":memory:")
    init_schema(conn)
    return conn

@pytest.fixture
def sample_entities(db):
    rows = [
        dict(id="id1", language="python", full_path="pkg.mod.ClassA", name="ClassA", type="CLASS",
             metadata_json='{"fqn":"pkg.mod.ClassA","lineno":1}'),
        dict(id="id2", language="python", full_path="pkg.mod.func", name="func", type="FUNCTION",
             metadata_json='{"fqn":"pkg.mod.func","lineno":5}'),
        dict(id="id3", language="python", full_path="pkg.mod.var", name="var", type="VARIABLE",
             metadata_json='{"fqn":"pkg.mod.var","lineno":10}'),
        dict(id="id4", language="python", full_path="pkg.mod2.ClassA", name="ClassA", type="CLASS",
             metadata_json='{"fqn":"pkg.mod2.ClassA","lineno":1,"exported":true}'),
        dict(id="id5", language="python", full_path="pkg.mod2", name="mod2", type="MODULE",
             metadata_json='{"fqn":"pkg.mod2","path":"pkg/mod2.py"}'),
    ]
    for kwargs in rows:
        upsert_entity(db, **kwargs)
    db.commit()
    return db
