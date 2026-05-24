import io
import os
import csv
import sys
import hmac
import hashlib
import time
import getpass
from pathlib import Path
from datetime import datetime

import pandas as pd

# ============================================================
# NOTE: CONFIG CHECK PREDICT_RESULT
# ============================================================
PREDICT_FILE = r"C:\Users\idtcu\Alpha-I\predicted_result_t6.csv"

# ============================================================
# GROUND-TRUTH
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
GROUND_TRUTH_ENC = BASE_DIR / "cpt_plant_unseen_gt.csv.enc"
MAX_SCORE = 25

SALT_SIZE = 16
NONCE_SIZE = 16
MAC_SIZE = 32
PBKDF2_ITERATIONS = 200_000
MISSING_PREDICTION_CLASS = -1

GT_REQUIRED_COLUMNS = [
    "timestamp",
    "Temperature_degree_celcius",
    "Humidity_percentage",
    "Lux_lumen",
    "VPD_Kilopascal",
    "environment_state",
    "environment_state_label",
]

PRED_REQUIRED_COLUMNS = [
    "timestamp",
    "Temperature_degree_celcius",
    "Humidity_percentage",
    "Lux_lumen",
    "VPD_Kilopascal",
    "y_pred",
    "y_label_pred",
    "y_pred_prob_normal",
    "y_pred_prob_alert",
    "y_pred_prob_alarm",
]

CLASS_ID_TO_LABEL = {
    0: "normal",
    1: "alert",
    2: "alarm",
}

PROB_COLUMNS = {
    0: "y_pred_prob_normal",
    1: "y_pred_prob_alert",
    2: "y_pred_prob_alarm",
}

# ============================================================
# COLOR PRINT
# ============================================================
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

def passed(msg):
    return f"{GREEN}PASSED{RESET} - {msg}"

def failed(msg):
    return f"{RED}FAILED{RESET} - {msg}"

def warning(msg):
    return f"{YELLOW}WARNING{RESET} - {msg}"

# ============================================================
# GROUND-TRUTH LOCK / UNLOCK  (password-protected, stdlib only)
# ============================================================
def _derive_keys(password, salt):
    master = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS, dklen=64
    )
    return master[:32], master[32:]

def _keystream(enc_key, nonce, length):
    out = bytearray()
    counter = 0
    while len(out) < length:
        out.extend(
            hmac.new(
                enc_key,
                nonce + counter.to_bytes(8, "big"),
                hashlib.sha256
            ).digest()
        )
        counter += 1
    return bytes(out[:length])

def _xor(data, keystream):
    if not data:
        return b""
    return (
        int.from_bytes(data, "big") ^ int.from_bytes(keystream, "big")
    ).to_bytes(len(data), "big")

def encrypt_file(plaintext_path, encrypted_path, password):
    data = Path(plaintext_path).read_bytes()
    salt = os.urandom(SALT_SIZE)
    nonce = os.urandom(NONCE_SIZE)
    enc_key, mac_key = _derive_keys(password, salt)
    ciphertext = _xor(data, _keystream(enc_key, nonce, len(data)))
    tag = hmac.new(mac_key, salt + nonce + ciphertext, hashlib.sha256).digest()
    Path(encrypted_path).write_bytes(salt + nonce + tag + ciphertext)
    return encrypted_path

def decrypt_bytes(encrypted_path, password):
    encrypted_path = Path(encrypted_path)

    if not encrypted_path.exists():
        raise FileNotFoundError(f"Encrypted ground-truth file not found: {encrypted_path}")

    blob = encrypted_path.read_bytes()
    if len(blob) < SALT_SIZE + NONCE_SIZE + MAC_SIZE:
        raise ValueError("Corrupted encrypted ground-truth file: file is too short.")

    salt = blob[:SALT_SIZE]
    nonce = blob[SALT_SIZE:SALT_SIZE + NONCE_SIZE]
    tag = blob[SALT_SIZE + NONCE_SIZE:SALT_SIZE + NONCE_SIZE + MAC_SIZE]
    ciphertext = blob[SALT_SIZE + NONCE_SIZE + MAC_SIZE:]

    enc_key, mac_key = _derive_keys(password, salt)
    expected = hmac.new(mac_key, salt + nonce + ciphertext, hashlib.sha256).digest()

    if not hmac.compare_digest(tag, expected):
        raise ValueError("Wrong password or corrupted encrypted ground-truth file.")

    return _xor(ciphertext, _keystream(enc_key, nonce, len(ciphertext)))

