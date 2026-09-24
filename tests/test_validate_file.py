"""Unit tests for the validate_file Lambda. Run with: pytest -q tests"""
import importlib.util
import pathlib

import pytest

APP = pathlib.Path(__file__).resolve().parents[1] / "lambda" / "validate_file" / "app.py"
spec = importlib.util.spec_from_file_location("validate_file_app", APP)
app = importlib.util.module_from_spec(spec)
spec.loader.exec_module(app)


@pytest.fixture(autouse=True)
def lambda_env(monkeypatch):
    monkeypatch.setenv("RAW_PREFIX", "raw/")
    monkeypatch.setenv("ALLOWED_DATASETS", "customers,orders")
    monkeypatch.setenv("ALLOWED_EXTENSIONS", ".csv")
    monkeypatch.setenv("MAX_FILE_SIZE_MB", "1")


def event(key, size=100):
    return {"bucket": "demo-learn-cloudform", "key": key, "size": size}


def test_valid_file():
    result = app.lambda_handler(event("raw/customers/customers_20260924.csv"), None)
    assert result == {
        "is_valid": True,
        "dataset": "customers",
        "s3_path": "s3://demo-learn-cloudform/raw/customers/customers_20260924.csv",
        "reason": "OK",
    }


@pytest.mark.parametrize(
    "key,size,reason_part",
    [
        ("raw/customers/", 0, "Folder marker"),
        ("landing/customers/a.csv", 100, "outside the raw prefix"),
        ("raw/a.csv", 100, "inside a dataset folder"),
        ("raw/products/a.csv", 100, "Unknown dataset"),
        ("raw/customers/a.json", 100, "not allowed"),
        ("raw/customers/a.csv", 0, "empty"),
        ("raw/customers/a.csv", 2 * 1024 * 1024, "larger than"),
    ],
)
def test_invalid_files(key, size, reason_part):
    result = app.lambda_handler(event(key, size), None)
    assert result["is_valid"] is False
    assert reason_part in result["reason"]
    assert set(result) == {"is_valid", "dataset", "s3_path", "reason"}
