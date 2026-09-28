"""Invariants of the declarative sensor tables.

The tables use a positional format read in WebBoilerGenericSensor.__init__:

    "B_Tk1": [unit, icon, device_class, description, attributes?]

A malformed entry does not fail at import time: it raises IndexError when Home
Assistant starts, on the user's installation. These tests check the shape with
the AST only, so they need neither Home Assistant nor a boiler.
"""
import ast
import pathlib

import pytest

SENSORS = (
    pathlib.Path(__file__).resolve().parents[1]
    / "custom_components"
    / "centrometal_boiler"
    / "sensors"
)
TABLE_FILES = sorted(SENSORS.glob("generic_sensors_*.py"))


def _tables(path):
    """Yield (table_name, [(key, value_node), ...]) for each dict literal."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Dict):
            continue
        name = node.targets[0].id if isinstance(node.targets[0], ast.Name) else "?"
        entries = []
        for key, value in zip(node.value.keys, node.value.values):
            if isinstance(key, ast.Constant) and isinstance(value, ast.List):
                entries.append((key.value, value))
        if entries:
            yield name, entries


def test_table_files_are_discovered():
    assert TABLE_FILES, "no generic_sensors_*.py file found"


@pytest.mark.parametrize("path", TABLE_FILES, ids=lambda p: p.name)
def test_entries_have_four_or_five_elements(path):
    for table, entries in _tables(path):
        for key, value in entries:
            assert 4 <= len(value.elts) <= 5, (
                f"{path.name}:{table}[{key}] has {len(value.elts)} elements, "
                "expected 4 or 5"
            )


@pytest.mark.parametrize("path", TABLE_FILES, ids=lambda p: p.name)
def test_icons_look_like_mdi_icons(path):
    for table, entries in _tables(path):
        for key, value in entries:
            icon = value.elts[1]
            assert isinstance(icon, ast.Constant), f"{path.name}:{table}[{key}]"
            assert icon.value.startswith("mdi:"), (
                f"{path.name}:{table}[{key}] icon is {icon.value!r}"
            )


@pytest.mark.parametrize("path", TABLE_FILES, ids=lambda p: p.name)
def test_descriptions_are_non_empty_strings(path):
    for table, entries in _tables(path):
        for key, value in entries:
            description = value.elts[3]
            assert isinstance(description, ast.Constant), (
                f"{path.name}:{table}[{key}] description is not a literal"
            )
            assert description.value.strip(), f"{path.name}:{table}[{key}]"


@pytest.mark.parametrize("path", TABLE_FILES, ids=lambda p: p.name)
def test_no_duplicate_keys_within_a_table(path):
    for table, entries in _tables(path):
        keys = [key for key, _ in entries]
        duplicates = {key for key in keys if keys.count(key) > 1}
        assert not duplicates, f"{path.name}:{table} repeats {sorted(duplicates)}"


@pytest.mark.parametrize("path", TABLE_FILES, ids=lambda p: p.name)
def test_no_duplicate_descriptions_within_a_table(path):
    """Two entries sharing a description would create colliding entity names."""
    for table, entries in _tables(path):
        seen = {}
        for key, value in entries:
            description = value.elts[3].value
            assert description not in seen, (
                f"{path.name}:{table}: {key} and {seen[description]} share "
                f"the description {description!r}"
            )
            seen[description] = key


def test_compact_table_only_pops_existing_keys():
    """generic_sensors_compact.py removes PelTec sensors with pop(..., None).

    A silent no-op would mean a Compact boiler exposing a sensor it does not
    have, so check every popped key really exists upstream.
    """
    compact = SENSORS / "generic_sensors_compact.py"
    peltec_keys = set()
    for _, entries in _tables(SENSORS / "generic_sensors_peltec.py"):
        peltec_keys |= {key for key, _ in entries}

    tree = ast.parse(compact.read_text(encoding="utf-8"))
    popped = [
        node.args[0].value
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "pop"
        and node.args
        and isinstance(node.args[0], ast.Constant)
    ]
    assert popped, "expected generic_sensors_compact.py to pop some keys"
    for key in popped:
        assert key in peltec_keys, (
            f"generic_sensors_compact.py pops {key!r}, which no longer exists "
            "in the PelTec tables: the removal is silently doing nothing"
        )
