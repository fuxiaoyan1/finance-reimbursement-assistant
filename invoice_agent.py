#!/usr/bin/env python3
"""
Local invoice reimbursement assistant.

Reads a folder of invoice PDFs/images, extracts key invoice fields, creates a
two-sheet reimbursement workbook, and combines source invoices into one PDF.
"""

from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import hashlib
import json
import os
import re
import textwrap
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Iterable

try:
    import httpx
except Exception:  # pragma: no cover - optional HTTP adapter
    httpx = None

try:
    import fitz  # PyMuPDF
except Exception as exc:  # pragma: no cover - startup guard
    raise SystemExit("缺少 PyMuPDF(fitz)，请先安装 pymupdf。") from exc

try:
    from PIL import Image, ImageOps, ImageEnhance
except Exception as exc:  # pragma: no cover - startup guard
    raise SystemExit("缺少 Pillow，请先安装 pillow。") from exc

try:
    from openpyxl import Workbook
    from openpyxl.comments import Comment
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter
except Exception as exc:  # pragma: no cover - startup guard
    raise SystemExit("缺少 openpyxl，请先安装 openpyxl。") from exc


SUPPORTED_PDF = {".pdf"}
SUPPORTED_IMAGES = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
SUPPORTED_EXTENSIONS = SUPPORTED_PDF | SUPPORTED_IMAGES
LEDGER_FILENAME = ".invoice_agent_ledger.json"

DEFAULT_CATEGORY_RULES = {
    "交通费": ["滴滴", "出租", "taxi", "火车", "铁路", "机票", "航空", "地铁", "公交", "停车", "高速", "加油"],
    "住宿费": ["住宿", "酒店", "宾馆", "旅店"],
    "餐饮费": ["餐饮", "餐费", "餐厅", "食品", "外卖", "咖啡", "茶饮"],
    "办公费": ["办公", "文具", "打印", "耗材", "软件", "订阅", "服务费"],
    "通讯费": ["通讯", "电话", "移动", "联通", "电信", "宽带"],
    "快递物流": ["快递", "物流", "运输", "邮政", "顺丰", "中通", "圆通", "韵达"],
}

AGENT_ROOT = Path(__file__).resolve().parent
AGENT_PROMPT_PATH = AGENT_ROOT / "AGENT.md"
SKILLS_ROOT = AGENT_ROOT / "skills"
GLOBAL_DATA_DIR = AGENT_ROOT / "data"
GLOBAL_REIMBURSEMENT_LEDGER = GLOBAL_DATA_DIR / "reimbursement_batches.json"
DEFAULT_LLM_MODEL = ""
DEFAULT_LLM_PROVIDER = "codex"
DEFAULT_LLM_BASE_URL = ""
LLM_TEXT_LIMIT = 12000

INVOICE_JSON_SCHEMA = {
    "name": "invoice_reimbursement_record",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "is_invoice": {"type": "boolean"},
            "is_reimbursement_attachment": {"type": "boolean"},
            "invoice_code": {"type": "string"},
            "invoice_number": {"type": "string"},
            "invoice_date": {"type": "string"},
            "seller": {"type": "string"},
            "buyer": {"type": "string"},
            "amount": {"type": ["number", "null"]},
            "tax_amount": {"type": ["number", "null"]},
            "total_amount": {"type": ["number", "null"]},
            "category": {"type": "string"},
            "include_in_total": {"type": "boolean"},
            "confidence": {"type": "number"},
            "review_notes": {"type": "array", "items": {"type": "string"}},
        },
        "required": [
            "is_invoice",
            "is_reimbursement_attachment",
            "invoice_code",
            "invoice_number",
            "invoice_date",
            "seller",
            "buyer",
            "amount",
            "tax_amount",
            "total_amount",
            "category",
            "include_in_total",
            "confidence",
            "review_notes",
        ],
    },
}


@dataclasses.dataclass
class InvoiceRecord:
    index: int
    custom_serial: str
    source_path: Path
    file_type: str
    pages: int = 0
    category: str = "待分类"
    invoice_code: str = ""
    invoice_number: str = ""
    invoice_date: str = ""
    seller: str = ""
    buyer: str = ""
    amount: Decimal | None = None
    tax_amount: Decimal | None = None
    total_amount: Decimal | None = None
    include_in_total: bool = True
    extraction_source: str = ""
    llm_model: str = ""
    llm_confidence: float | None = None
    review_status: str = "需复核"
    notes: str = ""
    extracted_text: str = ""

    @property
    def amount_for_total(self) -> Decimal | None:
        return self.total_amount or self.amount

    def to_jsonable(self) -> dict:
        item = dataclasses.asdict(self)
        item["source_path"] = str(self.source_path)
        for key in ["amount", "tax_amount", "total_amount"]:
            item[key] = str(item[key]) if item[key] is not None else None
        return item


@dataclasses.dataclass
class AgentConfig:
    use_llm: bool = True
    llm_provider: str = DEFAULT_LLM_PROVIDER
    llm_model: str = DEFAULT_LLM_MODEL
    llm_base_url: str = DEFAULT_LLM_BASE_URL
    llm_api_key_env: str = "LLM_API_KEY"
    llm_timeout: int = 45
    llm_text_limit: int = LLM_TEXT_LIMIT
    llm_max_files: int = 20
    llm_only_review_items: bool = True
    agent_prompt_path: Path = AGENT_PROMPT_PATH
    skills_root: Path = SKILLS_ROOT


def normalize_text(text: str) -> str:
    text = unicodedata.normalize("NFKC", text or "")
    text = text.replace("\u00a0", " ")
    text = text.replace("￥", "¥")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def compact_text(text: str, limit: int = 800) -> str:
    lines = [re.sub(r"\s+", " ", line).strip() for line in normalize_text(text).splitlines()]
    lines = [line for line in lines if line]
    value = " | ".join(lines)
    return value[:limit]


def parse_decimal(value: str | None) -> Decimal | None:
    if not value:
        return None
    value = unicodedata.normalize("NFKC", value)
    value = value.replace(",", "").replace("¥", "").replace("￥", "").replace("$", "").strip()
    value = re.sub(r"[^0-9.\-]", "", value)
    if not value or value in {".", "-", "-."}:
        return None
    try:
        return Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except InvalidOperation:
        return None


def money_to_float(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)


def decimal_to_json_number(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value)


def coerce_decimal(value) -> Decimal | None:
    if value is None or value == "":
        return None
    return parse_decimal(str(value))


def coerce_bool(value, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {"true", "1", "yes", "y", "是"}


def clamp_confidence(value) -> float | None:
    if value is None or value == "":
        return None
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, confidence))


def first_group(patterns: Iterable[str], text: str, flags: int = re.I) -> str:
    for pattern in patterns:
        match = re.search(pattern, text, flags)
        if match:
            for group in match.groups():
                if group:
                    return group.strip(" :：\t\r\n")
    return ""


