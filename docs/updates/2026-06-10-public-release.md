# Public Release Preparation / 公开发布准备

Date: 2026-06-10

日期：2026-06-10

## Summary / 摘要

This update prepares the finance reimbursement assistant for public visibility on GitHub while keeping the repository focused on reimbursement only.

本次更新将财务报销助手准备为 GitHub 对外可见仓库，同时保持仓库只聚焦报销功能。

## Scope / 范围

- Added repository-level notice and disclaimer documents.
- Added a repository-local GitHub submit assistant under `agents/skills/github-submit-assistant/`.
- Added an automated release safety check for tracked data artifacts, private terms, and bilingual public documentation.
- Kept salary, payroll, course-management, real invoice data, and organization-specific data out of the public repository.

- 新增仓库级公告和免责声明。
- 在 `agents/skills/github-submit-assistant/` 下新增仓库内 GitHub 提交助手。
- 新增自动发布安全检查，用于检查已跟踪数据产物、私有词和公开双语文档。
- 公开仓库继续排除工资、薪酬、课程管理、真实发票数据和特定组织数据。

## Validation / 验证

Before publishing this update, run:

发布本更新前运行：

```bash
git diff --check
node agents/skills/github-submit-assistant/scripts/check-release-safety.mjs .
python3 -m py_compile invoice_agent.py web_app.py
python3 -m unittest discover -s tests -v
```
