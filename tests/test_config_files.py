"""Checks on the integration's configuration files.

These run without Home Assistant and without a Centrometal account, so they
catch the packaging mistakes that used to slip through (a missing iot_class,
translations drifting apart from the config flow schema).
"""
import json
import re
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "centrometal_boiler"


def _load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def test_manifest_has_required_fields():
    manifest = _load(COMPONENT / "manifest.json")
    for field in (
        "domain",
        "name",
        "version",
        "documentation",
        "issue_tracker",
        "codeowners",
        "iot_class",
        "requirements",
    ):
        assert field in manifest, f"manifest.json is missing {field}"
    assert manifest["domain"] == "centrometal_boiler"
    # The integration talks to the Centrometal cloud over a websocket.
    assert manifest["iot_class"] == "cloud_push"


def test_manifest_keys_are_sorted_the_way_hassfest_wants():
    """hassfest requires: domain, name, then the rest in alphabetical order."""
    keys = list(_load(COMPONENT / "manifest.json"))
    expected = ["domain", "name"] + sorted(
        key for key in keys if key not in ("domain", "name")
    )
    assert keys == expected


def test_manifest_declares_a_valid_integration_type():
    manifest = _load(COMPONENT / "manifest.json")
    assert manifest["integration_type"] in {
        "device",
        "entity",
        "hardware",
        "helper",
        "hub",
        "service",
        "system",
        "virtual",
    }


def test_setup_declares_a_config_schema():
    """hassfest warns when async_setup exists without a CONFIG_SCHEMA."""
    source = (COMPONENT / "__init__.py").read_text(encoding="utf-8")
    if "async def async_setup(" in source:
        assert "CONFIG_SCHEMA" in source


def test_manifest_version_is_sane():
    """X.Y.Z, optionally followed by a pre-release suffix (0.1.0-beta.1) for HACS betas."""
    version = _load(COMPONENT / "manifest.json")["version"]
    assert re.fullmatch(r"\d+\.\d+\.\d+(-[0-9A-Za-z]+(\.[0-9A-Za-z]+)*)?", version), version


def test_hacs_manifest_matches():
    hacs = _load(ROOT / "hacs.json")
    assert hacs["name"]
    assert "homeassistant" in hacs


def test_translations_share_the_same_keys():
    def keys(node, prefix=""):
        found = set()
        for key, value in node.items():
            found.add(prefix + key)
            if isinstance(value, dict):
                found |= keys(value, prefix + key + ".")
        return found

    strings = keys(_load(COMPONENT / "strings.json"))
    for translation in (COMPONENT / "translations").glob("*.json"):
        assert keys(_load(translation)) == strings, (
            f"{translation.name} has drifted from strings.json"
        )


def test_config_flow_fields_are_translated():
    translation = _load(COMPONENT / "translations" / "en.json")
    fields = translation["config"]["step"]["user"]["data"]
    for name in ("email", "password", "prefix", "product_prefix"):
        assert name in fields, f"{name} has no label in en.json"


def test_every_error_key_is_used_in_python():
    translation = _load(COMPONENT / "translations" / "en.json")
    source = (COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    for key in translation["config"]["error"]:
        if key == "too_many_requests":
            # Reserved for a future rate-limit response from the server.
            continue
        assert key in source, f"error '{key}' is translated but never raised"
