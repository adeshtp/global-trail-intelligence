"""
Train the one canonical trail-difficulty model.

    dataset (real, committed) -> features -> geographic split
      -> model comparison on validation -> one untouched test read
      -> artifact + report

Run:

    PYTHONPATH=backend backend/.venv/bin/python -m app.ml.train

Method, and why each step is the way it is:

TARGET
    Supervision is the ``difficulty_class`` tier derived from the recorded
    OpenStreetMap ``sac_scale`` by ``feature_contract.SAC_SCALE_TO_DIFFICULTY``.
    The recorded grade itself is never altered, imputed or invented; the mapping
    is total, fixed and shared with serving.

OBSERVATION UNIT
    One OSM way. Route relations are not modelled: a bounded census of the
    provider found only 2,226 relation-level graded rows worldwide and 3 in the
    hardest tier, which cannot train a model. A way's measured profile is also
    the only thing the runtime can compute for the object a user selects.

SPLIT
    One-degree centroid cells merged across ways that share an endpoint, so
    connected fragments of one route cannot straddle the split. The held-out
    test is a whole set of merged groups, removed before anything is fitted.

SELECTION
    Everything - the tier grouping, the estimator, and the prior-correction
    strength - is chosen by grouped cross-validation INSIDE the training split,
    scored under the real OpenStreetMap grade distribution, because that is the
    distribution a user actually meets. The test set is read once, afterwards.

WHY THE TRAINING WEIGHTS ARE NOT NEUTRAL
    The retained dataset is class-capped, so it is not the population. Weighting
    each row by (natural share / retained share) makes the fitted model
    correspond to the real distribution. The strength is a validation choice,
    not a constant, and an unweighted fit is included in the comparison.
"""

from __future__ import annotations