def decrypt_text(encrypted_path, password):
    return decrypt_bytes(encrypted_path, password).decode("utf-8-sig")

# ============================================================
# FILE STRUCTURE CHECK
# ============================================================
def _scan_csv_rows(row_iter):
    bad_rows = []

    try:
        header = next(row_iter)
    except StopIteration:
        raise ValueError("CSV file is empty.")

    expected_cols = len(header)

    for line_no, row in enumerate(row_iter, start=2):
        if len(row) != expected_cols:
            bad_rows.append({
                "line_no": line_no,
                "expected_columns": expected_cols,
                "actual_columns": len(row),
                "row_preview": row[:15],
            })

    return {
        "status": len(bad_rows) == 0,
        "expected_columns": expected_cols,
        "bad_rows": bad_rows,
    }

def check_csv_structure(file_path):
    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"Prediction file not found: {file_path}")

    with open(file_path, "r", encoding="utf-8-sig", errors="replace", newline="") as f:
        return _scan_csv_rows(csv.reader(f))

def check_csv_structure_text(text):
    return _scan_csv_rows(csv.reader(io.StringIO(text)))

# ============================================================
# DATA VALIDATION
# ============================================================
def normalize_columns(df):
    df = df.copy()
    df.columns = (
        df.columns
        .astype(str)
        .str.replace("\ufeff", "", regex=False)
        .str.strip()
    )
    return df

def normalize_timestamp_key(series):
    ts = pd.to_datetime(series.astype(str).str.strip(), errors="coerce")
    return ts.dt.strftime("%Y-%m-%d %H:%M:%S")

def check_required_columns(df, required_columns, file_name):
    df = normalize_columns(df)

    missing = [col for col in required_columns if col not in df.columns]

    if missing:
        print("\nAvailable columns:")
        print(list(df.columns))
        raise ValueError(
            f"{file_name} missing required columns: {missing}"
        )

    return True

def check_no_missing_required_values(df, required_columns, file_name):
    missing_value_counts = df[required_columns].isna().sum()
    missing_value_counts = missing_value_counts[missing_value_counts > 0]

    if not missing_value_counts.empty:
        raise ValueError(
            f"{file_name} has missing values in required columns: "
            f"{missing_value_counts.to_dict()}"
        )

    return True

def validate_unique_timestamp_key(df, file_name):
    if df["_timestamp_key"].isna().any():
        bad_count = int(df["_timestamp_key"].isna().sum())
        raise ValueError(f"{file_name} has {bad_count} invalid timestamp value(s).")

    duplicate_mask = df["_timestamp_key"].duplicated(keep=False)
    if duplicate_mask.any():
        duplicate_rows = df.loc[duplicate_mask].copy()
        duplicate_path = BASE_DIR / f"{file_name}_duplicated_timestamp_rows.csv"
        duplicate_rows.to_csv(duplicate_path, index=False)

        raise ValueError(
            f"{file_name} has duplicated timestamp key rows. "
            f"Saved duplicated rows to: {duplicate_path}"
        )

    return True

def validate_and_prepare_gt(gt_df):
    gt = normalize_columns(gt_df)

    check_required_columns(gt, GT_REQUIRED_COLUMNS, "ground_truth")
    check_no_missing_required_values(gt, GT_REQUIRED_COLUMNS, "ground_truth")

    gt["_timestamp_key"] = normalize_timestamp_key(gt["timestamp"])
    validate_unique_timestamp_key(gt, "ground_truth")

    gt["environment_state"] = pd.to_numeric(
        gt["environment_state"], errors="coerce"
    )

    if gt["environment_state"].isna().any():
        bad_count = int(gt["environment_state"].isna().sum())
        raise ValueError(f"ground_truth has {bad_count} invalid environment_state value(s).")

    gt["environment_state"] = gt["environment_state"].astype(int)

    invalid_classes = sorted(set(gt["environment_state"]) - set(CLASS_ID_TO_LABEL.keys()))
    if invalid_classes:
        raise ValueError(f"ground_truth has invalid class IDs: {invalid_classes}")

    gt["environment_state_label"] = (
        gt["environment_state_label"]
        .astype(str)
        .str.strip()
        .str.lower()
    )

    return gt