def parse_invoice_text(text: str) -> dict:
    text = normalize_text(text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    joined = "\n".join(lines)
    is_invoice = bool(re.search(r"电子发票|发票号码|Invoice\s*(?:No\.?|Number)", joined, re.I))

    invoice_code = first_group(
        [
            r"发票代码[:：]?\s*([0-9]{8,20})",
            r"Invoice\s*Code[:：]?\s*([0-9A-Z\-]{6,30})",
        ],
        joined,
    )
    invoice_number = first_group(
        [
            r"发票(?:号码|编号)[:：]?\s*([0-9A-Z\-]{6,30})",
            r"Invoice\s*(?:No\.?|Number)[:：]?\s*([0-9A-Z\-]{4,30})",
            r"\bNo\.?\s*[:：]?\s*([0-9A-Z\-]{6,30})",
        ],
        joined,
    )
    if not invoice_number and is_invoice:
        invoice_number = infer_invoice_number_from_lines(lines)
    invoice_date = first_group(
        [
            r"开票日期[:：]?\s*([0-9]{4}[年./-][0-9]{1,2}[月./-][0-9]{1,2}日?)",
            r"(?:Date|日期)[:：]?\s*([0-9]{4}[./-][0-9]{1,2}[./-][0-9]{1,2})",
            r"([0-9]{4}年[0-9]{1,2}月[0-9]{1,2}日)",
        ],
        joined,
    )
    invoice_date = standardize_date(invoice_date)

    buyer, seller = infer_parties_from_lines(lines, invoice_number, invoice_date)
    seller = seller or first_group(
        [
            r"(?:销售方|销方|Seller|Vendor)(?:名称|Name)?[:：]?\s*([^\n|]{2,80})",
            r"名称[:：]?\s*([^\n|]{2,80})\s*(?:纳税人识别号|税号)",
        ],
        joined,
    )
    buyer = buyer or first_group(
        [
            r"(?:购买方|购方|Buyer|Customer)(?:名称|Name)?[:：]?\s*([^\n|]{2,80})",
        ],
        joined,
    )

    total_amount = extract_amount(lines, preferred=True)
    amount = extract_amount(lines, preferred=False)
    tax_amount = extract_labeled_amount(lines, ["税额", "Tax"])

    return {
        "invoice_code": invoice_code,
        "invoice_number": invoice_number,
        "invoice_date": invoice_date,
        "seller": clean_party_name(seller),
        "buyer": clean_party_name(buyer),
        "amount": amount,
        "tax_amount": tax_amount,
        "total_amount": total_amount,
        "is_invoice": is_invoice,
    }


def read_text_file(path: Path, fallback: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return fallback


def load_agent_instructions(config: AgentConfig) -> str:
    parts = [
        read_text_file(
            config.agent_prompt_path,
            fallback=(
                "你是财务报销智能体。任务是从发票文本中抽取结构化字段，"
                "判断是否计入报销汇总，并输出可复核的 JSON。"
            ),
        )
    ]
    for skill_name in ["invoice-reading", "reimbursement-workbook", "invoice-pdf-bundling"]:
        skill_path = config.skills_root / skill_name / "SKILL.md"
        content = read_text_file(skill_path)
        if content:
            parts.append(f"\n# Skill: {skill_name}\n{content}")
    return "\n\n".join(parts)


def load_json_file(path: Path, default):
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return default


def save_json_file(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class LlmClient:
    def __init__(self, config: AgentConfig):
        self.config = config
        self.api_key = os.getenv(config.llm_api_key_env, "")
        self.instructions = load_agent_instructions(config)
        self.calls = 0

    @property
    def enabled(self) -> bool:
        if not self.config.use_llm:
            return False
        if self.config.llm_provider == "codex":
            return shutil.which("codex") is not None
        if self.config.llm_provider == "http":
            return bool(httpx and self.api_key and self.config.llm_base_url)
        return False

    @property
    def display_model(self) -> str:
        if self.config.llm_model:
            return f"{self.config.llm_provider}:{self.config.llm_model}"
        return self.config.llm_provider

    def extract_invoice(self, file_name: str, text: str, rule_candidate: dict) -> dict | None:
        if not self.enabled:
            return None
        if self.calls >= self.config.llm_max_files:
            return {"_llm_error": f"达到 LLM 调用上限 {self.config.llm_max_files}"}
        self.calls += 1
        if self.config.llm_provider == "codex":
            return self.extract_with_codex(file_name, text, rule_candidate)
        if self.config.llm_provider == "http":
            return self.extract_with_http(file_name, text, rule_candidate)
        return None

    def extract_with_codex(self, file_name: str, text: str, rule_candidate: dict) -> dict | None:
        schema = json.dumps(INVOICE_JSON_SCHEMA["schema"], ensure_ascii=False)
        prompt = self.instructions + "\n\n" + self.build_user_prompt(file_name, text, rule_candidate)
        with tempfile.TemporaryDirectory(prefix="invoice_codex_") as tmp:
            output_path = Path(tmp) / "last_message.txt"
            schema_path = Path(tmp) / "schema.json"
            schema_path.write_text(schema, encoding="utf-8")
            cmd = [
                "codex",
                "exec",
                "--cd",
                str(AGENT_ROOT),
                "--sandbox",
                "read-only",
                "--ephemeral",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
            ]
            if self.config.llm_model:
                cmd.extend(["--model", self.config.llm_model])
            cmd.append(prompt)
            try:
                result = subprocess.run(
                    cmd,
                    text=True,
                    capture_output=True,
                    timeout=self.config.llm_timeout,
                    check=False,
                )
                if result.returncode != 0:
                    return {"_llm_error": (result.stderr or result.stdout or "codex exec failed")[:300]}
                raw = output_path.read_text(encoding="utf-8").strip()
                return json.loads(raw)
            except Exception as exc:
                return {"_llm_error": str(exc)[:300]}

    def extract_with_http(self, file_name: str, text: str, rule_candidate: dict) -> dict | None:
        if httpx is None:
            return {"_llm_error": "httpx 未安装，HTTP LLM 适配器不可用"}
        payload = {
            "model": self.config.llm_model,
            "instructions": self.instructions,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": self.build_user_prompt(file_name, text, rule_candidate),
                        }
                    ],
                }
            ],
            "text": {"format": {"type": "json_schema", **INVOICE_JSON_SCHEMA}},
        }
        url = self.config.llm_base_url.rstrip("/") + "/responses"
        try:
            with httpx.Client(timeout=self.config.llm_timeout) as client:
                response = client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
            return extract_response_json(response.json())
        except Exception as exc:
            chat_result = self.extract_with_chat_completions(file_name, text, rule_candidate)
            if chat_result is not None:
                return chat_result
            return {"_llm_error": str(exc)[:300]}

    def extract_with_chat_completions(self, file_name: str, text: str, rule_candidate: dict) -> dict | None:
        if not self.config.llm_base_url:
            return None
        payload = {
            "model": self.config.llm_model,
            "messages": [
                {"role": "system", "content": self.instructions},
                {"role": "user", "content": self.build_user_prompt(file_name, text, rule_candidate)},
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": INVOICE_JSON_SCHEMA,
            },
        }
        url = self.config.llm_base_url.rstrip("/") + "/chat/completions"
        try:
            with httpx.Client(timeout=self.config.llm_timeout) as client:
                response = client.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )
                response.raise_for_status()
            return extract_chat_completion_json(response.json())
        except Exception:
            return None

    def build_user_prompt(self, file_name: str, text: str, rule_candidate: dict) -> str:
        categories = ", ".join(DEFAULT_CATEGORY_RULES.keys())
        clipped_text = normalize_text(text)[: self.config.llm_text_limit]
        candidate = json.dumps(rule_candidate, ensure_ascii=False, default=str, indent=2)
        return textwrap.dedent(
            f"""
            请读取下面的发票/OCR文本，输出符合 JSON Schema 的单条报销记录。

            文件名：{file_name}

            可选报销种类优先使用：{categories}。如果不是正式发票而是行程单、订单、截图或附件，category 使用“行程单/附件”，include_in_total=false。
            如果是正式发票，金额优先采用“价税合计/小写/总计”。不要把纳税人识别号、发票号码、日期、下载次数、订单号当作金额。
            如果规则候选和文本冲突，以原文为准；不确定字段填空字符串或 null，并在 review_notes 写明。

            规则候选：
            {candidate}

            发票/OCR文本：
            {clipped_text}
            """
        ).strip()


