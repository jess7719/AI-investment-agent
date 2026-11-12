"""Purged monthly walk-forward ridge experiment. No LLM and no random splits."""

from datetime import datetime, timezone
import hashlib
import json
import math
from zoneinfo import ZoneInfo

from .storage import encode, utc_now


HORIZON = 20
FEATURES = ["momentum_20", "momentum_60", "volatility_20", "market_momentum_20",
            "sector_minus_market_20", "beta_252"]
RULE = {"minimum_price_sessions": 1261, "holdout_sessions": 252,
        "rolling_training_sessions": 1260, "minimum_training_samples": 252,
        "tuning_origins": 126, "minimum_mae_improvement": .05,
        "minimum_nonoverlapping_origins": 10, "interval_coverage_range": [.70, .90],
        "interval_nominal_coverage": .80, "bootstrap_block_sessions": 20,
        "bootstrap_repetitions": 1000, "seed": 20261106}


def evaluation_id(config):
    return hashlib.sha256(encode({"identity": config.identity, "rule": RULE,
                                  "model": "ridge-v1", **({"security_id": config.security_id}
                                  if config.security_id and config.ticker != "NVDA" else {})}).encode()).hexdigest()


def feature_data(chart):
    import numpy as np
    prices = np.array([[r[k] for k in ("company", "market", "sector")] for r in chart])
    daily = prices[1:] / prices[:-1] - 1
    features, labels, origins = [], [], []
    for i in range(252, len(prices)):
        ret20, ret60 = prices[i] / prices[i - 20] - 1, prices[i] / prices[i - 60] - 1
        variance = np.var(daily[i - 252:i, 1], ddof=1)
        if variance <= 0:
            continue
        beta = np.cov(daily[i - 252:i, :2], rowvar=False)[0, 1] / variance
        features.append([ret20[0], ret60[0], np.std(daily[i - 20:i, 0], ddof=1) * math.sqrt(252),
                         ret20[1], ret20[2] - ret20[1], beta])
        labels.append(prices[i + HORIZON, 0] / prices[i, 0] - 1 if i + HORIZON < len(prices) else np.nan)
        origins.append(i)
    return np.array(features), np.array(labels), np.array(origins)


def ridge_fit(x, y, alpha):
    import numpy as np
    mean, scale, y_mean = x.mean(axis=0), x.std(axis=0), y.mean()
    scale = np.where(scale == 0, 1, scale)
    z = (x - mean) / scale
    coefficients = np.linalg.solve(z.T @ z + alpha * np.eye(x.shape[1]), z.T @ (y - y_mean))
    return {"mean": mean.tolist(), "scale": scale.tolist(), "intercept": float(y_mean),
            "coefficients": coefficients.tolist(), "alpha": alpha}


def ridge_predict(model, x):
    import numpy as np
    return ((x - np.array(model["mean"])) / np.array(model["scale"])) @ np.array(model["coefficients"]) + model["intercept"]


def training_indices(origins, validation_origin):
    import numpy as np
    # Strictly exclude a label ending at or beyond the validation origin.
    return np.flatnonzero((origins + HORIZON < validation_origin)
                         & (origins >= validation_origin - RULE["rolling_training_sessions"]))


def walk_forward(x, y, origins, chart, indices, alpha):
    import numpy as np
    rows, models = [], []
    for index in indices:
        origin = int(origins[index])
        month = chart[origin]["date"][:7]
        if not models or models[-1]["month"] != month:
            train = training_indices(origins, origin)
            train = train[np.isfinite(y[train])]
            if len(train) < RULE["minimum_training_samples"]:
                continue
            model = ridge_fit(x[train], y[train], alpha)
            model.update(month=month, training_cutoff=chart[int(origins[train[-1]] + HORIZON)]["date"],
                         training_samples=len(train), majority_positive=bool(np.mean(y[train] > 0) >= .5))
            models.append(model)
        model = models[-1]
        rows.append({"origin": chart[origin]["date"], "origin_index": origin,
                     "target_end": chart[origin + HORIZON]["date"], "actual": float(y[index]),
                     "prediction": float(ridge_predict(model, x[index])), "no_change": 0.0,
                     "historical_mean": model["intercept"],
                     "majority_positive": model["majority_positive"]})
    return rows, models


