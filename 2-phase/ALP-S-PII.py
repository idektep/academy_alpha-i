import io
import os
import csv
import json
import hmac
import hashlib
import getpass
from pathlib import Path
from datetime import datetime
import time

# ============================================================
# NOTE: CONFIG CHECK TEST_EVAL_METADATA_RF , CHECK TEST
# ============================================================
METADATA_JSON = r"C:\Users\idtcu\Alpha-I\Z\test_eval_metadata_3.json"
TEST_PRED_CSV = r"C:\Users\idtcu\Alpha-I\test\phase1\cpt_plant_valid.csv"



# ============================================================
# COLOR PRINT & GROUNDTRUTH
# ============================================================
BASE_DIR = Path(__file__).resolve().parent
TEST_GT_ENC  = BASE_DIR / "cpt_plant_valid_p2.csv.enc"
MAX_SCORE = 25
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
RESET = "\033[0m"

MAX_TIME_DIFF_SECONDS = 0.5

KEY_COL = "timestamp"
TEST_REQUIRED_COLUMNS = [
    "timestamp",
    "Temperature_degree_celcius",
    "Humidity_percentage",
    "Lux_lumen",
    "VPD_Kilopascal",
    "environment_state",
    "environment_state_label",
]
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"

COMPARE_COLUMNS = [c for c in TEST_REQUIRED_COLUMNS if c != KEY_COL]
NUMERIC_TOL = 1e-9

SALT_SIZE = 16
NONCE_SIZE = 16
MAC_SIZE = 32
PBKDF2_ITERATIONS = 200_000

def green_pass(text):
    return f"{GREEN}PASSED{RESET} - {text}"

def red_fail(text):
    return f"{RED}FAILED{RESET} - {text}"

def yellow_warn(text):
    return f"{YELLOW}WARNING{RESET} - {text}"

# ============================================================
# FILE INTEGRITY CHECK  
# ============================================================
def get_creation_time(stat_result):
    birthtime = getattr(stat_result, "st_birthtime", None)
    if birthtime is not None:
        return birthtime, "birthtime"
    if os.name == "nt":
        return stat_result.st_ctime, "win_creation"
    return stat_result.st_ctime, "ctime_fallback"

def evaluate_integrity(created, modified, max_diff_seconds=MAX_TIME_DIFF_SECONDS):
    diff = abs(modified - created)
    return diff <= max_diff_seconds, diff

def check_file_not_modified(file_path, max_diff_seconds=MAX_TIME_DIFF_SECONDS):
    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    st = file_path.stat()
    modified = st.st_mtime
    accessed = st.st_atime
    size = st.st_size
    created, time_source = get_creation_time(st)

    ok, diff = evaluate_integrity(created, modified, max_diff_seconds)

    if ok:
        message = green_pass(
            f"{diff:.4f}s "
        )
    else:
        message = red_fail(
            f"file was modified (created->modified gap {diff:.4f}s "
            f"> {max_diff_seconds}s)"
        )

    return {
        "status": ok,
        "message": message,
        "size_bytes": size,
        "created": created,
        "modified": modified,
        "accessed": accessed,
        "diff_seconds": diff,
        "max_diff_seconds": max_diff_seconds,
        "time_source": time_source,
    }

# ============================================================
# LOCKED TEST-DATA: 
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
        out.extend(hmac.new(enc_key, nonce + counter.to_bytes(8, "big"),
                            hashlib.sha256).digest())
        counter += 1
    return bytes(out[:length])

def _xor(data, keystream):
    if not data:
        return b""
    return (int.from_bytes(data, "big") ^ int.from_bytes(keystream, "big")).to_bytes(
        len(data), "big"
    )

def decrypt_text(encrypted_path, password):
    blob = Path(encrypted_path).read_bytes()
    if len(blob) < SALT_SIZE + NONCE_SIZE + MAC_SIZE:
        raise ValueError("Corrupted encrypted test-data file (too short).")
    salt = blob[:SALT_SIZE]
    nonce = blob[SALT_SIZE:SALT_SIZE + NONCE_SIZE]
    tag = blob[SALT_SIZE + NONCE_SIZE:SALT_SIZE + NONCE_SIZE + MAC_SIZE]
    ciphertext = blob[SALT_SIZE + NONCE_SIZE + MAC_SIZE:]
    enc_key, mac_key = _derive_keys(password, salt)
    expected = hmac.new(mac_key, salt + nonce + ciphertext, hashlib.sha256).digest()
    if not hmac.compare_digest(tag, expected):
        raise ValueError("Wrong password or corrupted encrypted test-data file.")
    return _xor(ciphertext, _keystream(enc_key, nonce, len(ciphertext))).decode("utf-8")