def extract_response_json(payload: dict) -> dict | None:
    if "output_text" in payload and payload["output_text"]:
        try:
            return json.loads(payload["output_text"])
        except json.JSONDecodeError:
            pass
    for item in payload.get("output", []):
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                try:
                    return json.loads(content["text"])
                except json.JSONDecodeError:
                    continue
    return None


def extract_chat_completion_json(payload: dict) -> dict | None:
    choices = payload.get("choices") or []
    for choice in choices:
        message = choice.get("message") or {}
        content = message.get("content")
        if isinstance(content, str):
            try:
                return json.loads(content)
            except json.JSONDecodeError:
                match = re.search(r"\{.*\}", content, re.S)
                if match:
                    try:
                        return json.loads(match.group(0))
                    except json.JSONDecodeError:
                        continue
        if isinstance(content, list):
            text = "\n".join(part.get("text", "") for part in content if isinstance(part, dict))
            if text:
                try:
                    return json.loads(text)
                except json.JSONDecodeError:
                    continue
    return None


def apply_llm_result(record: InvoiceRecord, llm_result: dict | None, notes: list[str]) -> None:
    if not llm_result:
        return
    if "_llm_error" in llm_result:
        notes.append(f"LLM未使用: {llm_result['_llm_error']}")
        return

    for attr in ["invoice_code", "invoice_number", "invoice_date", "seller", "buyer", "category"]:
        value = llm_result.get(attr)
        if isinstance(value, str) and value.strip():
            cleaned = value.strip()
            if attr == "invoice_date":
                cleaned = standardize_date(cleaned)
            elif attr in {"seller", "buyer"}:
                cleaned = clean_party_name(cleaned)
            setattr(record, attr, cleaned)

    for attr in ["amount", "tax_amount", "total_amount"]:
        value = coerce_decimal(llm_result.get(attr))
        if value is not None:
            setattr(record, attr, value)

    record.include_in_total = coerce_bool(llm_result.get("include_in_total"), record.include_in_total)
    record.llm_confidence = clamp_confidence(llm_result.get("confidence"))
    record.llm_model = llm_result.get("_model", "")
    review_notes = llm_result.get("review_notes") or []
    if isinstance(review_notes, list):
        notes.extend(str(note).strip() for note in review_notes if str(note).strip())
    if coerce_bool(llm_result.get("is_reimbursement_attachment"), False):
        record.include_in_total = False
        if not record.category or record.category == "待分类":
            record.category = "行程单/附件"


def parsed_candidate_json(parsed: dict, category: str, include_in_total: bool) -> dict:
    return {
        "is_invoice": bool(parsed.get("is_invoice")),
        "invoice_code": parsed.get("invoice_code") or "",
        "invoice_number": parsed.get("invoice_number") or "",
        "invoice_date": parsed.get("invoice_date") or "",
        "seller": parsed.get("seller") or "",
        "buyer": parsed.get("buyer") or "",
        "amount": decimal_to_json_number(parsed.get("amount")),
        "tax_amount": decimal_to_json_number(parsed.get("tax_amount")),
        "total_amount": decimal_to_json_number(parsed.get("total_amount")),
        "category": category,
        "include_in_total": include_in_total,
    }


def should_use_llm_for_record(record: InvoiceRecord, parsed: dict, config: AgentConfig) -> bool:
    if not config.use_llm:
        return False
    if not config.llm_only_review_items:
        return True
    if not parsed.get("is_invoice"):
        return True
    if record.amount_for_total is None or not record.invoice_number:
        return True
    if record.category in {"待分类", ""}:
        return True
    if record.extraction_source == "OCR":
        return True
    return False


def infer_invoice_number_from_lines(lines: list[str]) -> str:
    ignored = {"91110108MAC64MM492"}
    for line in lines:
        stripped = re.sub(r"\D", "", line)
        if 16 <= len(stripped) <= 24 and stripped not in ignored:
            if re.fullmatch(r"[0-9]{16,24}", stripped):
                return stripped
    return ""


def infer_parties_from_lines(lines: list[str], invoice_number: str, invoice_date: str) -> tuple[str, str]:
    inline_names = []
    for line in lines:
        match = re.search(r"名称[:：]\s*([^\n]{2,80})", line)
        if match:
            inline_names.append(clean_party_name(match.group(1)))
    inline_names = [name for name in inline_names if is_likely_party_name(name)]
    if len(inline_names) >= 2:
        return inline_names[0], inline_names[1]

    date_index = -1
    for idx, line in enumerate(lines):
        if invoice_date and standardize_date(line) == invoice_date:
            date_index = idx
            break
    if date_index == -1 and invoice_number:
        for idx, line in enumerate(lines):
            if invoice_number in re.sub(r"\s+", "", line):
                date_index = idx
                break
    if date_index == -1:
        return "", ""

    candidates = []
    for line in lines[date_index + 1 : date_index + 8]:
        cleaned = clean_party_name(line)
        if is_likely_party_name(cleaned):
            candidates.append(cleaned)
    if len(candidates) >= 2:
        return candidates[0], candidates[1]
    if len(candidates) == 1:
        return candidates[0], ""
    return "", ""


def is_likely_party_name(value: str) -> bool:
    if not value:
        return False
    if re.fullmatch(r"[0-9A-Z]{12,24}", value):
        return False
    bad_terms = ["发票", "名称", "信息", "项目名称", "规格型号", "下载次数", "开票日期"]
    if any(term in value for term in bad_terms):
        return False
    return bool(re.search(r"公司|酒店|饭店|餐厅|银行|中心|LLC|Ltd|Inc", value, re.I))


def clean_party_name(value: str) -> str:
    value = re.sub(r"\s+", " ", normalize_text(value))
    value = re.split(r"(?:纳税人识别号|税号|地址|电话|开户行|账号|购买方|销售方)", value)[0]
    return value.strip(" :：|")[:80]