import hashlib
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    precision_recall_fscore_support,
)
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from app.ml import geographic_groups
from app.ml.feature_contract import (
    BASE_FEATURES,
    CATEGORICAL_FEATURES,
    DERIVED_FEATURES,
    DIFFICULTY_CLASSES,
    DIFFICULTY_CLASS_INDEX,
    DIFFICULTY_CLASS_LABELS,
    DIFFICULTY_CLASS_SUMMARY,
    MODEL_FEATURES,
    SAC_SCALE_TO_DIFFICULTY,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
FEATURE_CSV = REPO_ROOT / "data" / "difficulty" / "difficulty_features.csv"
AUDIT_JSON = REPO_ROOT / "data" / "difficulty" / "difficulty_audit.json"
HOLDOUT_CSV = (
    REPO_ROOT / "data" / "difficulty" / "difficulty_natural_holdout.csv"
)
ARTIFACT = (
    REPO_ROOT / "data" / "difficulty" / "trail_difficulty_model.joblib"
)
REPORT = (
    REPO_ROOT / "data" / "difficulty" / "trail_difficulty_model_report.json"
)

MODEL_NAME = "logistic_regression_difficulty_tier"
RANDOM_STATE = 20260927
CV_SPLITS = 5
SPATIAL_BUFFER_KM = 5.0

# Prior-correction strengths compared during validation. 0 means the retained
# class-capped distribution is used as-is.
PRIOR_STRENGTHS: tuple[float, ...] = (0.0, 0.15, 0.25, 0.35, 0.5, 0.75, 1.0)

ESTIMATORS: dict[str, Any] = {
    "logistic_regression": lambda: LogisticRegression(
        max_iter=6000, C=1.0
    ),
    "random_forest": lambda: RandomForestClassifier(
        n_estimators=300,
        min_samples_leaf=3,
        n_jobs=-1,
        random_state=RANDOM_STATE,
    ),
    "xgboost": lambda: _xgboost(),
}

MODEL_FEATURES_LIST = list(MODEL_FEATURES)


# --------------------------------------------------------------------------
# Data
# --------------------------------------------------------------------------


def load_frame() -> pd.DataFrame:
    """The committed dataset, with the supervised tier derived from the label."""
    frame = pd.read_csv(FEATURE_CSV)
    frame["difficulty_class"] = frame["sac_scale"].map(
        SAC_SCALE_TO_DIFFICULTY
    )
    missing = frame["difficulty_class"].isna().sum()
    if missing:
        raise ValueError(
            f"{missing} rows have a recorded grade with no tier mapping"
        )
    return add_derived(frame)


def add_derived(frame: pd.DataFrame) -> pd.DataFrame:
    """
    The derived features, computed from base features only.

    Every expression below is a function of the columns, never of the label, so
    nothing can leak the answer through a transformation.
    """
    frame = frame.copy()
    length = pd.to_numeric(
        frame["length_km"], errors="coerce"
    ).clip(lower=1e-3)
    gain = pd.to_numeric(frame["elevation_gain_m"], errors="coerce")
    loss = pd.to_numeric(frame["elevation_loss_m"], errors="coerce")
    relief = pd.to_numeric(
        frame["elevation_range_m"], errors="coerce"
    )
    slope = pd.to_numeric(frame["average_slope_pct"], errors="coerce")
    return frame.assign(
        log_length=np.log(length),
        gain_per_km=(gain / length).replace([np.inf, -np.inf], np.nan),
        descent_ratio=(loss / gain.replace(0, np.nan)).replace(
            [np.inf, -np.inf], np.nan
        ),
        steepness_squared=(slope**2).replace(
            [np.inf, -np.inf], np.nan
        ),
        relief_per_km=(relief / length).replace(
            [np.inf, -np.inf], np.nan
        ),
        length_x_no_terrain=length
        * (1.0 - pd.to_numeric(frame["terrain_available"])),
    )


def natural_prior() -> dict[str, float]:
    """
    The real grade distribution, from the provider census in the audit file.

    Read rather than recomputed so the weights rest on the same recorded counts
    that were used when the dataset was built.
    """
    audit = json.loads(AUDIT_JSON.read_text())
    counts = audit.get("audit")
    if not counts:
        raise ValueError(
            "the dataset audit does not record the natural grade counts"
        )
    total = sum(int(row["total_ways"]) for row in counts)
    return {
        str(row["sac_scale"]): int(row["total_ways"]) / total
        for row in counts
    }


def _ways() -> pd.DataFrame:
    return pd.read_csv(REPO_ROOT / "data" / "difficulty" / "difficulty_ways.csv")


def _nearest_train_distance_km(
    lat_a: np.ndarray,
    lon_a: np.ndarray,
    lat_b: np.ndarray,
    lon_b: np.ndarray,
) -> np.ndarray:
    """Great-circle distance from each point in A to the nearest point in B."""
    # ravel() guards the shape: the caller may hand over a single-column frame
    # selection, which would otherwise make the broadcast produce a matrix and
    # the returned per-row distance unreducible by a plain comparison.
    lat1 = np.radians(np.ravel(lat_a))[:, None]
    lat2 = np.radians(np.ravel(lat_b))[None, :]
    dlat = lat2 - lat1
    dlon = (
        np.radians(np.ravel(lon_b))[None, :]
        - np.radians(np.ravel(lon_a))[:, None]
    )
    a = (
        np.sin(dlat / 2) ** 2
        + np.cos(lat1) * np.cos(lat2) * np.sin(dlon / 2) ** 2
    )
    # The pairwise matrix is reduced per row here rather than by the caller, so
    # the function returns one distance per point in A as its signature promises.
    return (
        6371.0088
        * 2
        * np.arcsin(
            np.sqrt(np.clip(a, 0, 1))
        ).min(axis=1)
    )


# --------------------------------------------------------------------------
# Split
# --------------------------------------------------------------------------


def training_split(
    frame: pd.DataFrame,
    ways: pd.DataFrame,
) -> dict[str, Any]:
    """
    Remove one set of whole geographic groups as the held-out test.

    Groups are one-degree cells merged across ways that share an endpoint, so a
    route split across several ways cannot have some parts in training and
    others in the test. The remainder is the training split, and all selection
    happens strictly inside it.
    """
    merged = geographic_groups.build_split_groups(ways)
    by_osm_id = pd.Series(
        merged.to_numpy(), index=ways["osm_id"].to_numpy()
    )
    aligned = by_osm_id.reindex(frame["osm_id"].to_numpy())
    if aligned.isna().any():
        raise SystemExit(
            "the feature rows and the way rows do not describe the same ways"
        )
    groups = aligned.astype(str).to_numpy()

    # One whole fold of merged groups is the held-out test, chosen by
    # StratifiedGroupKFold so every tier is represented. The label and the group
    # decide the split; the features play no part in it.
    positions = np.zeros((len(frame), 1))
    splitter = StratifiedGroupKFold(
        n_splits=CV_SPLITS, shuffle=True, random_state=RANDOM_STATE
    )
    train_idx, test_idx = list(
        splitter.split(positions, frame["difficulty_class"], groups)
    )[0]

    overlap = set(groups[train_idx]) & set(groups[test_idx])
    if overlap:
        raise AssertionError(
            f"{len(overlap)} split groups appear on both sides"
        )

    return {
        "groups": groups,
        "train_idx": train_idx,
        "test_idx": test_idx,
        "report": {
            "train_rows": int(len(train_idx)),
            "test_rows": int(len(test_idx)),
            "merged_groups": int(len(set(groups))),
            "group_overlap_train_test": int(len(overlap)),
        },
    }


# --------------------------------------------------------------------------
# Model
# --------------------------------------------------------------------------


def _xgboost() -> Any:
    from xgboost import XGBClassifier

    return XGBClassifier(
        n_estimators=300,
        max_depth=6,
        learning_rate=0.1,
        subsample=0.9,
        colsample_bytree=0.9,
        reg_lambda=1.0,
        n_jobs=-1,
        random_state=RANDOM_STATE,
        eval_metric="mlogloss",
    )


def preprocessor(features: list[str] | None = None) -> Any:
    """
    One preprocessing definition, parameterised by its column list.

    The runtime and training both pass the canonical contract order and get an
    identical transformer. The column list is an argument rather than a module
    constant so the feature-group ablation can actually drop a column instead
    of silently measuring nothing.
    """
    features = list(MODEL_FEATURES if features is None else features)
    numeric = [f for f in features if f not in CATEGORICAL_FEATURES]
    blocks: list[tuple[str, Any, list[str]]] = [
        (
            "num",
            Pipeline(
                [
                    ("impute", SimpleImputer(strategy="median")),
                    ("scale", StandardScaler()),
                ]
            ),
            numeric,
        )
    ]
    categorical = [f for f in features if f in CATEGORICAL_FEATURES]
    if categorical:
        blocks.append(
            (
                "cat",
                Pipeline(
                    [
                        ("impute", SimpleImputer(strategy="most_frequent")),
                        (
                            "onehot",
                            OneHotEncoder(
                                handle_unknown="ignore",
                                min_frequency=5,
                            ),
                        ),
                    ]
                ),
                categorical,
            )
        )
    return ColumnTransformer(blocks, remainder="drop")


def build_pipeline(estimator: Any, features: list[str] | None = None) -> Any:
    return Pipeline(
        [("prep", preprocessor(features)), ("clf", estimator)]
    )


# --------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------


def weighted_metrics(
    y_true: np.ndarray,
    y_pred: np.ndarray,
    weights: np.ndarray,
) -> dict[str, Any]:
    """Metrics under the real grade distribution, via importance weights.

    The rows are the untouched held-out rows. Weighting them by the natural
    share answers "how would this perform on a real population" without adding,
    removing or duplicating a single row, and without fitting anything here.
    """
    return {
        "accuracy": round(
            float(accuracy_score(y_true, y_pred, sample_weight=weights)),
            4,
        ),
        "macro_f1": round(
            float(
                f1_score(
                    y_true,
                    y_pred,
                    labels=list(DIFFICULTY_CLASSES),
                    average="macro",
                    sample_weight=weights,
                    zero_division=0,
                )
            ),
            4,
        ),
        "balanced_accuracy": round(
            float(
                balanced_accuracy_score(
                    y_true, y_pred, sample_weight=weights
                )
            ),
            4,
        ),
    }


def _normalise(prior: dict[str, float]) -> dict[str, float]:
    total = sum(prior.get(c, 0.0) for c in DIFFICULTY_CLASSES)
    return {
        label: prior.get(label, 0.0) / total
        for label in DIFFICULTY_CLASSES
    }


def class_prior(grade_prior: dict[str, float]) -> dict[str, float]:
    """The natural grade distribution, aggregated onto the supervised tiers."""
    return _normalise(
        {
            label: sum(
                share
                for grade, share in grade_prior.items()
                if SAC_SCALE_TO_DIFFICULTY.get(grade) == label
            )
            for label in DIFFICULTY_CLASSES
        }
    )


def row_weights(
    labels: np.ndarray,
    prior: dict[str, float],
    strength: float,
) -> np.ndarray:
    """
    Per-row training weight: (natural share / retained share) ** strength.

    The retained dataset is capped per grade, so its distribution is designed.
    ``strength`` 0 leaves it alone; 1 fully corrects it. Intermediate values are
    compared on validation, because a full correction maximises accuracy on the
    dominant tier at the cost of the rare ones.
    """
    if strength <= 0:
        return np.ones(len(labels), dtype=float)
    counts = np.bincount(
        labels, minlength=len(DIFFICULTY_CLASSES)
    ).astype(float)
    shares = counts / max(1, len(labels))
    per_class = np.ones(len(DIFFICULTY_CLASSES), dtype=float)
    for index, label in enumerate(DIFFICULTY_CLASSES):
        if shares[index] > 0:
            per_class[index] = prior.get(label, 0.0) / shares[index]
    per_class = np.clip(per_class, 1e-3, None)
    per_class = per_class ** strength
    return per_class[labels]


# --------------------------------------------------------------------------
# Training
# --------------------------------------------------------------------------


def main() -> int:
    import joblib

    frame = load_frame()
    ways = _ways()
    split = training_split(frame, ways)

    labels = np.array(
        [DIFFICULTY_CLASS_INDEX[value] for value in frame["difficulty_class"]],
        dtype=int,
    )
    groups = split["groups"]
    train_idx, test_idx = split["train_idx"], split["test_idx"]
    X = frame[MODEL_FEATURES_LIST]

    grade_prior = natural_prior()
    prior = class_prior(grade_prior)
    report_split = split["report"]
    print(
        f"rows={len(frame)} groups={report_split['merged_groups']} "
        f"train={len(train_idx)} test={len(test_idx)} "
        f"overlap={report_split['group_overlap_train_test']}"
    )
    print(
        "natural tier distribution: "
        + ", ".join(f"{k}={v:.3f}" for k, v in prior.items())
    )
    print()

    # ---- Validation: estimator and prior strength, training split only.
    Xt, yt, gt = X.iloc[train_idx], labels[train_idx], groups[train_idx]
    splitter = StratifiedGroupKFold(
        n_splits=CV_SPLITS, shuffle=True, random_state=RANDOM_STATE
    )
    folds = list(splitter.split(Xt, yt, gt))
    names_of = lambda codes: np.array(
        [DIFFICULTY_CLASSES[int(c)] for c in codes], dtype=object
    )
    tier_of_row = np.array(
        [prior[DIFFICULTY_CLASSES[c]] for c in yt]
    )
    majority = max(range(len(DIFFICULTY_CLASSES)), key=lambda c: prior[
        DIFFICULTY_CLASSES[c]
    ])
    baseline_cv = float(
        np.mean(
            [
                accuracy_score(
                    names_of(yt[fold_val]),
                    np.full(
                        len(yt[fold_val]),
                        DIFFICULTY_CLASSES[majority],
                        dtype=object,
                    ),
                    sample_weight=tier_of_row[fold_val],
                )
                for _fold_train, fold_val in folds
            ]
        )
    )
    print(f"validation majority baseline (weighted): {baseline_cv:.4f}\n")

    candidates: dict[str, dict[str, Any]] = {}
    for strength in PRIOR_STRENGTHS:
        weights = row_weights(yt, prior, strength)
        for name, factory in ESTIMATORS.items():
            accuracies: list[float] = []
            f1s: list[float] = []
            for fold_train, fold_val in folds:
                model = build_pipeline(factory(), MODEL_FEATURES_LIST)
                model.fit(
                    Xt.iloc[fold_train],
                    yt[fold_train],
                    clf__sample_weight=weights[fold_train],
                )
                predicted = model.predict(Xt.iloc[fold_val])
                row_w = tier_of_row[fold_val]
                named = names_of(predicted)
                accuracies.append(
                    accuracy_score(
                        names_of(yt[fold_val]),
                        named,
                        sample_weight=row_w,
                    )
                )
                f1s.append(
                    f1_score(
                        names_of(yt[fold_val]),
                        named,
                        labels=list(DIFFICULTY_CLASSES),
                        average="macro",
                        sample_weight=row_w,
                        zero_division=0,
                    )
                )
            key = f"{name}/prior_{strength}"
            candidates[key] = {
                "estimator": name,
                "prior_strength": strength,
                "validation_accuracy": round(float(np.mean(accuracies)), 4),
                "validation_macro_f1": round(float(np.mean(f1s)), 4),
            }
            print(
                f"  {key:34s} natAcc={candidates[key]['validation_accuracy']:.4f} "
                f"macroF1={candidates[key]['validation_macro_f1']:.4f}"
            )

    # Selection rule, fixed in advance: a candidate must beat the majority
    # baseline on validation accuracy, and among those the highest macro-F1 wins.
    eligible = {
        key: value
        for key, value in candidates.items()
        if value["validation_accuracy"] > baseline_cv
    }
    if not eligible:
        raise SystemExit(
            "no candidate beats the majority baseline on validation; the "
            "target or the feature set is not viable and must be revisited"
        )
    best_key = max(
        eligible, key=lambda k: eligible[k]["validation_macro_f1"]
    )
    best = eligible[best_key]
    print(
        f"\nselected on validation: {best_key} "
        f"(macro-F1 {best['validation_macro_f1']:.4f})\n"
    )

    # ---- Final model, refit on the whole training split.
    final_weights = row_weights(
        labels[train_idx], prior, best["prior_strength"]
    )
    model = build_pipeline(
        ESTIMATORS[best["estimator"]](), MODEL_FEATURES_LIST
    )
    model.fit(
        X.iloc[train_idx],
        labels[train_idx],
        clf__sample_weight=final_weights,
    )

    # ---- The held-out test, read once.
    y_test = np.array(
        [frame["difficulty_class"].to_numpy()[i] for i in test_idx],
        dtype=object,
    )
    predictions = np.array(
        [DIFFICULTY_CLASSES[int(c)] for c in model.predict(X.iloc[test_idx])],
        dtype=object,
    )
    tier_weight = np.array([prior[t] for t in y_test])
    tier_weight = tier_weight / tier_weight.mean()
    test_metrics = weighted_metrics(y_test, predictions, tier_weight)

    majority_label = DIFFICULTY_CLASSES[majority]
    baseline_predictions = np.full(
        len(y_test), majority_label, dtype=object
    )
    test_baseline = weighted_metrics(
        y_test, baseline_predictions, tier_weight
    )

    precision, recall, f1, support = precision_recall_fscore_support(
        y_test,
        predictions,
        labels=list(DIFFICULTY_CLASSES),
        sample_weight=tier_weight,
        zero_division=0,
    )
    per_class = {
        label: {
            "precision": round(float(precision[i]), 4),
            "recall": round(float(recall[i]), 4),
            "f1": round(float(f1[i]), 4),
            "held_out_rows": int(
                (y_test == label).sum()
            ),
            "natural_share": round(prior[label], 4),
        }
        for i, label in enumerate(DIFFICULTY_CLASSES)
    }

    print("HELD-OUT TEST, scored under the real grade distribution")
    print(f"  {'metric':22s} {'model':>8} {'majority':>9}")
    for key in ("accuracy", "macro_f1", "balanced_accuracy"):
        print(
            f"  {key:22s} {test_metrics[key]:8.4f} "
            f"{test_baseline[key]:9.4f}"
        )
    gate = all(
        test_metrics[key] > test_baseline[key]
        for key in ("accuracy", "macro_f1", "balanced_accuracy")
    )
    print(f"\n  usefulness gate (beats majority on all three): "
          f"{'PASS' if gate else 'FAIL'}")
    print("\n  per class")
    for label in DIFFICULTY_CLASSES:
        row = per_class[label]
        print(
            f"    {label:9s} P={row['precision']:.3f} R={row['recall']:.3f} "
            f"F1={row['f1']:.3f} n={row['held_out_rows']}"
        )
    print("\n  confusion matrix (rows true, cols predicted)")
    matrix = confusion_matrix(
        y_test, predictions, labels=list(DIFFICULTY_CLASSES)
    )
    print("            " + "".join(f"{c[:8]:>10s}" for c in DIFFICULTY_CLASSES))
    for i, label in enumerate(DIFFICULTY_CLASSES):
        print(f"    {label:8s} " + "".join(f"{matrix[i][j]:10d}" for j in range(len(DIFFICULTY_CLASSES))))

    # ---- Independent natural sample, if the committed one is present.
    holdout = _score_holdout(model)
    if holdout.get("available"):
        print(
            f"\n  independent natural sample: {holdout['rows']} rows, "
            f"accuracy {holdout['accuracy']:.4f}, macro-F1 "
            f"{holdout['macro_f1']:.4f}"
        )

    # ---- Spatial robustness: rows far from any training row.
    ways_by_id = ways.set_index("osm_id")
    test_ids = frame["osm_id"].to_numpy()[test_idx]
    train_ids = frame["osm_id"].to_numpy()[train_idx]
    nearest = _nearest_train_distance_km(
        ways_by_id.loc[test_ids, "latitude"].to_numpy(dtype=float),
        ways_by_id.loc[test_ids, "longitude"].to_numpy(dtype=float),
        ways_by_id.loc[train_ids, "latitude"].to_numpy(dtype=float),
        ways_by_id.loc[train_ids, "longitude"].to_numpy(dtype=float),
    )
    buffered = nearest >= SPATIAL_BUFFER_KM
    buffered_metrics = (
        weighted_metrics(
            y_test[buffered],
            predictions[buffered],
            tier_weight[buffered],
        )
        if buffered.any()
        else None
    )
    if buffered_metrics:
        print(
            f"\n  spatial buffer {SPATIAL_BUFFER_KM} km: "
            f"{int(buffered.sum())} of {len(y_test)} held-out rows, "
            f"accuracy {buffered_metrics['accuracy']:.4f}"
        )

    # ---- Ablation: which kinds of evidence carry the signal.
    best = {**best, "natural_prior": prior}
    ablation = _feature_ablation(Xt, yt, gt, tier_of_row, best)
    best.pop("natural_prior", None)
    print("\n  feature group ablation (macro-F1 given up by removing it)")
    for name, delta in ablation["delta_macro_f1"].items():
        print(f"    {name:20s} {delta:+.4f}")

    dataset_digest = hashlib.sha256(FEATURE_CSV.read_bytes()).hexdigest()
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        ),
        "model": {
            "name": MODEL_NAME,
            "selected": best_key,
            "estimator": best["estimator"],
            "prior_strength": best["prior_strength"],
            "feature_contract": "difficulty_tier_v1",
            "features": MODEL_FEATURES_LIST,
            "classes": list(DIFFICULTY_CLASSES),
            "class_labels": dict(DIFFICULTY_CLASS_LABELS),
            "observation_unit": "osm_way",
            "authoritative_for_complete_route": False,
        },
        "target": {
            "recorded_field": "sac_scale",
            "supervised_field": "difficulty_class",
            "recorded_grades": list(SAC_SCALE_TO_DIFFICULTY),
            "classes": list(DIFFICULTY_CLASSES),
            "mapping": dict(SAC_SCALE_TO_DIFFICULTY),
            "rationale": (
                "The three tiers are the skill requirement a walker actually "
                "decides on. Predicting the seven recorded grades separately "
                "was measured and rejected: on geographically held-out data "
                "under the real distribution it scored below the "
                "majority-class baseline, because a single tagging pattern "
                "carries several recorded grades and the finer distinctions "
                "are not recoverable from a way's static attributes."
            ),
        },
        "dataset": {
            "path": str(FEATURE_CSV.relative_to(REPO_ROOT)),
            "rows": int(len(frame)),
            "sha256": dataset_digest[:16],
            "natural_grade_distribution": {
                k: round(v, 6) for k, v in grade_prior.items()
            },
            "natural_tier_distribution": {
                k: round(v, 6) for k, v in prior.items()
            },
            "retained_tier_distribution": {
                label: round(
                    float(
                        (
                            frame["difficulty_class"] == label
                        ).mean()
                    ),
                    6,
                )
                for label in DIFFICULTY_CLASSES
            },
            "note": (
                "The retained sample is capped per recorded grade, so its "
                "class balance is designed rather than natural. Both "
                "distributions are recorded so the numbers cannot be misread."
            ),
        },
        "split": {
            **report_split,
            "method": (
                "One-degree centroid cells merged across ways sharing an "
                "endpoint, so connected route fragments cannot straddle the "
                "split. One fifth of the merged groups is removed whole as "
                "the test set before anything is fitted."
            ),
            "spatial_buffer_km": SPATIAL_BUFFER_KM,
            "buffered_test_metrics": buffered_metrics,
            "median_distance_to_training_km": round(
                float(np.median(nearest)), 2
            ),
            "residual_risk": (
                "The provider's way table does not expose relation "
                "membership, so two non-adjacent ways belonging to one named "
                "route cannot be proven to share a group. The spatial buffer "
                "is a robustness check, not proof of isolation."
            ),
        },
        "validation": {
            "majority_baseline": round(baseline_cv, 4),
            "candidates": candidates,
            "selection_rule": (
                "Must beat the majority baseline on validation accuracy under "
                "the real grade distribution; among those, highest macro-F1. "
                "The test set takes no part in this."
            ),
        },
        "final_test": {
            **test_metrics,
            "majority_baseline": test_baseline,
            "majority_class": majority_label,
            "usefulness_gate_passed": bool(gate),
            "per_class": per_class,
            "confusion_matrix": {
                "labels": list(DIFFICULTY_CLASSES),
                "rows": matrix.tolist(),
            },
        },
        "natural_sample_holdout": holdout,
        "feature_group_ablation": ablation,
        "environment": {
            "python": sys.version.split()[0],
            "platform": platform.platform(),
            "numpy": np.__version__,
            "pandas": pd.__version__,
            "scikit_learn": __import__("sklearn").__version__,
        },
        "reproduce": (
            "PYTHONPATH=backend backend/.venv/bin/python -m app.ml.difficulty_dataset "
            "then PYTHONPATH=backend backend/.venv/bin/python -m app.ml.feature_engineering "
            "(rebuilds the dataset from the provider) then "
            "PYTHONPATH=backend backend/.venv/bin/python -m app.ml.train"
        ),
    }

    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    joblib.dump(
        {
            "model": model,
            "model_name": MODEL_NAME,
            "task": (
                "classification of the trail difficulty tier implied by the "
                "recorded OpenStreetMap sac_scale"
            ),
            "classes": list(DIFFICULTY_CLASSES),
            "class_labels": dict(DIFFICULTY_CLASS_LABELS),
            "class_summaries": dict(DIFFICULTY_CLASS_SUMMARY),
            "class_index": dict(DIFFICULTY_CLASS_INDEX),
            "sac_scale_to_class": dict(SAC_SCALE_TO_DIFFICULTY),
            "model_features": MODEL_FEATURES_LIST,
            "base_features": list(BASE_FEATURES),
            "derived_features": list(DERIVED_FEATURES),
            "categorical_features": list(CATEGORICAL_FEATURES),
            "terrain_features_used": True,
            "observation_unit": "osm_way",
            "authoritative_for_complete_route": False,
            "natural_tier_distribution": prior,
            "metrics": test_metrics,
            "majority_baseline_metrics": test_baseline,
            "usefulness_gate_passed": bool(gate),
            "per_class": per_class,
            "natural_sample_holdout": holdout,
            "trained_rows": int(len(train_idx)),
            "dataset_sha256": dataset_digest[:16],
            "limitations": (
                "Way-level only, so a route relation or a connected component "
                "is deliberately not given an estimate. Two thirds of the "
                "training rows carry a sampled terrain profile; the rest are "
                "predicted from geometry and tagging alone. Training read a "
                "static Copernicus DEM and serving reads a live Open-Meteo "
                "profile, so the slope predictors in particular are the same "
                "quantity derived from two different real DEMs. The recorded "
                "grade is a mapper's judgement, not a measurement, and ways "
                "sharing identical tagging are frequently recorded at "
                "different grades."
            ),
        },
        ARTIFACT,
    )
    REPORT.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(f"\nwrote {ARTIFACT.name} and {REPORT.name}")
    return 0 if gate else 1


