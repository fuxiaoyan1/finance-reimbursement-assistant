#!/usr/bin/env python3
from __future__ import annotations

import datetime as dt
import json
import os
import re
import subprocess
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

from flask import Flask, jsonify, request, send_from_directory

from invoice_agent import GLOBAL_DATA_DIR, GLOBAL_REIMBURSEMENT_LEDGER, load_json_file, save_json_file


APP_ROOT = Path(__file__).resolve().parent
STATIC_DIR = APP_ROOT / "web" / "static"
DATA_DIR = APP_ROOT / "data"
UI_STATE_FILE = DATA_DIR / "ui_state.json"
APP_NAME = "Finance Reimbursement Assistant"
INVOICE_JOB_TIMEOUT_SECONDS = 60 * 30
INVOICE_JOB_HISTORY_LIMIT = 20
INVOICE_JOBS: dict[str, dict[str, Any]] = {}
INVOICE_JOBS_LOCK = threading.Lock()

app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path="/static")


def money(value: Any) -> float:
    try:
        return round(float(value or 0), 2)
    except (TypeError, ValueError):
        return 0.0


def current_year_month() -> tuple[int, int]:
    today = dt.date.today()
    return today.year, today.month


def current_month_key() -> str:
    year, month = current_year_month()
    return f"{year}-{month:02d}"


def now_iso() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def compact_process_output(text: str, max_lines: int = 12) -> str:
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return "\n".join(lines[-max_lines:])


def read_ui_state() -> dict[str, Any]:
    state = load_json_file(UI_STATE_FILE, {})
    if not isinstance(state, dict):
        state = {}
    if state.get("last_invoice_dir"):
        return state
    latest_dir = latest_reimbursement_dir()
    if latest_dir:
        state["last_invoice_dir"] = latest_dir
    return state


def save_ui_state(**updates: Any) -> dict[str, Any]:
    state = read_ui_state()
    state.update({key: value for key, value in updates.items() if value is not None})
    save_json_file(UI_STATE_FILE, state)
    return state


def latest_reimbursement_dir() -> str:
    data = load_json_file(GLOBAL_REIMBURSEMENT_LEDGER, {"batches": []})
    batches = data.get("batches", [])
    if not batches:
        return ""
    latest = max(batches, key=lambda item: item.get("reimbursed_at", ""))
    return str(latest.get("invoice_dir") or "")


def applescript_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def choose_directory(default_dir: Path | None = None) -> str:
    if sys.platform == "darwin":
        prompt = applescript_quote("选择新报销发票目录")
        if default_dir and default_dir.exists() and default_dir.is_dir():
            default_path = applescript_quote(str(default_dir))
            script = f"""
try
  set pickedFolder to choose folder with prompt {prompt} default location (POSIX file {default_path} as alias)
on error number -128
  return ""
end try
POSIX path of pickedFolder
"""
        else:
            script = f"""
try
  set pickedFolder to choose folder with prompt {prompt}
on error number -128
  return ""
end try
POSIX path of pickedFolder
"""
        result = subprocess.run(["osascript", "-e", script], text=True, capture_output=True, check=False)
        if result.returncode != 0:
            raise RuntimeError(compact_process_output(result.stderr or result.stdout or "目录选择失败", max_lines=4))
        return result.stdout.strip()
    # Windows / Linux: 使用 PowerShell 系统文件夹选择框 (不依赖 Python 的 tcl/tk)
    ps1 = APP_ROOT / "scripts" / "choose_folder.ps1"
    if not ps1.exists():
        raise RuntimeError(f"缺少目录选择脚本: {ps1}")
    env = os.environ.copy()
    if default_dir and default_dir.exists():
        env["FAPIAO_DEFAULT_DIR"] = str(default_dir)
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-STA", "-ExecutionPolicy", "Bypass", "-File", str(ps1)],
            text=True,
            capture_output=True,
            check=False,
            env=env,
            timeout=120,
        )
    except Exception as exc:
        raise RuntimeError(f"目录选择对话框不可用: {exc}")
    if result.returncode != 0:
        raise RuntimeError(compact_process_output(result.stderr or result.stdout or "目录选择失败", max_lines=4))
    selected = result.stdout.strip()
    return "" if not selected else os.path.normpath(selected)


def parse_invoice_agent_output(stdout: str) -> dict[str, Any]:
    output: dict[str, Any] = {}
    count_match = re.search(r"发票数量:\s*([0-9]+)，已识别:\s*([0-9]+)，需复核:\s*([0-9]+)", stdout)
    if count_match:
        output["file_count"] = int(count_match.group(1))
        output["recognized_count"] = int(count_match.group(2))
        output["review_count"] = int(count_match.group(3))
    amount_match = re.search(r"识别金额合计:\s*¥?([0-9,]+(?:\.[0-9]{1,2})?)", stdout)
    if amount_match:
        output["total_amount"] = money(amount_match.group(1).replace(",", ""))
    path_labels = {
        "workbook_path": "报销表",
        "pdf_path": "发票附件PDF",
        "json_path": "识别JSON",
    }
    for key, label in path_labels.items():
        match = re.search(rf"{label}:\s*(.+)", stdout)
        if match:
            output[key] = match.group(1).strip()
    return output


