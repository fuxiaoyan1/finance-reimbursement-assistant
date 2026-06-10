# Finance Reimbursement Assistant

本仓库是一个本地优先的发票报销助手。它读取用户选择的发票目录，识别 PDF 和图片发票，生成报销 Excel，并把原始发票整理成一个合并 PDF。仓库不包含任何真实发票、企业数据或运行时生成数据。

This repository is a local-first invoice reimbursement assistant. It reads an invoice folder selected by the user, extracts invoice data from PDFs and images, creates a reimbursement workbook, and bundles the source invoices into one PDF. The repository contains no real invoices, company data, or runtime data.

Public review documents:

公开审核文档：

- [Notice / 公告](NOTICE.md)
- [Disclaimer / 免责声明](DISCLAIMER.md)
- [Public release preparation / 公开发布准备](docs/updates/2026-06-10-public-release.md)

## 功能

- 读取一个目录下的 PDF、JPG、PNG、TIFF、WEBP 发票文件。
- 使用 PDF 文字层、本地 OCR 和可选 LLM 识别发票字段。
- 生成 Excel 工作簿，包含 `报销单` 和 `发票清单` 两个工作表。
- 生成合并后的 `发票附件_*.pdf`。
- 维护目录级 `.invoice_agent_ledger.json`，已经登记报销的原始凭证不会被重复处理。
- 维护应用级 `data/reimbursement_batches.json`，前端按年月列示报销批次和月度合计。
- 提供一个本地 Web 页面，用于选择目录、启动处理、查看批次结果。

## Features

- Reads PDF, JPG, PNG, TIFF, and WEBP invoice files from a selected folder.
- Extracts invoice fields from PDF text, local OCR, and an optional LLM pass.
- Creates an Excel workbook with two sheets: `报销单` and `发票清单`.
- Creates a combined `发票附件_*.pdf`.
- Keeps a folder-level `.invoice_agent_ledger.json` so already reimbursed files are skipped later.
- Keeps an app-level `data/reimbursement_batches.json` for monthly batch listing and totals.
- Includes a local web UI for directory selection, processing, and result review.

## 安装

建议使用 Python 3.11 或更新版本。

```bash
cd finance-reimbursement-assistant
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

如需识别扫描件或图片发票，请安装 Tesseract OCR。macOS 可使用：

```bash
brew install tesseract
brew install tesseract-lang
```

如果不安装 OCR，助手仍可处理带文字层的 PDF，但图片识别能力会下降。

## Installation

Python 3.11 or newer is recommended.

```bash
cd finance-reimbursement-assistant
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Install Tesseract OCR if you need scanned invoice or image recognition. On macOS:

```bash
brew install tesseract
brew install tesseract-lang
```

Without OCR, the assistant can still process PDFs with a text layer, but image extraction will be limited.

## Web 使用方法

启动本地页面：

```bash
python3 web_app.py
```

打开：

```text
http://127.0.0.1:5055
```

如需指定端口：

```bash
PORT=5056 python3 web_app.py
```

页面流程：

1. 点击 `发票报销`。
2. 点击 `选择目录`，选择本次要处理的发票文件夹。
3. 点击 `处理并登记`。
4. 处理完成后，页面会展示本月批次、凭证数量、计入张数、报销金额、报销表入口和合并 PDF 入口。
5. 同一个目录后续加入新发票时，再次处理只会处理未登记的新文件；旧文件会根据目录级台账自动跳过。

## Web Usage

Start the local web app:

```bash
python3 web_app.py
```

Open:

```text
http://127.0.0.1:5055
```

To use another port:

```bash
PORT=5056 python3 web_app.py
```

Workflow:

1. Click `发票报销`.
2. Click `选择目录` and choose the invoice folder for the current batch.
3. Click `处理并登记`.
4. When processing completes, the page lists the monthly batch, file count, included invoice count, amount, workbook link, and combined PDF link.
5. If new invoices are added to the same folder later, running the job again only processes new files. Previously registered files are skipped by the folder-level ledger.

## 命令行使用

基本命令：

```bash
python3 invoice_agent.py /path/to/invoice-folder --mark-reimbursed --save-json
```

指定输出目录：

```bash
python3 invoice_agent.py /path/to/invoice-folder -o /path/to/output-folder --mark-reimbursed --save-json
```

填写报销单基础信息：

```bash
python3 invoice_agent.py /path/to/invoice-folder \
  --applicant "Example User" \
  --department "Example Department" \
  --reason "Monthly reimbursement" \
  --mark-reimbursed \
  --save-json
```

禁用 LLM，只使用本地规则和 OCR：

```bash
python3 invoice_agent.py /path/to/invoice-folder --no-llm --mark-reimbursed --save-json
```

## Command Line Usage

Basic command:

```bash
python3 invoice_agent.py /path/to/invoice-folder --mark-reimbursed --save-json
```

Set an output folder:

```bash
python3 invoice_agent.py /path/to/invoice-folder -o /path/to/output-folder --mark-reimbursed --save-json
```

Set reimbursement form fields:

```bash
python3 invoice_agent.py /path/to/invoice-folder \
  --applicant "Example User" \
  --department "Example Department" \
  --reason "Monthly reimbursement" \
  --mark-reimbursed \
  --save-json
```

Disable LLM and use local rules plus OCR only:

```bash
python3 invoice_agent.py /path/to/invoice-folder --no-llm --mark-reimbursed --save-json
```

## LLM 配置