def _score_holdout(model: Any) -> dict[str, Any]:
    """
    Score the committed natural sample, collected from held-out geography.

    It is an independent check on real rows that share no training geography
    and were never used to fit or select anything. It is small and regional, so
    it is a corroborating number and not the headline.
    """
    if not HOLDOUT_CSV.is_file():
        return {"available": False}
    holdout = pd.read_csv(HOLDOUT_CSV)
    holdout["difficulty_class"] = holdout["sac_scale"].map(
        SAC_SCALE_TO_DIFFICULTY
    )
    holdout = holdout.dropna(subset=["difficulty_class"])
    if holdout.empty:
        return {"available": False}
    frame = add_derived(holdout)
    present = [c for c in MODEL_FEATURES_LIST if c in frame.columns]
    if len(present) != len(MODEL_FEATURES_LIST):
        return {"available": False, "reason": "feature columns missing"}
    predicted = np.array(
        [
            DIFFICULTY_CLASSES[int(code)]
            for code in model.predict(frame[MODEL_FEATURES_LIST])
        ],
        dtype=object,
    )
    truth = frame["difficulty_class"].to_numpy()
    return {
        "available": True,
        "rows": int(len(frame)),
        "accuracy": round(float(accuracy_score(truth, predicted)), 4),
        "macro_f1": round(
            float(
                f1_score(
                    truth,
                    predicted,
                    labels=list(DIFFICULTY_CLASSES),
                    average="macro",
                    zero_division=0,
                )
            ),
            4,
        ),
        "note": (
            "Real rows drawn from held-out one-degree cells, never used for "
            "fitting or selection. Regional and small: a corroborating check, "
            "not a global performance claim."
        ),
    }


