"""Repository paths shared by production and evaluation entry points."""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

ARTIFACTS = ROOT / "artifacts"
CURRENT = ARTIFACTS / "current"
STAGE1_ARTIFACTS = CURRENT / "stage1"
STAGE2_ARTIFACTS = CURRENT / "stage2"
STAGE1_BLOCKS = STAGE1_ARTIFACTS / "blocks"
STAGE1_RUNS = STAGE1_ARTIFACTS / "runs"

EVALUATION = ROOT / "evaluation"
EVIDENCE = EVALUATION / "evidence"
RAW = EVIDENCE / "raw"
DIGESTS = EVIDENCE / "digests"
WINDOW_DIGESTS = EVIDENCE / "digests_windows"
GOLDENS = EVALUATION / "goldens"
STAGE1_GOLDENS = GOLDENS / "stage1"
STAGE2_GOLDENS = GOLDENS / "stage2"
STAGE2_LABELS = STAGE2_GOLDENS / "labels"

DOCS = ROOT / "docs"