def standardize_date(value: str) -> str:
    value = normalize_text(value)
    if not value:
        return ""
    match = re.search(r"([0-9]{4})[年./-]([0-9]{1,2})[月./-]([0-9]{1,2})", value)
    if not match:
        return value[:20]
    year, month, day = map(int, match.groups())
    try:
        return dt.date(year, month, day).isoformat()
    except ValueError:
        return value[:20]


def extract_labeled_amount(lines: list[str], labels: list[str]) -> Decimal | None:
    amount_pattern = r"([¥$]?[ \t]*[0-9][0-9,]*(?:\.[0-9]{1,2})?)"
    for line in lines:
        line_cmp = line.lower()
        if not any(label.lower() in line_cmp for label in labels):
            continue
        values = [parse_decimal(value) for value in re.findall(amount_pattern, line)]
        values = [value for value in values if is_plausible_invoice_amount(value)]
        if values:
            return values[-1]
    return None


def extract_amount(lines: list[str], preferred: bool) -> Decimal | None:
    # 带 ¥ 符号的金额是发票上的正式金额（明细金额/税额/价税合计），价税合计通常是其中的最大值。
    # 优先采用，可避免把开票日期年份（如"2026年09月27日"里的 2026）等裸数字误判为金额。
    currency_values = []
    for line in lines:
        for value in re.findall(r"[¥$][ \t]*[0-9][0-9,]*(?:\.[0-9]{1,2})?", line):
            parsed = parse_decimal(value)
            if is_plausible_invoice_amount(parsed):
                currency_values.append(parsed)
    if currency_values:
        return max(currency_values)

    priority_labels = ["价税合计", "小写", "总计", "合计金额", "Total", "Amount Due", "Grand Total"]
    secondary_labels = ["合计", "金额", "Amount", "Total"]
    labels = priority_labels if preferred else secondary_labels
    labeled = extract_labeled_amount(lines, labels)
    if labeled is not None:
        return labeled

    context_value = extract_amount_near_labels(lines, labels)
    if context_value is not None:
        return context_value

    decimal_values = []
    for line in lines:
        for value in re.findall(r"\b[0-9]{1,7}(?:,[0-9]{3})*(?:\.[0-9]{2})\b|\b[0-9]{1,7}\.[0-9]{2}\b", line):
            parsed = parse_decimal(value)
            if is_plausible_invoice_amount(parsed):
                decimal_values.append(parsed)
    if decimal_values:
        return max(decimal_values)
    return None


def extract_amount_near_labels(lines: list[str], labels: list[str]) -> Decimal | None:
    label_indexes = [
        idx for idx, line in enumerate(lines) if any(label.lower() in line.lower() for label in labels)
    ]
    for idx in label_indexes:
        window = lines[idx : min(len(lines), idx + 8)]
        candidates: list[Decimal] = []
        for line in window:
            for match in re.finditer(r"[¥$]?[ \t]*[0-9][0-9,]*(?:\.[0-9]{1,2})?", line):
                # 排除日期年份（数字后紧跟"年"，如 2026年09月27日），避免把年份当金额
                if re.match(r"\s*年", line[match.end():]):
                    continue
                parsed = parse_decimal(match.group())
                if is_plausible_invoice_amount(parsed):
                    candidates.append(parsed)
        if candidates:
            return max(candidates)
    return None


def is_plausible_invoice_amount(value: Decimal | None) -> bool:
    if value is None:
        return False
    return Decimal("0.00") <= value <= Decimal("1000000.00")


def infer_category(text: str, filename: str, rules: dict[str, list[str]], default: str) -> str:
    haystack = normalize_text(f"{filename}\n{text}").lower()
    for category, keywords in rules.items():
        for keyword in keywords:
            if keyword and keyword.lower() in haystack:
                return category
    return default


def read_pdf_text(path: Path) -> tuple[str, int]:
    doc = fitz.open(path)
    try:
        page_text = [page.get_text("text") for page in doc]
        return "\n".join(page_text), doc.page_count
    finally:
        doc.close()


def render_pdf_pages_to_images(path: Path, workdir: Path, dpi_scale: float = 2.0) -> list[Path]:
    doc = fitz.open(path)
    output_paths: list[Path] = []
    try:
        matrix = fitz.Matrix(dpi_scale, dpi_scale)
        for i, page in enumerate(doc):
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            out = workdir / f"{path.stem}_page_{i + 1}.png"
            pix.save(out)
            output_paths.append(out)
    finally:
        doc.close()
    return output_paths


def preprocess_image_for_ocr(input_path: Path, output_path: Path) -> Path:
    with Image.open(input_path) as img:
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")
        width, height = img.size
        min_width = 1800
        if width < min_width:
            scale = min_width / max(width, 1)
            img = img.resize((int(width * scale), int(height * scale)), Image.Resampling.LANCZOS)
        gray = ImageOps.grayscale(img)
        gray = ImageOps.autocontrast(gray)
        gray = ImageEnhance.Sharpness(gray).enhance(1.5)
        gray.save(output_path)
    return output_path


def run_tesseract(image_path: Path, lang: str, timeout: int = 45) -> str:
    if shutil.which("tesseract") is None:
        return ""
    cmd = ["tesseract", str(image_path), "stdout", "-l", lang, "--psm", "6"]
    result = subprocess.run(cmd, text=True, capture_output=True, timeout=timeout, check=False)
    if result.returncode != 0:
        return ""
    return result.stdout


def ocr_images(paths: Iterable[Path], lang: str, workdir: Path) -> str:
    texts: list[str] = []
    for i, path in enumerate(paths, start=1):
        prepared = workdir / f"ocr_{i}_{path.stem}.png"
        try:
            preprocess_image_for_ocr(path, prepared)
            text = run_tesseract(prepared, lang)
        except Exception:
            text = ""
        if text:
            texts.append(text)
    return "\n".join(texts)


def has_enough_extraction(parsed: dict, text: str) -> bool:
    if len(normalize_text(text)) < 30:
        return False
    return bool(parsed.get("invoice_number") or parsed.get("total_amount") or parsed.get("amount"))