FEATURE_GROUPS: dict[str, tuple[str, ...]] = {
    "route_shape": (
        "mean_turn",
        "verts_per_km",
    ),
    "terrain": (
        "elevation_gain_m",
        "elevation_loss_m",
        "elevation_range_m",
        "average_slope_pct",
        "max_slope_pct",
        "terrain_available",
    ),
    "geometry": (
        "length_km",
        "log_length",
        "gain_per_km",
        "descent_ratio",
        "steepness_squared",
        "relief_per_km",
        "length_x_no_terrain",
    ),
    "tagging": tuple(CATEGORICAL_FEATURES),
    "measured_attributes": (
        "incline_pct",
        "width_m",
        "incline_numeric_missing",
        "width_missing",
    ),
}


def _cv_macro_f1(
    X: pd.DataFrame,
    y: np.ndarray,
    groups: np.ndarray,
    row_w: np.ndarray,
    weights: np.ndarray,
    estimator: str,
) -> float:
    splitter = StratifiedGroupKFold(
        n_splits=CV_SPLITS, shuffle=True, random_state=RANDOM_STATE
    )
    scores = []
    for fold_train, fold_val in splitter.split(X, y, groups):
        model = build_pipeline(
            ESTIMATORS[estimator](), list(X.columns)
        )
        model.fit(
            X.iloc[fold_train],
            y[fold_train],
            clf__sample_weight=weights[fold_train],
        )
        predicted = model.predict(X.iloc[fold_val])
        scores.append(
            f1_score(
                np.array([DIFFICULTY_CLASSES[int(c)] for c in y[fold_val]], dtype=object),
                np.array([DIFFICULTY_CLASSES[int(c)] for c in predicted], dtype=object),
                labels=list(DIFFICULTY_CLASSES),
                average="macro",
                sample_weight=row_w[fold_val],
                zero_division=0,
            )
        )
    return round(float(np.mean(scores)), 4)