def validate_and_prepare_pred(pred_df):
    pred = normalize_columns(pred_df)

    check_required_columns(pred, PRED_REQUIRED_COLUMNS, "prediction")
    check_no_missing_required_values(pred, PRED_REQUIRED_COLUMNS, "prediction")

    pred["_timestamp_key"] = normalize_timestamp_key(pred["timestamp"])
    validate_unique_timestamp_key(pred, "prediction")

    pred["y_pred"] = pd.to_numeric(pred["y_pred"], errors="coerce")

    if pred["y_pred"].isna().any():
        bad_count = int(pred["y_pred"].isna().sum())
        raise ValueError(f"prediction has {bad_count} invalid y_pred value(s).")

    pred["y_pred"] = pred["y_pred"].astype(int)

    invalid_classes = sorted(set(pred["y_pred"]) - set(CLASS_ID_TO_LABEL.keys()))
    if invalid_classes:
        raise ValueError(f"prediction has invalid y_pred class IDs: {invalid_classes}")

    pred["y_label_pred"] = (
        pred["y_label_pred"]
        .astype(str)
        .str.strip()
        .str.lower()
    )

    for col in PROB_COLUMNS.values():
        pred[col] = pd.to_numeric(pred[col], errors="coerce")

    prob_cols = list(PROB_COLUMNS.values())
    if pred[prob_cols].isna().any().any():
        bad_counts = pred[prob_cols].isna().sum()
        bad_counts = bad_counts[bad_counts > 0].to_dict()
        raise ValueError(f"prediction has invalid probability value(s): {bad_counts}")

    return pred

def check_prediction_probability_matches_y_pred(pred_df):
    pred_df = validate_and_prepare_pred(pred_df)
    check_rows = []

    for idx, row in pred_df.iterrows():
        probs = {
            0: float(row["y_pred_prob_normal"]),
            1: float(row["y_pred_prob_alert"]),
            2: float(row["y_pred_prob_alarm"]),
        }

        expected_y_pred = max(probs, key=probs.get)
        actual_y_pred = int(row["y_pred"])

        expected_label = CLASS_ID_TO_LABEL[expected_y_pred]
        actual_label = str(row["y_label_pred"]).strip().lower()

        prob_match_y_pred = expected_y_pred == actual_y_pred
        label_match_y_pred = expected_label == actual_label

        if not prob_match_y_pred or not label_match_y_pred:
            check_rows.append({
                "row_index": idx,
                "timestamp": row.get("timestamp"),
                "timestamp_key": row.get("_timestamp_key"),
                "actual_y_pred": actual_y_pred,
                "expected_y_pred_from_prob": expected_y_pred,
                "actual_y_label_pred": actual_label,
                "expected_label_from_prob": expected_label,
                "prob_normal": probs[0],
                "prob_alert": probs[1],
                "prob_alarm": probs[2],
                "prob_match_y_pred": prob_match_y_pred,
                "label_match_y_pred": label_match_y_pred,
            })

    return {
        "status": len(check_rows) == 0,
        "total_rows": len(pred_df),
        "failed_rows": len(check_rows),
        "details": pd.DataFrame(check_rows),
    }

