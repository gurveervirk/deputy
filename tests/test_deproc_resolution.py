import json

from deputy.database.sqlite import upsert_branch_entities, upsert_entity
from deputy.tools.deproc_resolution import DeprocResolutionAdapter
from deputy.tools.resolve import InteractiveResolver


def _insert_python_graph(db, branch_name: str, include_target: bool = True) -> None:
    rows = [
        {
            "id": f"{branch_name}_consumer",
            "language": "python",
            "full_path": "pkg.consumer",
            "name": "consumer",
            "type": "PYTHON_MODULE",
            "metadata_json": json.dumps(
                {
                    "fqn": "pkg.consumer",
                    "path": "pkg/consumer.py",
                    "import_stmt_ids": [f"{branch_name}_import"],
                }
            ),
        },
        {
            "id": f"{branch_name}_import",
            "language": "python",
            "full_path": "pkg.consumer.__import__.import",
            "name": "pkg.target",
            "type": "IMPORT_STATEMENT",
            "metadata_json": json.dumps(
                {
                    "import_type": "from_import",
                    "name_ids": [f"{branch_name}_alias"],
                    "parent_id": f"{branch_name}_consumer",
                }
            ),
            "parent_id": f"{branch_name}_consumer",
        },
        {
            "id": f"{branch_name}_alias",
            "language": "python",
            "full_path": "pkg.consumer.foo",
            "name": "foo",
            "type": "IMPORT_ALIAS",
            "metadata_json": json.dumps(
                {
                    "original_name": "foo",
                    "fqn": "pkg.consumer.foo",
                    "parent_id": f"{branch_name}_import",
                }
            ),
            "parent_id": f"{branch_name}_import",
        },
    ]
    if include_target:
        rows.extend(
            [
                {
                    "id": f"{branch_name}_target",
                    "language": "python",
                    "full_path": "pkg.target",
                    "name": "target",
                    "type": "PYTHON_MODULE",
                    "metadata_json": '{"fqn":"pkg.target","path":"pkg/target.py"}',
                },
                {
                    "id": f"{branch_name}_foo",
                    "language": "python",
                    "full_path": "pkg.target.foo",
                    "name": "foo",
                    "type": "FUNCTION",
                    "metadata_json": '{"fqn":"pkg.target.foo","lineno":1}',
                    "parent_id": f"{branch_name}_target",
                },
            ]
        )
    for row in rows:
        upsert_entity(db, **row)
    upsert_branch_entities(db, branch_name, [row["id"] for row in rows])
    db.commit()


def _insert_java_graph(db, branch_name: str) -> None:
    rows = [
        {
            "id": f"{branch_name}_cu",
            "language": "java",
            "full_path": "com.example.Main",
            "name": "Main",
            "type": "COMPILATION_UNIT",
            "metadata_json": json.dumps(
                {
                    "fqn": "com.example.Main",
                    "package_fqn": "com.example",
                    "path": "src/Main.java",
                    "import_stmt_ids": [f"{branch_name}_import"],
                }
            ),
        },
        {
            "id": f"{branch_name}_import",
            "language": "java",
            "full_path": "java.util.List",
            "name": "List",
            "type": "IMPORT",
            "metadata_json": json.dumps(
                {
                    "import_path": "java.util.List",
                    "import_kind": "single_type",
                    "imported_name": "List",
                    "parent_id": f"{branch_name}_cu",
                }
            ),
            "parent_id": f"{branch_name}_cu",
        },
        {
            "id": f"{branch_name}_list",
            "language": "java",
            "full_path": "java.util.List",
            "name": "List",
            "type": "INTERFACE",
            "metadata_json": '{"fqn":"java.util.List","visibility":"public"}',
        },
    ]
    for row in rows:
        upsert_entity(db, **row)
    upsert_branch_entities(db, branch_name, [row["id"] for row in rows])
    db.commit()


def test_adapter_resolves_from_branch_scoped_python_graph(db):
    _insert_python_graph(db, "main")

    result = DeprocResolutionAdapter(db, "main").resolve("pkg.consumer", "foo")

    assert result.language == "python"
    assert [record["full_path"] for record in result.resolved] == ["pkg.target.foo"]
    assert result.unresolved == ()


def test_adapter_does_not_resolve_entities_from_another_branch(db):
    _insert_python_graph(db, "main")
    _insert_python_graph(db, "feature", include_target=False)

    result = DeprocResolutionAdapter(db, "feature").resolve("pkg.consumer", "foo")

    assert result.resolved == ()
    assert [record["full_path"] for record in result.unresolved] == ["pkg.consumer.foo"]


def test_interactive_resolver_can_use_deproc_backend(db):
    _insert_python_graph(db, "main")

    resolver = InteractiveResolver(db, branch_name="main", backend="deproc")
    result = resolver.resolve("pkg.consumer", "foo")

    assert result is not None
    assert result["full_path"] == "pkg.target.foo"


def test_adapter_resolves_java_imports(db):
    _insert_java_graph(db, "main")

    result = DeprocResolutionAdapter(db, "main").resolve(
        "com.example.Main", "List", language="java"
    )

    assert result.language == "java"
    assert [record["full_path"] for record in result.resolved] == ["java.util.List"]