def _feature_ablation(
    X: pd.DataFrame,
    y: np.ndarray,
    groups: np.ndarray,
    row_w: np.ndarray,
    best: dict[str, Any],
) -> dict[str, Any]:
    """
    What each KIND of evidence is worth, measured by removing whole groups.

    One group carries the train/serve elevation-source caveat, so its measured
    value is what that caveat is worth rather than an assertion about it.
    """
    weights = row_weights(y, best["natural_prior"], best["prior_strength"])
    full = _cv_macro_f1(
        X, y, groups, row_w, weights, best["estimator"]
    )
    result: dict[str, Any] = {
        "all_features": full,
        "macro_f1_removed": {},
        "delta_macro_f1": {},
        "method": (
            "Whole feature groups removed one at a time, same folds, same "
            "estimator, macro-F1 on the training split only. The test set "
            "takes no part."
        ),
    }
    for name, columns in FEATURE_GROUPS.items():
        remaining = [f for f in MODEL_FEATURES_LIST if f not in columns]
        if not remaining or len(remaining) == len(MODEL_FEATURES_LIST):
            continue
        reduced = _cv_macro_f1(
            X[remaining], y, groups, row_w, weights, best["estimator"]
        )
        result["macro_f1_removed"][name] = reduced
        result["delta_macro_f1"][name] = round(full - reduced, 4)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