def build_invoice_agent_command(invoice_dir: Path, payload: dict[str, Any]) -> list[str]:
    cmd = [
        sys.executable,
        str(APP_ROOT / "invoice_agent.py"),
        str(invoice_dir),
        "-o",
        str(invoice_dir),
        "--mark-reimbursed",
        "--save-json",
    ]
    for payload_key, cli_flag in [
        ("applicant", "--applicant"),
        ("department", "--department"),
        ("reason", "--reason"),
    ]:
        value = str(payload.get(payload_key, "")).strip()
        if value:
            cmd.extend([cli_flag, value])
    if payload.get("no_llm"):
        cmd.append("--no-llm")
    if payload.get("llm_all"):
        cmd.append("--llm-all")
    return cmd


def job_snapshot(job_id: str) -> dict[str, Any]:
    with INVOICE_JOBS_LOCK:
        return dict(INVOICE_JOBS.get(job_id, {}))


def save_job_update(job_id: str, **updates: Any) -> None:
    with INVOICE_JOBS_LOCK:
        if job_id in INVOICE_JOBS:
            INVOICE_JOBS[job_id].update(updates)


def prune_invoice_jobs() -> None:
    with INVOICE_JOBS_LOCK:
        if len(INVOICE_JOBS) <= INVOICE_JOB_HISTORY_LIMIT:
            return
        sorted_jobs = sorted(INVOICE_JOBS.items(), key=lambda item: item[1].get("created_at", ""))
        for job_id, _job in sorted_jobs[: len(INVOICE_JOBS) - INVOICE_JOB_HISTORY_LIMIT]:
            INVOICE_JOBS.pop(job_id, None)


