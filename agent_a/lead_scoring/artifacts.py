"""
Model artifacts on disk.

    models/
      LATEST                      name of the version to serve
      <version>/model.joblib      fitted pipeline + calibrator
      <version>/model_card.json   thresholds, metrics, data provenance, sha256

joblib files are pickles and can run code when loaded, so the loader
refuses a model.joblib whose sha256 does not match its model card.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import joblib

from lead_scoring.dataset import file_sha256

MODELS_DIR = Path(__file__).resolve().parents[1] / "models"
MODEL_FILE = "model.joblib"
CARD_FILE = "model_card.json"
LATEST_FILE = "LATEST"


class ModelArtifactError(RuntimeError):
    pass


def resolve_model_dir(model_dir: str | os.PathLike | None = None) -> Path:
    """Explicit dir > $AGENT_A_MODEL_DIR > models/<LATEST>."""
    chosen = model_dir or os.environ.get("AGENT_A_MODEL_DIR")
    if chosen:
        return Path(chosen)
    latest = MODELS_DIR / LATEST_FILE
    if not latest.exists():
        raise ModelArtifactError(
            f"No trained model found ({latest} missing). Train one with: "
            "python -m lead_scoring.training.train"
        )
    return MODELS_DIR / latest.read_text(encoding="utf-8").strip()


def save(
    bundle: dict[str, Any],
    card: dict[str, Any],
    version: str,
    *,
    set_latest: bool = True,
    models_dir: Path = MODELS_DIR,
) -> Path:
    out = models_dir / version
    out.mkdir(parents=True, exist_ok=True)
    joblib.dump(bundle, out / MODEL_FILE, compress=3)
    card = {**card, "version": version, "artifact_sha256": file_sha256(out / MODEL_FILE)}
    (out / CARD_FILE).write_text(json.dumps(card, indent=2) + "\n", encoding="utf-8")
    if set_latest:
        (models_dir / LATEST_FILE).write_text(version + "\n", encoding="utf-8")
    return out


def load(model_dir: str | os.PathLike | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    directory = resolve_model_dir(model_dir)
    model_path, card_path = directory / MODEL_FILE, directory / CARD_FILE
    if not model_path.exists() or not card_path.exists():
        raise ModelArtifactError(f"{directory} must contain {MODEL_FILE} and {CARD_FILE}")

    card = json.loads(card_path.read_text(encoding="utf-8"))
    expected = card.get("artifact_sha256")
    actual = file_sha256(model_path)
    if expected != actual:
        raise ModelArtifactError(
            f"{model_path} sha256 {actual} does not match the model card ({expected}); "
            "refusing to load it"
        )
    return joblib.load(model_path), card