def extract_invoice_record(
    path: Path,
    index: int,
    serial_prefix: str,
    category_rules: dict[str, list[str]],
    default_category: str,
    ocr_lang: str,
    workdir: Path,
    llm_client: LlmClient | None = None,
) -> InvoiceRecord:
    record = InvoiceRecord(
        index=index,
        custom_serial=f"{serial_prefix}-{index:03d}",
        source_path=path,
        file_type=path.suffix.lower().lstrip(".").upper(),
    )

    direct_text = ""
    ocr_text = ""
    notes: list[str] = []

    if path.suffix.lower() in SUPPORTED_PDF:
        try:
            direct_text, pages = read_pdf_text(path)
            record.pages = pages
        except Exception as exc:
            notes.append(f"PDF读取失败: {exc}")
        parsed = parse_invoice_text(direct_text)
        if not has_enough_extraction(parsed, direct_text):
            try:
                image_paths = render_pdf_pages_to_images(path, workdir)
                ocr_text = ocr_images(image_paths, ocr_lang, workdir)
            except Exception as exc:
                notes.append(f"PDF OCR失败: {exc}")
        record.extraction_source = "PDF文字层" if direct_text.strip() else "OCR"
    else:
        record.pages = 1
        ocr_text = ocr_images([path], ocr_lang, workdir)
        record.extraction_source = "OCR"

    text = "\n".join(part for part in [direct_text, ocr_text] if part.strip())
    parsed = parse_invoice_text(text)
    for field_name, value in parsed.items():
        if hasattr(record, field_name):
            setattr(record, field_name, value)
    record.category = infer_category(text, path.name, category_rules, default_category)
    record.extracted_text = compact_text(text)
    is_invoice = bool(parsed.get("is_invoice"))

    if (
        llm_client
        and llm_client.enabled
        and text.strip()
        and should_use_llm_for_record(record, parsed, llm_client.config)
    ):
        candidate = parsed_candidate_json(parsed, record.category, record.include_in_total)
        llm_result = llm_client.extract_invoice(path.name, text, candidate)
        if llm_result and "_llm_error" not in llm_result:
            llm_result["_model"] = llm_client.display_model
            record.extraction_source = f"{record.extraction_source}+LLM"
        apply_llm_result(record, llm_result, notes)

    if not is_invoice and re.search(r"行程单|TRIP TABLE|行程报销单", text, re.I):
        record.category = "行程单/附件"
        record.include_in_total = False
        record.total_amount = None
        record.amount = parsed.get("amount")
        record.invoice_number = ""

    if not text.strip():
        record.review_status = "未识别"
        notes.append("未提取到有效文字，请人工补录")
    elif not record.include_in_total:
        record.review_status = "附件"
        notes.append("非发票附件，已合并PDF但不计入报销总额")
    elif record.amount_for_total is None:
        record.review_status = "需补金额"
        notes.append("未识别到发票金额")
    elif not record.invoice_number:
        record.review_status = "需复核"
        notes.append("未识别到发票编号")
    else:
        record.review_status = "已识别"
    if record.extraction_source == "OCR":
        notes.append("OCR结果可能受图片清晰度影响")

    record.notes = "；".join(dict.fromkeys(note for note in notes if note))
    return record


def mark_duplicate_invoices(records: list[InvoiceRecord]) -> None:
    seen: dict[tuple[str, str], InvoiceRecord] = {}
    for record in records:
        if not record.include_in_total or not record.invoice_number or record.amount_for_total is None:
            continue
        key = (record.invoice_number, str(record.amount_for_total))
        first = seen.get(key)
        if first is None:
            seen[key] = record
            continue
        record.include_in_total = False
        record.review_status = "重复"
        duplicate_note = f"疑似重复发票，已不计入汇总；首次出现为 {first.custom_serial}"
        record.notes = "；".join(note for note in [record.notes, duplicate_note] if note)


def invoice_file_id(path: Path) -> str:
    stat = path.stat()
    return f"sha256:{file_sha256(path)}|size:{stat.st_size}"


def load_invoice_ledger(input_dir: Path) -> dict:
    ledger = load_json_file(input_dir / LEDGER_FILENAME, {})
    if not isinstance(ledger, dict):
        return {}
    ledger.setdefault("version", 1)
    ledger.setdefault("reimbursed_files", {})
    ledger.setdefault("batches", [])
    return ledger


def save_invoice_ledger(input_dir: Path, ledger: dict) -> None:
    save_json_file(input_dir / LEDGER_FILENAME, ledger)


def is_reimbursed_file(path: Path, ledger: dict) -> bool:
    reimbursed = ledger.get("reimbursed_files", {})
    try:
        file_id = invoice_file_id(path)
    except OSError:
        return False
    if file_id in reimbursed:
        return True
    return str(path.resolve()) in reimbursed


def collect_invoice_files(input_dir: Path, ledger: dict | None = None, include_reimbursed: bool = False) -> list[Path]:
    generated_prefixes = ("报销表_", "发票附件_", "识别结果_")
    files = [
        path
        for path in input_dir.rglob("*")
        if path.is_file()
        and path.suffix.lower() in SUPPORTED_EXTENSIONS
        and not path.name.startswith("._")
        and not path.name.startswith(generated_prefixes)
        and path.name != LEDGER_FILENAME
    ]
    if ledger is not None and not include_reimbursed:
        files = [path for path in files if not is_reimbursed_file(path, ledger)]
    return sorted(files, key=lambda p: str(p.relative_to(input_dir)).lower())


def parse_category_rules(values: list[str] | None) -> dict[str, list[str]]:
    rules = {category: keywords[:] for category, keywords in DEFAULT_CATEGORY_RULES.items()}
    for item in values or []:
        if "=" not in item:
            raise ValueError(f"分类规则格式应为 类别=关键词1,关键词2，收到: {item}")
        category, raw_keywords = item.split("=", 1)
        category = category.strip()
        keywords = [part.strip() for part in re.split(r"[,，]", raw_keywords) if part.strip()]
        if category and keywords:
            rules.setdefault(category, []).extend(keywords)
    return rules


def included_total(records: list[InvoiceRecord]) -> Decimal:
    return sum(
        (record.amount_for_total or Decimal("0.00") for record in records if record.include_in_total),
        Decimal("0.00"),
    )


def mark_batch_reimbursed(
    input_dir: Path,
    records: list[InvoiceRecord],
    workbook_path: Path,
    pdf_path: Path,
    json_path: Path | None,
    timestamp: str,
) -> None:
    ledger = load_invoice_ledger(input_dir)
    batch_id = f"RB-{timestamp}"
    reimbursed_at = dt.datetime.now().isoformat(timespec="seconds")
    file_entries = []
    for record in records:
        try:
            file_id = invoice_file_id(record.source_path)
        except OSError:
            continue
        entry = {
            "batch_id": batch_id,
            "reimbursed_at": reimbursed_at,
            "custom_serial": record.custom_serial,
            "source_path": str(record.source_path),
            "file_name": record.source_path.name,
            "invoice_number": record.invoice_number,
            "amount": str(record.amount_for_total) if record.amount_for_total is not None else None,
            "include_in_total": record.include_in_total,
            "review_status": record.review_status,
        }
        ledger["reimbursed_files"][file_id] = entry
        ledger["reimbursed_files"][str(record.source_path.resolve())] = entry
        file_entries.append(entry)
    batch = {
        "batch_id": batch_id,
        "reimbursed_at": reimbursed_at,
        "invoice_dir": str(input_dir),
        "workbook_path": str(workbook_path),
        "pdf_path": str(pdf_path),
        "json_path": str(json_path) if json_path else "",
        "file_count": len(records),
        "included_count": sum(1 for record in records if record.include_in_total),
        "total_amount": str(included_total(records)),
        "files": file_entries,
    }
    ledger["batches"].append(batch)
    save_invoice_ledger(input_dir, ledger)
    append_global_reimbursement_batch(batch)


