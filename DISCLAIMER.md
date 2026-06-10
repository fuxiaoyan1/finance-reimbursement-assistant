# Disclaimer / 免责声明

This project is a local-first invoice reimbursement assistant. It helps organize invoice files, extract structured fields, generate an Excel reimbursement workbook, and combine invoice attachments into one PDF.

本项目是本地优先的发票报销助手，用于整理发票文件、抽取结构化字段、生成 Excel 报销工作簿，并把发票附件合并为一个 PDF。

## Not Professional Advice / 非专业意见

The output is not financial, tax, accounting, audit, or legal advice. Users must verify every invoice, amount, category, duplicate judgment, tax amount, and reimbursement total before using the output for any official purpose.

本项目输出不构成财务、税务、会计、审计或法律意见。用户在将结果用于任何正式用途前，必须复核每张发票、金额、分类、重复判断、税额和报销合计。

## Extraction Risk / 识别风险

PDF text layers, OCR results, and LLM extraction may misread numbers, dates, invoice numbers, sellers, buyers, or totals. Low-confidence and review-needed records should be treated as prompts for manual checking, not as final conclusions.

PDF 文字层、OCR 结果和 LLM 抽取都可能误读数字、日期、发票号码、销售方、购买方或总额。低置信度和需复核记录只能作为人工检查提示，不能视为最终结论。

## Data Boundary / 数据边界

The repository is designed to contain code and documentation only. Real invoices, generated reimbursement files, local ledgers, local paths, and organization-specific data should not be committed.

本仓库设计上只应包含代码和文档。真实发票、生成的报销文件、本地台账、本地路径和特定组织数据不应提交到仓库。

## Model Boundary / 模型边界

By default, the assistant can use a local Codex CLI provider when available. Users may disable LLM use or configure an HTTP-compatible endpoint. Users are responsible for deciding whether invoice contents may be sent to any configured model service.

默认情况下，本助手可在本机 Codex CLI 可用时调用它。用户也可以禁用 LLM，或配置 HTTP 兼容接口。用户需要自行判断发票内容是否允许发送到所配置的模型服务。

## No Warranty / 无保证

The project is provided as-is. It may contain bugs, incomplete extraction rules, environment assumptions, or platform-specific behavior. Users are responsible for testing it in their own workflow.

本项目按现状提供，可能存在缺陷、不完整识别规则、环境假设或平台相关行为。用户需要在自己的工作流中自行测试。