def score(rows):
    import numpy as np
    if not rows:
        return {"samples": 0}
    actual = np.array([r["actual"] for r in rows])
    errors = {key: np.abs(actual - np.array([r[key] for r in rows]))
              for key in ("prediction", "no_change", "historical_mean")}
    result = {"samples": len(rows), "mae": {k: float(v.mean()) for k, v in errors.items()},
              "directional_accuracy": float(np.mean((np.array([r["prediction"] for r in rows]) > 0) == (actual > 0))),
              "majority_direction_accuracy": float(np.mean(np.array([r["majority_positive"] for r in rows]) == (actual > 0)))}
    intervals = [r for r in rows if "lower" in r]
    result["interval_samples"] = len(intervals)
    result["interval_coverage"] = float(np.mean([r["lower"] <= r["actual"] <= r["upper"] for r in intervals])) if intervals else None
    result["interval_mean_width"] = float(np.mean([r["upper"] - r["lower"] for r in intervals])) if intervals else None
    rng = np.random.default_rng(RULE["seed"])
    boot = {k: [] for k in ("no_change", "historical_mean")}
    spacing = max(1, rows[1]["origin_index"] - rows[0]["origin_index"]) if len(rows) > 1 else 1
    block = min(max(1, math.ceil(HORIZON / spacing)), len(rows))
    for _ in range(RULE["bootstrap_repetitions"]):
        starts = rng.integers(0, len(rows) - block + 1, size=math.ceil(len(rows) / block))
        sample = np.concatenate([np.arange(s, s + block) for s in starts])[:len(rows)]
        for key in boot:
            denominator = errors[key][sample].mean()
            boot[key].append(float(1 - errors["prediction"][sample].mean() / denominator) if denominator else 0.0)
    result["mae_improvement"] = {k: float(1 - errors["prediction"].mean() / errors[k].mean())
                                   if errors[k].mean() else 0 for k in boot}
    result["improvement_95pct_block_bootstrap"] = {k: np.quantile(v, [.025, .975]).tolist() for k, v in boot.items()}
    return result


def nonoverlapping(rows):
    result = []
    for row in rows:
        if not result or row["origin_index"] >= result[-1]["origin_index"] + HORIZON:
            result.append(row)
    return result


def add_intervals(rows, residuals=None):
    import numpy as np
    history = list(residuals or [])
    for i, row in enumerate(rows):
        # At origin i only completed prior labels are known, even within a held-out year.
        available = history + [r["actual"] - r["prediction"] for r in rows[:i]
                               if r["origin_index"] + HORIZON < row["origin_index"]]
        if len(available) >= 60:
            low, high = np.quantile(available[-1260:], [.10, .90])
            row.update(lower=float(row["prediction"] + low), upper=float(row["prediction"] + high))