def append_global_reimbursement_batch(batch: dict) -> None:
    data = load_json_file(GLOBAL_REIMBURSEMENT_LEDGER, {"version": 1, "batches": []})
    batches = [item for item in data.get("batches", []) if item.get("batch_id") != batch.get("batch_id")]
    batches.append({key: value for key, value in batch.items() if key != "files"})
    batches.sort(key=lambda item: item.get("reimbursed_at", ""))
    data["batches"] = batches
    save_json_file(GLOBAL_REIMBURSEMENT_LEDGER, data)


def create_combined_pdf(records: list[InvoiceRecord], output_path: Path) -> None:
    output = fitz.open()
    try:
        for record in records:
            path = record.source_path
            if path.suffix.lower() in SUPPORTED_PDF:
                try:
                    src = fitz.open(path)
                    try:
                        output.insert_pdf(src)
                    finally:
                        src.close()
                except Exception:
                    add_error_page(output, record, "PDF无法合并")
            else:
                try:
                    add_image_page(output, path)
                except Exception:
                    add_error_page(output, record, "图片无法合并")
        output.save(output_path)
    finally:
        output.close()


def add_image_page(output: fitz.Document, image_path: Path) -> None:
    with Image.open(image_path) as img:
        img = ImageOps.exif_transpose(img)
        width, height = img.size
        if width <= 0 or height <= 0:
            raise ValueError("invalid image size")
        a4_w, a4_h = fitz.paper_size("a4")
        if width > height:
            page_w, page_h = a4_h, a4_w
        else:
            page_w, page_h = a4_w, a4_h
        margin = 28
        scale = min((page_w - 2 * margin) / width, (page_h - 2 * margin) / height)
        draw_w = width * scale
        draw_h = height * scale
        rect = fitz.Rect(
            (page_w - draw_w) / 2,
            (page_h - draw_h) / 2,
            (page_w + draw_w) / 2,
            (page_h + draw_h) / 2,
        )

    page = output.new_page(width=page_w, height=page_h)
    page.insert_image(rect, filename=str(image_path), keep_proportion=True)


def add_error_page(output: fitz.Document, record: InvoiceRecord, reason: str) -> None:
    page = output.new_page(width=fitz.paper_size("a4")[0], height=fitz.paper_size("a4")[1])
    text = f"{record.custom_serial}\n{record.source_path.name}\n{reason}"
    page.insert_textbox(fitz.Rect(72, 72, 520, 220), text, fontsize=12)


def autosize_columns(ws, max_width: int = 55) -> None:
    for column_cells in ws.columns:
        length = 0
        col_letter = get_column_letter(column_cells[0].column)
        for cell in column_cells:
            value = cell.value
            if value is None:
                continue
            length = max(length, min(len(str(value)) + 2, max_width))
        ws.column_dimensions[col_letter].width = max(10, length)


def build_workbook(
    records: list[InvoiceRecord],
    output_path: Path,
    applicant: str,
    department: str,
    reason: str,
    run_date: dt.date,
) -> None:
    wb = Workbook()
    ws_form = wb.active
    ws_form.title = "报销单"
    ws_list = wb.create_sheet("发票清单")

    populate_invoice_list(ws_list, records)
    populate_reimbursement_form(ws_form, records, applicant, department, reason, run_date)

    for ws in [ws_form, ws_list]:
        ws.sheet_view.showGridLines = False
        autosize_columns(ws)
    wb.save(output_path)


def populate_invoice_list(ws, records: list[InvoiceRecord]) -> None:
    headers = [
        "序号",
        "自定义发票序号",
        "原始文件名",
        "文件类型",
        "页数",
        "自定义报销种类",
        "发票代码",
        "发票编号",
        "开票日期",
        "销售方",
        "购买方",
        "发票金额",
        "税额",
        "价税合计",
        "是否计入汇总",
        "识别来源",
        "LLM模型",
        "LLM置信度",
        "复核状态",
        "备注",
        "原始路径",
        "识别文本摘要",
    ]
    ws.append(headers)
    for record in records:
        ws.append(
            [
                record.index,
                record.custom_serial,
                record.source_path.name,
                record.file_type,
                record.pages,
                record.category,
                record.invoice_code,
                record.invoice_number,
                record.invoice_date,
                record.seller,
                record.buyer,
                money_to_float(record.amount_for_total),
                money_to_float(record.tax_amount),
                money_to_float(record.total_amount),
                "是" if record.include_in_total else "否",
                record.extraction_source,
                record.llm_model,
                record.llm_confidence,
                record.review_status,
                record.notes,
                str(record.source_path),
                record.extracted_text,
            ]
        )

    last_data_row = max(2, len(records) + 1)
    total_row = len(records) + 2
    ws.cell(total_row, 11, "合计")
    ws.cell(total_row, 12, f'=SUMIF(O2:O{last_data_row},"是",L2:L{last_data_row})')
    ws.cell(total_row, 14, f'=SUMIF(O2:O{last_data_row},"是",N2:N{last_data_row})')

    header_fill = PatternFill("solid", fgColor="1F4E78")
    header_font = Font(color="FFFFFF", bold=True)
    total_fill = PatternFill("solid", fgColor="D9EAF7")
    thin = Side(style="thin", color="D9E2F3")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    for row in ws.iter_rows(min_row=1, max_row=total_row, min_col=1, max_col=len(headers)):
        for cell in row:
            cell.border = border
            cell.alignment = Alignment(vertical="center", wrap_text=True)
    for cell in ws[1]:
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    for cell in ws[total_row]:
        cell.fill = total_fill
        cell.font = Font(bold=True)

    for row_idx in range(2, last_data_row + 1):
        ws.cell(row_idx, 12).number_format = '¥#,##0.00;[Red]-¥#,##0.00;"-"'
        ws.cell(row_idx, 13).number_format = '¥#,##0.00;[Red]-¥#,##0.00;"-"'
        ws.cell(row_idx, 14).number_format = '¥#,##0.00;[Red]-¥#,##0.00;"-"'
        ws.cell(row_idx, 19).number_format = '0%'
        status = ws.cell(row_idx, 20).value
        if status == "已识别":
            ws.cell(row_idx, 20).fill = PatternFill("solid", fgColor="E2F0D9")
        elif status == "未识别":
            ws.cell(row_idx, 20).fill = PatternFill("solid", fgColor="F4CCCC")
        elif status in {"附件", "重复"}:
            ws.cell(row_idx, 20).fill = PatternFill("solid", fgColor="D9EAD3")
        else:
            ws.cell(row_idx, 20).fill = PatternFill("solid", fgColor="FFF2CC")
    for col in [12, 13, 14]:
        ws.cell(total_row, col).number_format = '¥#,##0.00;[Red]-¥#,##0.00;"-"'

    ws.freeze_panes = "A2"
    ws.auto_filter.ref = f"A1:V{total_row}"
    ws.row_dimensions[1].height = 28
    ws.column_dimensions["U"].width = 45
    ws.column_dimensions["V"].width = 70

    if records:
        ws.cell(1, 12).comment = Comment("发票金额优先取价税合计/总计；识别不到时留空，需人工补录。", "invoice_agent")