def validate_test_data_text(csv_text,
                            required_columns=TEST_REQUIRED_COLUMNS,
                            key_col=KEY_COL,
                            ts_format=TIMESTAMP_FORMAT):
    reader = csv.reader(io.StringIO(csv_text))
    try:
        header = next(reader)
    except StopIteration:
        return {"status": False, "message": red_fail("test data is empty (no header)"),
                "row_count": 0, "missing_columns": list(required_columns)}

    header = [h.strip() for h in header]
    missing = [c for c in required_columns if c not in header]
    if missing:
        return {"status": False,
                "message": red_fail(f"test data missing required columns: {missing}"),
                "row_count": 0, "missing_columns": missing}

    if key_col not in header:
        return {"status": False,
                "message": red_fail(f"key column '{key_col}' not found in test data"),
                "row_count": 0, "missing_columns": [key_col]}

    key_idx = header.index(key_col)
    n_cols = len(header)
    seen = set()
    row_count = 0
    bad_ts = []
    dup_ts = []
    ragged = []

    for line_no, row in enumerate(reader, start=2):
        if len(row) != n_cols:
            ragged.append(line_no)
            continue
        row_count += 1
        key_val = row[key_idx].strip()
        if key_val == "":
            bad_ts.append((line_no, "<empty>"))
            continue
        try:
            datetime.strptime(key_val, ts_format)
        except ValueError:
            bad_ts.append((line_no, key_val))
            continue
        if key_val in seen:
            dup_ts.append((line_no, key_val))
        else:
            seen.add(key_val)

    if ragged:
        return {"status": False,
                "message": red_fail(f"test data has ragged rows at lines {ragged[:5]}"),
                "row_count": row_count, "missing_columns": []}
    if bad_ts:
        return {"status": False,
                "message": red_fail(
                    f"test data has invalid '{key_col}' values "
                    f"(expected '{ts_format}'), e.g. {bad_ts[:3]}"),
                "row_count": row_count, "missing_columns": []}
    if dup_ts:
        return {"status": False,
                "message": red_fail(
                    f"test data has duplicate '{key_col}' keys, e.g. {dup_ts[:3]}"),
                "row_count": row_count, "missing_columns": []}
    if row_count == 0:
        return {"status": False,
                "message": red_fail("test data has a header but no data rows"),
                "row_count": 0, "missing_columns": []}

    return {"status": True,
            "message": green_pass(
                f"test data valid: {row_count} rows, keyed by '{key_col}', "
                f"columns OK"),
            "row_count": row_count, "missing_columns": []}

# ============================================================
# COMPARE PREDICTION FILE vs GROUND TRUTH 
# ============================================================
def _is_missing(value):
    if value is None:
        return True
    return str(value).strip().lower() in ("", "nan", "none", "null")

def cells_equal(a, b, numeric_tol=NUMERIC_TOL):
    if _is_missing(a) and _is_missing(b):
        return True
    if _is_missing(a) or _is_missing(b):
        return False
    try:
        return abs(float(a) - float(b)) <= numeric_tol
    except (ValueError, TypeError):
        pass
    return str(a).strip().lower() == str(b).strip().lower()

def _index_by_key(csv_text, key_col):
    reader = csv.DictReader(io.StringIO(csv_text))
    header = [h.strip() for h in (reader.fieldnames or [])]
    index = {}
    dup_keys = []
    for raw in reader:
        row = {(k.strip() if k else k): v for k, v in raw.items()}
        if key_col not in row:
            continue
        key_val = (row[key_col] or "").strip()
        if key_val in index:
            dup_keys.append(key_val)
        else:
            index[key_val] = row
    return index, header, dup_keys