# ============================================================
# CLASSIFICATION METRICS
# ============================================================
def calculate_classification_metrics(y_true, y_pred, labels=(0, 1, 2)):
    y_true = [int(float(v)) for v in y_true]
    y_pred = [int(float(v)) for v in y_pred]

    total = len(y_true)
    correct = sum(1 for t, p in zip(y_true, y_pred) if t == p)
    accuracy = correct / total if total > 0 else 0

    class_rows = []

    for label in labels:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == label and p == label)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != label and p == label)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == label and p != label)

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0

        f1 = (
            2 * precision * recall / (precision + recall)
            if (precision + recall) > 0
            else 0
        )

        support = sum(1 for t in y_true if t == label)

        class_rows.append({
            "class_id": label,
            "class_label": CLASS_ID_TO_LABEL[label],
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": support,
            "tp": tp,
            "fp": fp,
            "fn": fn,
        })

    class_metric_df = pd.DataFrame(class_rows)

    macro_precision = class_metric_df["precision"].mean()
    macro_recall = class_metric_df["recall"].mean()
    macro_f1 = class_metric_df["f1"].mean()

    correct_prediction_rows = correct
    total_ground_truth_rows_for_score = total
    wrong_prediction_rows = total_ground_truth_rows_for_score - correct_prediction_rows
    prediction_compare_accuracy = accuracy
    prediction_compare_score_out_of_25 = prediction_compare_accuracy * MAX_SCORE

    macro_f1_score_out_of_25 = macro_f1 * MAX_SCORE

    return {
        "accuracy": accuracy,
        "correct_prediction_rows": correct_prediction_rows,
        "wrong_prediction_rows": wrong_prediction_rows,
        "total_ground_truth_rows_for_score": total_ground_truth_rows_for_score,
        "prediction_compare_accuracy": prediction_compare_accuracy,
        "prediction_compare_score_out_of_25": prediction_compare_score_out_of_25,
        "macro_precision": macro_precision,
        "macro_recall": macro_recall,
        "macro_f1": macro_f1,
        "macro_f1_score_out_of_25_diagnostic": macro_f1_score_out_of_25,
        "max_score": MAX_SCORE,
        "class_metric_df": class_metric_df,
    }

# ============================================================
# COMPARE WITH GROUND TRUTH
# ============================================================
def compare_with_ground_truth(gt_df, pred_df):
    gt = validate_and_prepare_gt(gt_df)
    pred = validate_and_prepare_pred(pred_df)

    # Ground-truth LEFT JOIN prediction by normalized timestamp key.
    merged = gt.merge(
        pred,
        on="_timestamp_key",
        how="left",
        suffixes=("_gt", "_pred"),
        validate="one_to_one",
        indicator=True,
    )

    matched_mask = merged["_merge"].eq("both")
    matched_timestamp_rows = int(matched_mask.sum())
    missing_prediction_rows = int((~matched_mask).sum())

    gt_keys = set(gt["_timestamp_key"])
    extra_pred = pred[~pred["_timestamp_key"].isin(gt_keys)].copy()
    extra_prediction_rows = len(extra_pred)

    # Missing prediction is encoded as -1 and counted as wrong.
    merged["y_pred_for_score"] = (
        pd.to_numeric(merged["y_pred"], errors="coerce")
        .fillna(MISSING_PREDICTION_CLASS)
        .astype(int)
    )

    metrics = calculate_classification_metrics(
        y_true=merged["environment_state"],
        y_pred=merged["y_pred_for_score"],
        labels=(0, 1, 2),
    )

    merged["match_status"] = merged["_merge"].map({
        "both": "matched",
        "left_only": "missing_prediction",
        "right_only": "extra_prediction",
    })

    merged["y_match"] = (
        merged["environment_state"] == merged["y_pred_for_score"]
    )

    merged["y_label_match"] = (
        merged["environment_state_label"] == merged["y_label_pred"]
    ).fillna(False)

    label_match_accuracy = merged["y_label_match"].mean()

    missing_prediction_df = merged[merged["match_status"].eq("missing_prediction")].copy()

    return {
        "merged_df": merged,
        "missing_prediction_df": missing_prediction_df,
        "extra_prediction_df": extra_pred,
        "matched_timestamp_rows": matched_timestamp_rows,
        "ground_truth_rows": len(gt),
        "prediction_rows": len(pred),
        "missing_prediction_rows": missing_prediction_rows,
        "extra_prediction_rows": extra_prediction_rows,
        "label_match_accuracy": label_match_accuracy,
        **metrics,
    }