def populate_reimbursement_form(
    ws,
    records: list[InvoiceRecord],
    applicant: str,
    department: str,
    reason: str,
    run_date: dt.date,
) -> None:
    ws.merge_cells("A1:H1")
    ws["A1"] = "报销单"
    ws["A1"].font = Font(size=18, bold=True, color="FFFFFF")
    ws["A1"].fill = PatternFill("solid", fgColor="1F4E78")
    ws["A1"].alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 34

    total = sum(
        (record.amount_for_total or Decimal("0.00") for record in records if record.include_in_total),
        Decimal("0.00"),
    )
    review_count = sum(1 for record in records if record.include_in_total and record.review_status != "已识别")
    total_row_in_list = len(records) + 2

    fields = [
        ("A3", "报销日期", "B3", run_date.isoformat()),
        ("D3", "单据数量", "E3", len(records)),
        ("A4", "报销人", "B4", applicant),
        ("D4", "部门", "E4", department),
        ("A5", "报销事由", "B5", reason),
        ("A7", "报销总额", "B7", f"='发票清单'!L{total_row_in_list}"),
        ("D7", "金额大写", "E7", rmb_upper(total)),
        ("A8", "复核提示", "B8", f"{review_count} 张需复核" if review_count else "全部已识别"),
    ]
    for label_cell, label, value_cell, value in fields:
        ws[label_cell] = label
        ws[label_cell].fill = PatternFill("solid", fgColor="D9EAF7")
        ws[label_cell].font = Font(bold=True)
        ws[value_cell] = value
    ws["B5"].alignment = Alignment(wrap_text=True, vertical="center")
    ws.merge_cells("B5:H5")
    ws.merge_cells("E7:H7")
    ws.merge_cells("B8:H8")

    ws["B7"].number_format = '¥#,##0.00;[Red]-¥#,##0.00;"-"'
    ws["B7"].font = Font(bold=True, color="C00000")

    ws["A10"] = "分类汇总"
    ws["A10"].font = Font(bold=True, color="FFFFFF")
    ws["A10"].fill = PatternFill("solid", fgColor="1F4E78")
    ws.merge_cells("A10:C10")
    summary_headers = ["报销种类", "发票张数", "金额"]
    for col, header in enumerate(summary_headers, start=1):
        cell = ws.cell(11, col, header)
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor="5B9BD5")
        cell.alignment = Alignment(horizontal="center")

    categories = sorted({record.category for record in records if record.include_in_total}) or ["待分类"]
    for idx, category in enumerate(categories, start=12):
        ws.cell(idx, 1, category)
        ws.cell(idx, 2, f'=COUNTIFS(发票清单!F:F,A{idx},发票清单!O:O,"是")')
        ws.cell(idx, 3, f'=SUMIFS(发票清单!L:L,发票清单!F:F,A{idx},发票清单!O:O,"是")')
        ws.cell(idx, 3).number_format = '¥#,##0.00;[Red]-¥#,##0.00;"-"'
    total_row = 12 + len(categories)
    ws.cell(total_row, 1, "合计")
    ws.cell(total_row, 2, f"=SUM(B12:B{total_row - 1})")
    ws.cell(total_row, 3, f"=SUM(C12:C{total_row - 1})")
    ws.cell(total_row, 3).number_format = '¥#,##0.00;[Red]-¥#,##0.00;"-"'

    sign_row = total_row + 3
    ws.cell(sign_row, 1, "经办人")
    ws.cell(sign_row, 3, "部门负责人")
    ws.cell(sign_row, 5, "财务审核")
    ws.cell(sign_row, 7, "审批人")

    thin = Side(style="thin", color="D9E2F3")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    for row in ws.iter_rows(min_row=3, max_row=sign_row + 2, min_col=1, max_col=8):
        for cell in row:
            cell.border = border
            cell.alignment = Alignment(vertical="center", wrap_text=True)
    for row_idx in range(12, total_row + 1):
        for col_idx in range(1, 4):
            ws.cell(row_idx, col_idx).border = border
        if row_idx == total_row:
            for col_idx in range(1, 4):
                ws.cell(row_idx, col_idx).fill = PatternFill("solid", fgColor="D9EAF7")
                ws.cell(row_idx, col_idx).font = Font(bold=True)

    ws.column_dimensions["A"].width = 14
    ws.column_dimensions["B"].width = 18
    ws.column_dimensions["C"].width = 14
    ws.column_dimensions["D"].width = 14
    ws.column_dimensions["E"].width = 18
    ws.column_dimensions["F"].width = 14
    ws.column_dimensions["G"].width = 14
    ws.column_dimensions["H"].width = 18


def rmb_upper(value: Decimal) -> str:
    value = (value or Decimal("0.00")).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if value == 0:
        return "零元整"
    digits = "零壹贰叁肆伍陆柒捌玖"
    units = ["", "拾", "佰", "仟"]
    big_units = ["", "万", "亿", "兆"]
    integer = int(value)
    cents = int((value - Decimal(integer)) * 100)

    def section_to_upper(section: int) -> str:
        result = ""
        zero = False
        for i in range(4):
            digit = section % 10
            if digit == 0:
                if result:
                    zero = True
            else:
                if zero:
                    result = digits[0] + result
                    zero = False
                result = digits[digit] + units[i] + result
            section //= 10
        return result

    integer_parts = []
    unit_index = 0
    need_zero = False
    while integer > 0:
        section = integer % 10000
        if section == 0:
            if integer_parts:
                need_zero = True
        else:
            section_text = section_to_upper(section) + big_units[unit_index]
            if need_zero:
                integer_parts.append(digits[0])
                need_zero = False
            integer_parts.append(section_text)
        integer //= 10000
        unit_index += 1
    integer_text = "".join(reversed(integer_parts)).rstrip("零") + "元"

    jiao = cents // 10
    fen = cents % 10
    if cents == 0:
        return integer_text + "整"
    decimal_text = ""
    if jiao:
        decimal_text += digits[jiao] + "角"
    elif fen:
        decimal_text += "零"
    if fen:
        decimal_text += digits[fen] + "分"
    return integer_text + decimal_text


def load_config(path: Path | None) -> dict:
    if not path:
        return {}
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def build_agent_config(args, config: dict) -> AgentConfig:
    configured_use_llm = config.get("use_llm")
    if args.no_llm:
        use_llm = False
    elif args.use_llm:
        use_llm = True
    elif configured_use_llm is not None:
        use_llm = coerce_bool(configured_use_llm)
    else:
        use_llm = shutil.which("codex") is not None

    provider = args.llm_provider or config.get("llm_provider", DEFAULT_LLM_PROVIDER)

    return AgentConfig(
        use_llm=use_llm,
        llm_provider=provider,
        llm_model=args.llm_model if args.llm_model is not None else config.get("llm_model", DEFAULT_LLM_MODEL),
        llm_base_url=args.llm_base_url or config.get("llm_base_url", DEFAULT_LLM_BASE_URL),
        llm_api_key_env=config.get("llm_api_key_env", "LLM_API_KEY"),
        llm_timeout=args.llm_timeout or int(config.get("llm_timeout", 45)),
        llm_text_limit=int(config.get("llm_text_limit", LLM_TEXT_LIMIT)),
        llm_max_files=int(config.get("llm_max_files", 20)),
        llm_only_review_items=False if getattr(args, "llm_all", False) else coerce_bool(config.get("llm_only_review_items", True), True),
        agent_prompt_path=Path(config.get("agent_prompt_path", AGENT_PROMPT_PATH)).expanduser(),
        skills_root=Path(config.get("skills_root", SKILLS_ROOT)).expanduser(),
    )


