"""
Download the training dataset from Kaggle.

    python -m lead_scoring.training.download

Needs a Kaggle API token (~/.kaggle/kaggle.json). The raw data is not
committed to git (Kaggle licence); it lands in agent_a/data/raw/.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from lead_scoring.dataset import DEFAULT_CSV

DATASET = "lakshmikalyan/lead-scoring-x-online-education"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_CSV.parent)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    from kaggle.api.kaggle_api_extended import KaggleApi

    api = KaggleApi()
    api.authenticate()
    api.dataset_download_files(DATASET, path=str(args.out), unzip=True, quiet=False)
    print(f"Dataset saved to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