def compare_pred_with_ground_truth(pred_text, gt_text,
                                   key_col=KEY_COL,
                                   compare_columns=None,
                                   numeric_tol=NUMERIC_TOL):
    if compare_columns is None:
        compare_columns = COMPARE_COLUMNS

    pred_idx, pred_header, pred_dups = _index_by_key(pred_text, key_col)
    gt_idx, gt_header, gt_dups = _index_by_key(gt_text, key_col)

    if key_col not in pred_header:
        return {"status": False,
                "message": red_fail(f"prediction file has no key column '{key_col}'")}
    if pred_dups:
        return {"status": False,
                "message": red_fail(
                    f"prediction file has duplicate '{key_col}' keys, e.g. {pred_dups[:3]}")}
    if gt_dups:
        return {"status": False,
                "message": red_fail(
                    f"ground truth has duplicate '{key_col}' keys, e.g. {gt_dups[:3]}")}

    gt_keys = set(gt_idx)
    pred_keys = set(pred_idx)
    missing_in_pred = sorted(gt_keys - pred_keys)   
    extra_in_pred = sorted(pred_keys - gt_keys)     

    usable_cols = [c for c in compare_columns if c in pred_header and c in gt_header]
    requested_missing = [c for c in compare_columns
                         if c not in pred_header or c not in gt_header]

    matched_cells = 0
    total_cells = 0
    mismatches = []
    for key in sorted(gt_keys & pred_keys):
        for col in usable_cols:
            total_cells += 1
            if cells_equal(gt_idx[key].get(col), pred_idx[key].get(col), numeric_tol):
                matched_cells += 1
            else:
                mismatches.append((key, col, gt_idx[key].get(col), pred_idx[key].get(col)))

    keys_match = not missing_in_pred and not extra_in_pred
    cells_match = (len(mismatches) == 0)
    status = keys_match and cells_match and not requested_missing

    if status:
        message = green_pass(
            f"prediction matches ground truth: {len(pred_keys)} rows, "
            f"{matched_cells}/{total_cells} compared cells equal")
    else:
        parts = []
        if missing_in_pred:
            parts.append(f"{len(missing_in_pred)} GT rows missing in prediction "
                         f"(e.g. {missing_in_pred[:3]})")
        if extra_in_pred:
            parts.append(f"{len(extra_in_pred)} extra prediction rows "
                         f"(e.g. {extra_in_pred[:3]})")
        if requested_missing:
            parts.append(f"columns not present in both files: {requested_missing}")
        if mismatches:
            ex = mismatches[0]
            parts.append(f"{len(mismatches)} cell mismatches "
                         f"(e.g. key={ex[0]} col={ex[1]} gt={ex[2]!r} pred={ex[3]!r})")
        message = red_fail("prediction does not match ground truth: " + "; ".join(parts))

    return {
        "status": status,
        "message": message,
        "ground_truth_rows": len(gt_keys),
        "prediction_rows": len(pred_keys),
        "matched_cells": matched_cells,
        "total_cells": total_cells,
        "missing_in_pred": missing_in_pred,
        "extra_in_pred": extra_in_pred,
        "compared_columns": usable_cols,
        "mismatches": mismatches,
    }

# ============================================================
# SCORE FROM METADATA JSON
# ============================================================
def load_eval_metadata(json_path):
    with open(json_path, "r", encoding="utf-8") as f:
        return json.load(f)

def get_eval_scores(metadata):
    accuracy = metadata.get("accuracy")

    macro_f1 = metadata.get("macro_f1")

    classification_report = metadata.get("classification_report", {})

    if macro_f1 is None:
        macro_f1 = (
            classification_report
            .get("macro avg", {})
            .get("f1-score")
        )

    weighted_f1 = (
        classification_report
        .get("weighted avg", {})
        .get("f1-score")
    )

    macro_f1_score_out_of_25 = macro_f1 * MAX_SCORE if macro_f1 is not None else None

    return {
        "accuracy": accuracy,
        "f1_weighted": weighted_f1,
        "macro_f1": macro_f1,
        "macro_f1_score_out_of_25": macro_f1_score_out_of_25,
        "max_score": MAX_SCORE
    }

# ============================================================
# MAIN RUN
# ============================================================
def _fmt_ts(epoch):
    return datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S.%f")

