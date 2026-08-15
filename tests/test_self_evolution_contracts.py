from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml
from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "atom-learn" / "assets" / "schemas"
FIXTURE_ROOT = ROOT / "tests" / "fixtures" / "self_evolution_v2"

CONTRACTS = {
    "user-profile": "user-profile.json",
    "user-strategy": "user-strategy.json",
    "effective-policy": "effective-policy.json",
    "strategy-experiment": "strategy-experiment.json",
    "evolution-capsule": "evolution-capsule.json",
    "core-manifest": "core-manifest.json",
}


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(("schema_name", "fixture_name"), CONTRACTS.items())
def test_v2_contract_examples_validate(schema_name: str, fixture_name: str) -> None:
    schema = load_json(SCHEMA_ROOT / f"{schema_name}.schema.json")
    Draft202012Validator.check_schema(schema)
    Draft202012Validator(schema).validate(load_json(FIXTURE_ROOT / fixture_name))


@pytest.mark.parametrize(("schema_name", "fixture_name"), CONTRACTS.items())
def test_v2_contracts_reject_unknown_top_level_fields(schema_name: str, fixture_name: str) -> None:
    schema = load_json(SCHEMA_ROOT / f"{schema_name}.schema.json")
    payload = load_json(FIXTURE_ROOT / fixture_name)
    payload["raw_message"] = "must never enter canonical state"
    errors = list(Draft202012Validator(schema).iter_errors(payload))
    assert errors
    assert any(error.validator == "additionalProperties" for error in errors)


def test_profile_contract_hard_codes_privacy_guards() -> None:
    schema = load_json(SCHEMA_ROOT / "user-profile.schema.json")
    payload = load_json(FIXTURE_ROOT / "user-profile.json")
    for key in ["store_raw_messages", "infer_sensitive_traits"]:
        invalid = copy.deepcopy(payload)
        invalid["policy"][key] = True
        assert list(Draft202012Validator(schema).iter_errors(invalid))


def test_capsule_contract_has_no_free_text_escape_hatch() -> None:
    schema = load_json(SCHEMA_ROOT / "evolution-capsule.schema.json")
    payload = load_json(FIXTURE_ROOT / "evolution-capsule.json")
    for field in ["summary", "message", "workspace_path", "source_url", "atom_id"]:
        invalid = copy.deepcopy(payload)
        invalid[field] = "sensitive-value"
        assert list(Draft202012Validator(schema).iter_errors(invalid))


def test_v1_migration_catalog_freezes_representative_baselines() -> None:
    catalog = yaml.safe_load(
        (ROOT / "tests" / "fixtures" / "self_evolution_v1" / "workspace-catalog.yaml").read_text(
            encoding="utf-8"
        )
    )
    assert catalog["created_from_core_version"] == "0.12.0"
    assert set(catalog["cases"]) == {
        "empty_workspace",
        "adaptation_workspace",
        "evolution_workspace",
        "full_subsystems_workspace",
    }
    for case in catalog["cases"].values():
        assert case["course"]["schema_version"] == 1
    adaptation = catalog["cases"]["adaptation_workspace"]["adaptation"]
    assert adaptation["state"]["policy"]["store_raw_messages"] is False
    evolution = catalog["cases"]["evolution_workspace"]["evolution"]
    assert evolution["policy"]["mode"] == "proposal_only"
    assert evolution["proposal"]["type"] == "teaching_strategy"