def run_invoice_job(job_id: str, cmd: list[str]) -> None:
    save_job_update(job_id, status="running", started_at=now_iso(), message="正在处理")
    try:
        result = subprocess.run(
            cmd,
            cwd=str(APP_ROOT),
            text=True,
            capture_output=True,
            timeout=INVOICE_JOB_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        save_job_update(
            job_id,
            status="error",
            finished_at=now_iso(),
            message=f"处理超时，超过 {INVOICE_JOB_TIMEOUT_SECONDS // 60} 分钟",
            stdout=compact_process_output(exc.stdout or ""),
            stderr=compact_process_output(exc.stderr or ""),
        )
        return
    except Exception as exc:
        save_job_update(job_id, status="error", finished_at=now_iso(), message=str(exc))
        return

    combined_output = "\n".join(part for part in [result.stdout, result.stderr] if part)
    parsed_output = parse_invoice_agent_output(result.stdout or "")
    if result.returncode == 0:
        status = "success"
        message = "处理完成，已登记为已报销"
    elif result.returncode == 1 and "未在目录中找到新的未报销发票文件" in combined_output:
        status = "no_new"
        message = "该目录暂无新的未报销发票"
    else:
        status = "error"
        message = compact_process_output(result.stderr or result.stdout or "处理失败", max_lines=6)

    save_job_update(
        job_id,
        status=status,
        finished_at=now_iso(),
        returncode=result.returncode,
        message=message,
        stdout=compact_process_output(result.stdout or ""),
        stderr=compact_process_output(result.stderr or ""),
        output=parsed_output,
    )
    prune_invoice_jobs()


def start_invoice_processing_job(invoice_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    with INVOICE_JOBS_LOCK:
        for job in INVOICE_JOBS.values():
            if job.get("invoice_dir") == str(invoice_dir) and job.get("status") in {"queued", "running"}:
                return dict(job)
    job_id = uuid.uuid4().hex
    cmd = build_invoice_agent_command(invoice_dir, payload)
    job = {
        "id": job_id,
        "status": "queued",
        "created_at": now_iso(),
        "invoice_dir": str(invoice_dir),
        "message": "已加入处理队列",
        "output": {},
    }
    with INVOICE_JOBS_LOCK:
        INVOICE_JOBS[job_id] = job
    thread = threading.Thread(target=run_invoice_job, args=(job_id, cmd), daemon=True)
    thread.start()
    return job_snapshot(job_id)


def read_reimbursement_months() -> list[dict]:
    data = load_json_file(GLOBAL_REIMBURSEMENT_LEDGER, {"batches": []})
    grouped: dict[str, dict] = {}
    for batch in data.get("batches", []):
        reimbursed_at = batch.get("reimbursed_at", "")
        month = ""
        if len(reimbursed_at) >= 7:
            month = reimbursed_at[:7]
        if not month:
            month = Path(batch.get("invoice_dir", "")).name[-6:]
            if len(month) == 6 and month.isdigit():
                month = f"{month[:4]}-{month[4:]}"
        if not month:
            continue
        item = grouped.setdefault(
            month,
            {
                "month": month,
                "label": f"{month[:4]}年{int(month[5:7])}月",
                "batch_count": 0,
                "file_count": 0,
                "included_count": 0,
                "total_amount": 0.0,
                "batches": [],
            },
        )
        item["batch_count"] += 1
        item["file_count"] += int(batch.get("file_count") or 0)
        item["included_count"] += int(batch.get("included_count") or 0)
        item["total_amount"] = round(item["total_amount"] + money(batch.get("total_amount")), 2)
        item["batches"].append(batch)
    return [grouped[key] for key in sorted(grouped.keys(), reverse=True)]


@app.get("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.get("/finance")
def finance_page():
    return send_from_directory(STATIC_DIR, "finance.html")


@app.get("/api/overview")
def api_overview():
    return jsonify({"app": APP_NAME, "today": dt.date.today().isoformat()})


@app.get("/healthz")
def healthz():
    return jsonify({"ok": True, "service": "finance-assistant", "today": dt.date.today().isoformat()})


@app.get("/api/finance")
def api_finance():
    ui_state = read_ui_state()
    return jsonify(
        {
            "reimbursements": read_reimbursement_months(),
            "last_invoice_dir": ui_state.get("last_invoice_dir", ""),
            "current_month": current_month_key(),
            "generated_at": now_iso(),
        }
    )


@app.get("/api/ui-state")
def api_ui_state():
    return jsonify(read_ui_state())


@app.post("/api/select-invoice-dir")
def api_select_invoice_dir():
    payload = request.get_json(force=True) or {}
    default_raw = str(payload.get("default_dir") or read_ui_state().get("last_invoice_dir") or "").strip()
    default_dir = Path(default_raw).expanduser().resolve() if default_raw else None
    try:
        selected = choose_directory(default_dir)
    except RuntimeError as exc:
        return jsonify({"error": str(exc)}), 500
    if not selected:
        return jsonify({"cancelled": True, "last_invoice_dir": read_ui_state().get("last_invoice_dir", "")})
    selected_dir = Path(selected).expanduser().resolve()
    if not selected_dir.exists() or not selected_dir.is_dir():
        return jsonify({"error": f"选择结果不是有效文件夹: {selected_dir}"}), 400
    save_ui_state(last_invoice_dir=str(selected_dir))
    return jsonify({"invoice_dir": str(selected_dir), "cancelled": False})


@app.get("/api/invoice-jobs")
def api_invoice_jobs():
    with INVOICE_JOBS_LOCK:
        jobs = sorted(INVOICE_JOBS.values(), key=lambda item: item.get("created_at", ""), reverse=True)
        return jsonify({"jobs": jobs[:INVOICE_JOB_HISTORY_LIMIT]})


@app.post("/api/invoice-jobs")
def api_create_invoice_job():
    payload = request.get_json(force=True) or {}
    raw_invoice_dir = str(payload.get("invoice_dir", "")).strip()
    if not raw_invoice_dir:
        return jsonify({"error": "invoice_dir is required"}), 400
    invoice_dir = Path(raw_invoice_dir).expanduser().resolve()
    if not invoice_dir.exists() or not invoice_dir.is_dir():
        return jsonify({"error": f"发票目录不存在或不是文件夹: {invoice_dir}"}), 400
    save_ui_state(last_invoice_dir=str(invoice_dir))
    return jsonify(start_invoice_processing_job(invoice_dir, payload)), 202


@app.get("/api/invoice-jobs/<job_id>")
def api_invoice_job(job_id: str):
    job = job_snapshot(job_id)
    if not job:
        return jsonify({"error": "job not found"}), 404
    return jsonify(job)


@app.post("/api/open-file")
def api_open_file():
    payload = request.get_json(force=True) or {}
    path = Path(str(payload.get("path", ""))).expanduser()
    if not path.exists() or not path.is_file():
        return jsonify({"error": "file not found"}), 404
    if sys.platform == "win32":
        os.startfile(str(path))
    elif sys.platform == "darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)
    return jsonify({"opened": str(path)})


if __name__ == "__main__":
    GLOBAL_DATA_DIR.mkdir(parents=True, exist_ok=True)
    port = int(os.getenv("PORT", "5055"))
    app.run(host="127.0.0.1", port=port, debug=False)