def main():
    start_time = time.perf_counter()

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    integrity_result = check_file_not_modified(METADATA_JSON)

    if integrity_result["time_source"] == "ctime_fallback":
        print(yellow_warn(
            "This OS does not expose a true file creation time; using "
            "st_ctime (inode-change time) as a fallback. The created vs. "
            "modified check is unreliable here and should be run on the "
            "Windows/macOS host where the file actually lives."
        ))

    if not integrity_result["status"]:
        print("\n********** File Integrity Check **********")
        print(f"Timestamp:          {now}")
        print(integrity_result["message"])
        print(f"File size:          {integrity_result['size_bytes']} bytes")
        print(f"Created:            {_fmt_ts(integrity_result['created'])}")
        print(f"Last modified:      {_fmt_ts(integrity_result['modified'])}")
        print(f"Last accessed:      {_fmt_ts(integrity_result['accessed'])}")
        print(f"Gap:                {integrity_result['diff_seconds']:.4f} s "
              f"(limit {integrity_result['max_diff_seconds']} s)")
        print("******************************************")

        raise RuntimeError("Stop scoring because the metadata file was modified.")

    password = getpass.getpass("Enter password to unlock test data: ")
    try:
        gt_csv_text = decrypt_text(TEST_GT_ENC, password)
    except ValueError as e:
        print("\n********** Test Data Check **********")
        print(f"Timestamp:          {now}")
        print(red_fail(str(e)))
        print("*************************************")
        raise RuntimeError("Stop scoring because the test data could not be unlocked.")

    gt_check = validate_test_data_text(gt_csv_text)
    if not gt_check["status"]:
        print("\n********** Test Data Check **********")
        print(f"Timestamp:          {now}")
        print(gt_check["message"])
        print("*************************************")
        raise RuntimeError("Stop scoring because the ground-truth test data is invalid.")

    pred_path = Path(TEST_PRED_CSV)
    if not pred_path.exists():
        print("\n********** Prediction vs Ground Truth **********")
        print(f"Timestamp:          {now}")
        print(red_fail(f"prediction file not found: {pred_path}"))
        print("***********************************************")
        raise RuntimeError("Stop scoring because the prediction file is missing.")

    pred_csv_text = pred_path.read_text(encoding="utf-8")
    compare_result = compare_pred_with_ground_truth(pred_csv_text, gt_csv_text)
    if not compare_result["status"]:
        print("\n********** Prediction vs Ground Truth **********")
        print(f"Timestamp:          {now}")
        print(compare_result["message"])
        print(f"Ground Truth Rows:  {compare_result['ground_truth_rows']}")
        print(f"Prediction Rows:    {compare_result['prediction_rows']}")
        print(f"Compared Columns:   {compare_result['compared_columns']}")
        print(f"Matched Cells:      {compare_result['matched_cells']} / {compare_result['total_cells']}")
        print("***********************************************")
        raise RuntimeError("Stop scoring because the prediction file does not match ground truth.")

    metadata = load_eval_metadata(METADATA_JSON)
    score_result = get_eval_scores(metadata)

    end_time = time.perf_counter()
    runtime_seconds = end_time - start_time

    print("\n********** Final Summary Score **********")
    print(f"Timestamp:              {now}")
    print(f"Runtime:                {runtime_seconds:.4f} seconds")
    print(f"File Integrity:          {integrity_result['message']}")
    print(f"File size:              {integrity_result['size_bytes']} bytes")
    print(f"Created:                {_fmt_ts(integrity_result['created'])}")
    print(f"Last modified:          {_fmt_ts(integrity_result['modified'])}")
    print(f"Last accessed:          {_fmt_ts(integrity_result['accessed'])}")
    print(f"Ground Truth Check:     {gt_check['message']}")
    print(f"Pred vs GT Check:       {compare_result['message']}")
    print("----------------------------------------")
    print(f"Accuracy:               {score_result['accuracy']:.4f}")
    print(f"F1 Weighted:            {score_result['f1_weighted']:.4f}")
    print(f"Macro-F1:               {score_result['macro_f1']:.4f}")
    print("----------------------------------------")
    print(f"PHASE2: Macro-F1 Score / 25:    {score_result['macro_f1_score_out_of_25']:.4f}")
    print(f"Max Score:                      {score_result['max_score']}")
    print("****************************************")

if __name__ == "__main__":
    main()