def evaluate(chart):
    import numpy as np
    if len(chart) < RULE["minimum_price_sessions"]:
        return {"status": "ineligible", "reason": "At least five years (1261 sessions) required", "rule": RULE}, {}
    x, y, origins = feature_data(chart)
    eligible = np.flatnonzero(np.isfinite(y))
    if len(eligible) < 800:
        return {"status": "ineligible", "reason": "Insufficient aligned feature/label observations", "rule": RULE}, {}
    # The final year is reserved before any tuning.
    holdout_start = int(origins[eligible[-RULE["holdout_sessions"]]])
    earlier = [int(i) for i in eligible if origins[i] + HORIZON < holdout_start
               and len(training_indices(origins, origins[i])) >= RULE["minimum_training_samples"]]
    tune = earlier[:RULE["tuning_origins"]]
    development = earlier[RULE["tuning_origins"]:]
    if len(development) < 100:
        return {"status": "ineligible", "reason": "Insufficient development history after purge", "rule": RULE}, {}
    tuning = {}
    for alpha in (.1, 1., 10., 100.):
        rows, _ = walk_forward(x, y, origins, chart, tune, alpha)
        tuning[alpha] = float(np.mean([abs(r["actual"] - r["prediction"]) for r in rows]))
    alpha = min(tuning, key=tuning.get)
    dev, dev_models = walk_forward(x, y, origins, chart, development, alpha)
    final_indices = [int(i) for i in eligible if origins[i] >= holdout_start]
    final, final_models = walk_forward(x, y, origins, chart, final_indices, alpha)
    add_intervals(dev)
    # Exclude development labels crossing the first held-out origin.
    residuals = [r["actual"] - r["prediction"] for r in dev if r["origin_index"] + HORIZON < holdout_start]
    add_intervals(final, residuals)
    scores = {"development": score(dev), "holdout": score(final),
              "development_nonoverlapping": score(nonoverlapping(dev)),
              "holdout_nonoverlapping": score(nonoverlapping(final))}
    passed = True
    for period in ("development", "holdout"):
        s, independent = scores[period], scores[f"{period}_nonoverlapping"]
        coverage = s.get("interval_coverage")
        passed &= (independent["samples"] >= RULE["minimum_nonoverlapping_origins"]
                   and all(v >= .05 for v in s["mae_improvement"].values())
                   and all(v[0] > 0 for v in s["improvement_95pct_block_bootstrap"].values())
                   and coverage is not None and .70 <= coverage <= .90)
    # Archive a current model trained without uncompleted labels. Publication is separate.
    train = training_indices(origins, len(chart) - 1)
    train = train[np.isfinite(y[train])]
    current = ridge_fit(x[train], y[train], alpha)
    current["training_cutoff"] = chart[int(origins[train[-1]] + HORIZON)]["date"]
    report = {"status": "passed" if passed else "not_validated", "rule": RULE, "features": FEATURES,
              "selected_alpha": alpha, "tuning_mae": tuning, "scores": scores,
              "holdout_start": chart[holdout_start]["date"], "holdout_end": final[-1]["target_end"],
              "return_convention": "20-session simple adjusted close-to-close, research return before costs",
              "note": "Frozen holdout decision. A failed holdout must not be repeatedly tuned. No causal claims or calibrated probabilities."}
    artifacts = {"development_predictions": dev, "holdout_predictions": final,
                 "monthly_models": dev_models + final_models, "current_model": current,
                 "feature_version": "company-v1", "model_version": "ridge-v1"}
    return report, artifacts


def evaluate_stored(store, config):
    if not config.sector:
        return {"status": "ineligible", "reason": "A reviewed sector benchmark is required by the existing model specification."}
    from .analysis import build_report
    from .calendar import completed_session
    with store.lease():
        snapshot = build_report(store, config, completed_session())
        if not config.adjustment_record or not config.usage_record:
            return {"status": "ineligible", "reason": "Review usage rights and verify split/dividend adjustments first."}
        if any(v["status"] != "current" for k, v in snapshot["sources"].items() if k.startswith("prices:")):
            return {"status": "ineligible", "reason": "Fresh price history required."}
        versions = {k: v for k, v in snapshot["input_versions"].items() if k.startswith("prices:") and k.endswith(":all")}
        # One frozen evaluation per company/benchmark specification, not per daily refresh.
        identity = evaluation_id(config)
        with store.connect() as db:
            saved = db.execute("SELECT report_json FROM forecast_evaluations WHERE id=?", (identity,)).fetchone()
        if saved:
            return {**json.loads(saved[0]), "evaluation_id": identity, "reused": True}
        report, artifact = evaluate(snapshot["chart"])
        if report["status"] == "ineligible":
            return report
        report["evaluated_at"] = datetime.now(timezone.utc).isoformat()
        report["dataset_versions"] = versions
        report["history_sessions"] = len(snapshot["chart"])
        report["history_hash"] = hashlib.sha256(encode(snapshot["chart"]).encode()).hexdigest()
        with store.connect() as db:
            db.execute("INSERT INTO forecast_evaluations VALUES(?,?,?,?,?)",
                       (identity, utc_now(), encode(versions), encode(report), encode(artifact)))
            for row in artifact.get("holdout_predictions", []):
                prediction_id = f"{identity}:{row['origin']}"
                origin_time = datetime.fromisoformat(row["origin"] + "T22:30:00").replace(
                    tzinfo=ZoneInfo("America/New_York")).astimezone(timezone.utc).isoformat()
                db.execute("INSERT INTO forecast_results VALUES(?,?,?,?,?,?,?,?,?)",
                           (prediction_id, identity, origin_time, row["origin"], row["target_end"],
                            row["prediction"], row.get("lower"), row.get("upper"), row["actual"]))
        publish_forecast(store, config, snapshot)
        store.save_snapshot(config.security_id or config.ticker, snapshot)
        return {**report, "evaluation_id": identity}