默认 provider 是 `codex`。如果本机已经安装并登录 Codex CLI，脚本会在需要模型辅助判断时调用：

```text
codex exec
```

也可以完全关闭模型：

```bash
python3 invoice_agent.py /path/to/invoice-folder --no-llm
```

HTTP 兼容接口作为可选备用：

```json
{
  "use_llm": true,
  "llm_provider": "http",
  "llm_base_url": "https://example.com/v1",
  "llm_model": "your-model",
  "llm_api_key_env": "LLM_API_KEY"
}
```

然后运行：

```bash
LLM_API_KEY=... python3 invoice_agent.py /path/to/invoice-folder --config config.json
```

## LLM Configuration

The default provider is `codex`. If the Codex CLI is installed and signed in on the machine, the script calls:

```text
codex exec
```

You can disable model use entirely:

```bash
python3 invoice_agent.py /path/to/invoice-folder --no-llm
```

An HTTP-compatible endpoint is available as an optional fallback:

```json
{
  "use_llm": true,
  "llm_provider": "http",
  "llm_base_url": "https://example.com/v1",
  "llm_model": "your-model",
  "llm_api_key_env": "LLM_API_KEY"
}
```

Then run:

```bash
LLM_API_KEY=... python3 invoice_agent.py /path/to/invoice-folder --config config.json
```

## 输出文件

默认输出到发票目录下的 `报销输出/`：

- `报销表_YYYYMMDD_HHMMSS.xlsx`
- `发票附件_YYYYMMDD_HHMMSS.pdf`
- `识别结果_YYYYMMDD_HHMMSS.json`，仅在传入 `--save-json` 时生成

目录中还会写入：

- `.invoice_agent_ledger.json`：记录已登记文件，避免重复处理。

应用目录中会写入：

- `data/ui_state.json`：记录上次选择的目录。
- `data/reimbursement_batches.json`：记录报销批次，供 Web 页面展示。

`data/`、日志、缓存和运行输出默认不会提交到 git。

## Output Files

By default, files are written to `报销输出/` under the invoice folder:

- `报销表_YYYYMMDD_HHMMSS.xlsx`
- `发票附件_YYYYMMDD_HHMMSS.pdf`
- `识别结果_YYYYMMDD_HHMMSS.json`, generated only with `--save-json`

The invoice folder also receives:

- `.invoice_agent_ledger.json`: records reimbursed files and prevents duplicate processing.

The application folder receives:

- `data/ui_state.json`: stores the last selected folder.
- `data/reimbursement_batches.json`: stores reimbursement batches for the web UI.

`data/`, logs, caches, and runtime output are ignored by git by default.

## 配置文件

可以复制 `config.example.json` 并按需修改：

```bash
cp config.example.json config.json
python3 invoice_agent.py /path/to/invoice-folder --config config.json --mark-reimbursed --save-json
```

常用配置项：

- `applicant`：报销人。
- `department`：部门。
- `reason`：报销事由。
- `serial_prefix`：自定义发票序号前缀。
- `ocr_lang`：OCR 语言，例如 `chi_sim+eng`。
- `category_rules`：自定义分类规则，格式为 `类别=关键词1,关键词2`。
- `llm_only_review_items`：默认只对需要复核的候选调用 LLM。
- `llm_max_files`：单次运行最多调用 LLM 的文件数。

## Config File

Copy `config.example.json` and adjust it as needed:

```bash
cp config.example.json config.json
python3 invoice_agent.py /path/to/invoice-folder --config config.json --mark-reimbursed --save-json
```

Common fields:

- `applicant`: applicant name.
- `department`: department name.
- `reason`: reimbursement reason.
- `serial_prefix`: custom invoice serial prefix.
- `ocr_lang`: OCR language, for example `chi_sim+eng`.
- `category_rules`: custom classification rules in `Category=keyword1,keyword2` format.
- `llm_only_review_items`: call the LLM only for candidates needing review by default.
- `llm_max_files`: maximum LLM calls per run.

## 隐私与数据边界

- 本仓库只包含代码、提示词、技能说明和示例配置。
- 不包含任何真实发票、人员信息、企业信息或历史运行结果。
- 发票内容只在本机处理；是否调用本机 Codex CLI 或外部 HTTP endpoint 由用户配置决定。
- 上传公开仓库前，请确认没有把运行时生成的 `data/`、发票目录、报销输出或本地配置文件加入 git。
- 使用仓库内提交助手 `agents/skills/github-submit-assistant/` 可在提交前执行隐私和发布检查。

## Privacy and Data Boundary

- This repository contains only code, prompts, skill instructions, and an example config.
- It contains no real invoices, personal data, company data, or historical runtime results.
- Invoice content is processed locally unless the user configures the Codex CLI or an external HTTP endpoint.
- Before publishing a repository, confirm that runtime `data/`, invoice folders, reimbursement outputs, and local config files are not tracked by git.
- Use the repository-local submit assistant under `agents/skills/github-submit-assistant/` for privacy and release checks before committing.

## 开发检查

```bash
git diff --check
node agents/skills/github-submit-assistant/scripts/check-release-safety.mjs .
python3 -m py_compile invoice_agent.py web_app.py
python3 -m unittest discover -s tests -v
```

## Development Checks

```bash
git diff --check
node agents/skills/github-submit-assistant/scripts/check-release-safety.mjs .
python3 -m py_compile invoice_agent.py web_app.py
python3 -m unittest discover -s tests -v
```