def choose_ocr_language(requested: str | None) -> str:
    if requested:
        return requested
    if shutil.which("tesseract") is None:
        return "eng"
    result = subprocess.run(["tesseract", "--list-langs"], text=True, capture_output=True, check=False)
    langs = set(result.stdout.split())
    if "chi_sim" in langs and "eng" in langs:
        return "chi_sim+eng"
    if "eng" in langs and "snum" in langs:
        return "eng+snum"
    if "eng" in langs:
        return "eng"
    return next(iter(langs), "eng")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="整理发票目录，生成报销表 Excel 和合并发票 PDF。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("invoice_dir", type=Path, help="发票文件夹，支持 PDF/JPG/PNG/TIFF/WEBP")
    parser.add_argument("-o", "--output-dir", type=Path, default=None, help="输出目录")
    parser.add_argument("--applicant", default="", help="报销人")
    parser.add_argument("--department", default="", help="部门")
    parser.add_argument("--reason", default="发票报销", help="报销事由")
    parser.add_argument("--serial-prefix", default="FP", help="自定义发票序号前缀")
    parser.add_argument("--default-category", default="待分类", help="无法匹配时使用的报销种类")
    parser.add_argument(
        "--category-rule",
        action="append",
        default=[],
        help="追加分类规则，格式: 类别=关键词1,关键词2。可重复传入。",
    )
    parser.add_argument("--ocr-lang", default=None, help="Tesseract OCR 语言，例如 chi_sim+eng")
    parser.add_argument("--config", type=Path, default=None, help="可选 JSON 配置文件")
    parser.add_argument("--save-json", action="store_true", help="同时保存结构化识别结果 JSON")
    parser.add_argument("--include-reimbursed", action="store_true", help="包含已登记报销的原始凭证")
    parser.add_argument("--mark-reimbursed", action="store_true", help="生成成功后把本批原始凭证登记为已报销")
    llm_group = parser.add_mutually_exclusive_group()
    llm_group.add_argument("--use-llm", action="store_true", help="启用 LLM 结构化抽取，默认使用本机 Codex 配置")
    llm_group.add_argument("--no-llm", action="store_true", help="禁用 LLM，仅使用本地规则/OCR")
    parser.add_argument("--llm-provider", choices=["codex", "http"], default=None, help="模型服务商适配器")
    parser.add_argument("--llm-model", default=None, help="LLM 模型；codex provider 留空时使用 Codex 当前配置")
    parser.add_argument("--llm-base-url", default=None, help="HTTP 兼容接口地址，仅 llm-provider=http 时使用")
    parser.add_argument("--llm-timeout", type=int, default=None, help="LLM 请求超时时间，秒")
    parser.add_argument("--llm-all", action="store_true", help="对所有文件都调用 LLM；默认只处理需复核候选")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    invoice_dir = args.invoice_dir.expanduser().resolve()
    if not invoice_dir.exists() or not invoice_dir.is_dir():
        print(f"发票目录不存在或不是文件夹: {invoice_dir}", file=sys.stderr)
        return 2

    config = load_config(args.config.expanduser().resolve() if args.config else None)
    output_dir = (args.output_dir or config.get("output_dir") or invoice_dir / "报销输出").expanduser().resolve()
    applicant = args.applicant or config.get("applicant", "")
    department = args.department or config.get("department", "")
    reason = args.reason or config.get("reason", "发票报销")
    serial_prefix = args.serial_prefix or config.get("serial_prefix", "FP")
    default_category = args.default_category or config.get("default_category", "待分类")
    rule_args = list(args.category_rule or []) + list(config.get("category_rules", []))
    try:
        category_rules = parse_category_rules(rule_args)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    ocr_lang = choose_ocr_language(args.ocr_lang or config.get("ocr_lang"))
    agent_config = build_agent_config(args, config)
    llm_client = LlmClient(agent_config)
    if agent_config.use_llm and not llm_client.enabled:
        print(
            f"提示: 已请求启用 LLM，但 provider={agent_config.llm_provider} 不可用，将使用本地规则兜底。",
            file=sys.stderr,
        )

    ledger = load_invoice_ledger(invoice_dir)
    files = collect_invoice_files(invoice_dir, ledger=ledger, include_reimbursed=args.include_reimbursed)
    if not files:
        print(f"未在目录中找到新的未报销发票文件: {invoice_dir}", file=sys.stderr)
        return 1

    timestamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir.mkdir(parents=True, exist_ok=True)
    workbook_path = output_dir / f"报销表_{timestamp}.xlsx"
    pdf_path = output_dir / f"发票附件_{timestamp}.pdf"
    json_path = output_dir / f"识别结果_{timestamp}.json"

    records: list[InvoiceRecord] = []
    with tempfile.TemporaryDirectory(prefix="invoice_agent_") as tmp:
        workdir = Path(tmp)
        for index, path in enumerate(files, start=1):
            print(f"[{index}/{len(files)}] 识别 {path.name}")
            record = extract_invoice_record(
                path=path,
                index=index,
                serial_prefix=serial_prefix,
                category_rules=category_rules,
                default_category=default_category,
                ocr_lang=ocr_lang,
                workdir=workdir,
                llm_client=llm_client,
            )
            records.append(record)

    mark_duplicate_invoices(records)
    create_combined_pdf(records, pdf_path)
    build_workbook(
        records=records,
        output_path=workbook_path,
        applicant=applicant,
        department=department,
        reason=reason,
        run_date=dt.date.today(),
    )

    should_save_json = args.save_json or config.get("save_json") or args.mark_reimbursed
    if should_save_json:
        with json_path.open("w", encoding="utf-8") as f:
            json.dump([record.to_jsonable() for record in records], f, ensure_ascii=False, indent=2)

    if args.mark_reimbursed:
        mark_batch_reimbursed(
            input_dir=invoice_dir,
            records=records,
            workbook_path=workbook_path,
            pdf_path=pdf_path,
            json_path=json_path if should_save_json else None,
            timestamp=timestamp,
        )

    recognized = sum(1 for record in records if record.review_status == "已识别")
    total = sum(
        (record.amount_for_total or Decimal("0.00") for record in records if record.include_in_total),
        Decimal("0.00"),
    )
    print("\n完成")
    print(f"发票数量: {len(records)}，已识别: {recognized}，需复核: {len(records) - recognized}")
    print(f"识别金额合计: ¥{total:,.2f}")
    print(f"报销表: {workbook_path}")
    print(f"发票附件PDF: {pdf_path}")
    if should_save_json:
        print(f"识别JSON: {json_path}")
    if args.mark_reimbursed:
        print("本批凭证已登记为已报销")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