def publish_forecast(store, config, report):
    """Publish once per origin, only after validation; preserve all earlier predictions."""
    import numpy as np
    from .calendar import next_session
    identity = evaluation_id(config)
    with store.connect() as db:
        row = db.execute("SELECT report_json,artifact_json FROM forecast_evaluations WHERE id=?",
                         (identity,)).fetchone()
    if not row:
        return
    evaluation, artifact = json.loads(row[0]), json.loads(row[1])
    report["evaluation"] = evaluation
    if (evaluation["status"] != "passed" or report["data_status"] != "current"
            or not config.usage_record or not config.adjustment_record):
        return
    history = report["chart"][:evaluation["history_sessions"]]
    if hashlib.sha256(encode(history).encode()).hexdigest() != evaluation["history_hash"]:
        report["forecast"]["reason"] = "Historical data changed; dependent forecast validation invalidated"
        return
    prediction_id = f"live:{identity}:{report['as_of']}"
    with store.connect() as db:
        previous = db.execute("SELECT * FROM forecast_results WHERE id=?", (prediction_id,)).fetchone()
    if previous:
        report["forecast"] = {"status": "validated_experiment", "prediction_id": prediction_id,
                              "prediction": previous["prediction"], "lower": previous["lower_bound"],
                              "upper": previous["upper_bound"], "target_end": previous["target_end"],
                              "horizon_sessions": HORIZON, "unit": "fractional_return",
                              "evaluation_id": identity}
        return
    x, y, origins = feature_data(report["chart"])
    month = report["as_of"][:7]
    with store.connect() as db:
        saved = db.execute("SELECT artifact_json FROM forecast_models WHERE evaluation_id=? AND month=?",
                           (identity, month)).fetchone()
    if saved:
        model = json.loads(saved[0])
    else:
        train = training_indices(origins, len(report["chart"]) - 1)
        train = train[np.isfinite(y[train])]
        model = ridge_fit(x[train], y[train], evaluation["selected_alpha"])
        cutoff = report["chart"][int(origins[train[-1]] + HORIZON)]["date"]
        with store.connect() as db:
            db.execute("INSERT INTO forecast_models VALUES(?,?,?,?,?,?)",
                       (f"{identity}:{month}", identity, month, cutoff, encode(model), encode(report["input_versions"])))
    prediction = float(ridge_predict(model, x[-1]))
    residuals = [r["actual"] - r["prediction"] for key in ("development_predictions", "holdout_predictions")
                 for r in artifact[key]]
    low, high = np.quantile(residuals[-1260:], [.10, .90]) + prediction
    target_end = report["as_of"]
    for _ in range(HORIZON):
        target_end = next_session(target_end)
    forecast = {"status": "validated_experiment", "prediction_id": prediction_id,
                "prediction": prediction, "lower": float(low), "upper": float(high),
                "target_end": target_end, "horizon_sessions": HORIZON,
                "unit": "fractional_return", "evaluation_id": identity,
                "model_version": "ridge-v1", "interval_nominal_coverage": .80}
    with store.connect() as db:
        db.execute("INSERT INTO forecast_results VALUES(?,?,?,?,?,?,?,?,?)",
                   (prediction_id, identity, utc_now(), report["as_of"], target_end,
                    prediction, float(low), float(high), None))
    report["forecast"] = forecast