# ============================================================
# MAIN
# ============================================================
def main():
    start_time = time.perf_counter()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    password = getpass.getpass("Enter password to unlock ground-truth file: ")

    pred_structure = check_csv_structure(PREDICT_FILE)
    if not pred_structure["status"]:
        print("\n********** CSV Structure Check **********")
        print(f"Timestamp:          {now}")
        print(f"Prediction File:    {failed('bad CSV structure')}")
        print(f"Expected Columns:   {pred_structure['expected_columns']}")
        print(f"Bad Row Count:      {len(pred_structure['bad_rows'])}")
        print("\nFirst 5 bad rows:")
        for bad in pred_structure["bad_rows"][:5]:
            print(
                f"Line {bad['line_no']}: "
                f"expected {bad['expected_columns']}, "
                f"actual {bad['actual_columns']}, "
                f"preview={bad['row_preview']}"
            )
        print("*****************************************")
        raise RuntimeError("Stop scoring because prediction CSV structure is invalid.")

    gt_text = decrypt_text(GROUND_TRUTH_ENC, password)

    gt_structure = check_csv_structure_text(gt_text)
    if not gt_structure["status"]:
        print("\n********** CSV Structure Check **********")
        print(f"Timestamp:          {now}")
        print(f"Ground Truth File:  {failed('bad CSV structure (decrypted)')}")
        print(f"Expected Columns:   {gt_structure['expected_columns']}")
        print(f"Bad Row Count:      {len(gt_structure['bad_rows'])}")
        print("*****************************************")
        raise RuntimeError("Stop scoring because ground-truth CSV structure is invalid.")

    gt_df = pd.read_csv(io.StringIO(gt_text))
    pred_df = pd.read_csv(PREDICT_FILE, encoding="utf-8-sig")

    check_required_columns(gt_df, GT_REQUIRED_COLUMNS, GROUND_TRUTH_ENC)
    check_required_columns(pred_df, PRED_REQUIRED_COLUMNS, PREDICT_FILE)

    prob_check = check_prediction_probability_matches_y_pred(pred_df)

    if not prob_check["status"]:
        mismatch_path = BASE_DIR / "prediction_probability_mismatch_rows.csv"
        prob_check["details"].to_csv(mismatch_path, index=False)

        print("\n********** Prediction Probability Check **********")
        print(f"Timestamp:          {now}")
        print(f"Status:             {failed('probability columns do not match y_pred/y_label_pred')}")
        print(f"Total Rows:         {prob_check['total_rows']}")
        print(f"Failed Rows:        {prob_check['failed_rows']}")
        print(f"Mismatch File:      {mismatch_path}")
        print("************************************************")
        raise RuntimeError(
            "Stop scoring because y_pred or y_label_pred does not match probability argmax."
        )

    result = compare_with_ground_truth(gt_df, pred_df)

    comparison_path = BASE_DIR / "ground_truth_prediction_comparison.csv"
    metrics_path = BASE_DIR / "classification_metrics_by_class.csv"
    missing_path = BASE_DIR / "missing_prediction_timestamp_rows.csv"
    extra_path = BASE_DIR / "extra_prediction_timestamp_rows.csv"

    result["merged_df"].to_csv(comparison_path, index=False)
    result["class_metric_df"].to_csv(metrics_path, index=False)
    result["missing_prediction_df"].to_csv(missing_path, index=False)
    result["extra_prediction_df"].to_csv(extra_path, index=False)

    score_summary_path = BASE_DIR / "score_method_summary.csv"
    pd.DataFrame([{
        "score_method": "direct_prediction_vs_ground_truth",
        "mapping_key": "timestamp",
        "ground_truth_rows": result["ground_truth_rows"],
        "prediction_rows": result["prediction_rows"],
        "matched_timestamp_rows": result["matched_timestamp_rows"],
        "missing_prediction_rows": result["missing_prediction_rows"],
        "extra_prediction_rows": result["extra_prediction_rows"],
        "correct_prediction_rows": result["correct_prediction_rows"],
        "wrong_prediction_rows": result["wrong_prediction_rows"],
        "prediction_compare_accuracy": result["prediction_compare_accuracy"],
        "phase4_score_out_of_25": result["prediction_compare_score_out_of_25"],
        "macro_f1_diagnostic": result["macro_f1"],
        "macro_f1_score_out_of_25_diagnostic": result["macro_f1_score_out_of_25_diagnostic"],
    }]).to_csv(score_summary_path, index=False)

    end_time = time.perf_counter()
    runtime_seconds = end_time - start_time

    print("\n********** Final Summary Score **********")
    print(f"Timestamp:                  {now}")
    print(f"Runtime:                    {runtime_seconds:.4f} seconds")
    print("----------------------------------------")
    print(f"Ground Truth Integrity:     {passed('decrypted and authenticated (HMAC) with password')}")
    print(f"CSV Structure Check:        {passed('valid CSV structure')}")
    print(f"Probability Check:          {passed('all probability rows match y_pred/y_label_pred')}")
    print("----------------------------------------")
    print("Scoring Mapping:            Ground-truth LEFT JOIN prediction by timestamp")
    print("Scoring Method:             Direct y_pred vs environment_state comparison")
    print("Missing Prediction Rule:    Missing timestamp predictions are counted as wrong")
    print("----------------------------------------")
    print(f"Ground Truth Rows:          {result['ground_truth_rows']}")
    print(f"Prediction Rows:            {result['prediction_rows']}")
    print(f"Matched Timestamp Rows:     {result['matched_timestamp_rows']}")
    print(f"Missing Prediction Rows:    {result['missing_prediction_rows']}")
    print(f"Extra Prediction Rows:      {result['extra_prediction_rows']}")
    print("----------------------------------------")
    print(f"Correct Prediction Rows:    {result['correct_prediction_rows']}")
    print(f"Wrong Prediction Rows:      {result['wrong_prediction_rows']}")
    print(f"Prediction-vs-GT Accuracy:  {result['prediction_compare_accuracy']:.4f}")
    print(f"Label Match Accuracy:       {result['label_match_accuracy']:.4f}")
    print("----------------------------------------")
    print("Diagnostic Metrics Only:")
    print(f"Macro Precision:            {result['macro_precision']:.4f}")
    print(f"Macro Recall:               {result['macro_recall']:.4f}")
    print(f"Macro-F1:                   {result['macro_f1']:.4f}")
    print(f"Macro-F1 Score / 25:        {result['macro_f1_score_out_of_25_diagnostic']:.4f}")
    print("----------------------------------------")
    print(f"PHASE4: Final Score / 25:   {result['prediction_compare_score_out_of_25']:.4f}")
    print(f"Max Score:                  {result['max_score']}")
    print("----------------------------------------")
    print(f"Saved Comparison File:      {comparison_path}")
    print(f"Saved Class Metrics File:   {metrics_path}")
    print(f"Saved Missing Rows File:    {missing_path}")
    print(f"Saved Extra Rows File:      {extra_path}")
    print(f"Saved Score Summary File:   {score_summary_path}")
    print("****************************************")

# ============================================================
# LOCK CLI
# ============================================================
def lock_cli(targets):
    targets = targets or ["plant_unseen_score_final.csv"]

    missing = [t for t in targets if not Path(t).exists()]
    if missing:
        print("These files were not found:", ", ".join(missing))
        sys.exit(1)

    pw = getpass.getpass("Set a password to lock the ground-truth file(s): ")
    pw2 = getpass.getpass("Confirm password: ")

    if pw != pw2:
        print("Passwords do not match. Nothing was locked.")
        sys.exit(1)

    if not pw:
        print("Empty password rejected.")
        sys.exit(1)

    for t in targets:
        out = str(t) + ".enc"
        encrypt_file(t, out, pw)
        decrypt_text(out, pw)
        print(f"Locked {t} -> {out}")

    print("\nDone. The .enc file(s) are verified readable with your password.")
    print("Now delete the plaintext CSV(s) so they cannot be opened:")
    for t in targets:
        print(f"  rm {t}")

if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "--lock":
        lock_cli(sys.argv[2:])
    else:
        main()
