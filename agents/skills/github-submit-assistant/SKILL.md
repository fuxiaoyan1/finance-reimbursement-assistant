---
name: github-submit-assistant
description: Use before publishing updates to this finance reimbursement assistant: inspect all dirty work, keep bilingual public docs current, verify no private invoice/company/salary/course data is tracked, run tests, commit locally, and push to GitHub.
---

# GitHub Submit Assistant

Use this skill before any GitHub-facing update of the finance reimbursement assistant, especially before making the repository public, changing invoice extraction behavior, changing LLM providers, changing web workflows, or updating public documentation.

在财务报销助手进行任何面向 GitHub 的更新前使用本 skill，尤其是仓库公开、发票识别逻辑变更、LLM provider 变更、Web 工作流变更或公开文档更新时。

## Required Workflow / 必需流程

1. Inspect repository state.
   检查仓库状态。
   - `git status --short --branch`
   - `git remote -v`
   - `git log --oneline origin/main..HEAD`
   - `git diff --name-status origin/main`
2. Treat a user request to submit or publish all current project work as permission to stage every tracked and untracked change in this repository with `git add -A`.
   当用户要求提交或发布当前项目全部工作时，视为允许在本仓库内用 `git add -A` 暂存全部已跟踪和未跟踪变更。
3. Keep public documentation bilingual where practical.
   尽量保持公开文档中英双语。
   - `README.md`
   - `NOTICE.md`
   - `DISCLAIMER.md`
   - `docs/updates/*.md`
   - `agents/README.md`
   - `agents/skills/github-submit-assistant/SKILL.md`
4. Update or add a dated file under `docs/updates/` for user-visible changes.
   对用户可见变更，在 `docs/updates/` 下更新或新增带日期说明文件。
5. Preserve the reimbursement-only public scope.
   保持公开版仅包含报销功能。
   - Do not add salary or payroll features.
     不添加工资功能。
   - Do not add course-management features.
     不添加课程管理功能。
   - Do not commit real invoice files, generated workbooks, generated PDFs, runtime ledgers, local config, or organization-specific data.
     不提交真实发票、生成工作簿、生成 PDF、运行台账、本地配置或特定组织数据。
6. Run validation before commit.
   提交前运行验证。
   - `git diff --check`
   - `node agents/skills/github-submit-assistant/scripts/check-release-safety.mjs .`
   - `python3 -m py_compile invoice_agent.py web_app.py`
   - `python3 -m unittest discover -s tests -v`
7. Commit with a concise Chinese message naming the release scope.
   使用简洁中文提交信息，并点明发布范围。
8. Push the current branch to `origin`.
   将当前分支推送到 `origin`。
9. Report commit hash, pushed branch, changed public docs, and validation results.
   汇报提交哈希、推送分支、变更的公开文档和验证结果。

## Privacy Contract / 隐私合同

Before publishing, verify that tracked files contain no real user or organization data. The safety script checks common private terms and artifact patterns, but human review is still required.

发布前必须确认已跟踪文件不包含真实用户或组织数据。安全脚本会检查常见私有词和产物模式，但仍需要人工复核。

The repository must not track:

仓库不得跟踪：

- invoice PDFs or invoice images;
  发票 PDF 或发票图片；
- generated reimbursement workbooks or combined PDFs;
  生成的报销工作簿或合并 PDF；
- `data/`, local ledgers, or `.invoice_agent_ledger.json`;
  `data/`、本地台账或 `.invoice_agent_ledger.json`；
- salary, payroll, course-management, or organization-specific workflow code;
  工资、薪酬、课程管理或特定组织工作流代码；
- private local paths, secrets, access tokens, or API keys.
  私有本地路径、密钥、访问令牌或 API key。

## Documentation Contract / 文档合同

`README.md` is the front door. It should link to `NOTICE.md`, `DISCLAIMER.md`, and the latest relevant `docs/updates/*.md` when the repository is prepared for public review or public visibility.

`README.md` 是入口文档。仓库准备公开审核或对外可见时，它应链接 `NOTICE.md`、`DISCLAIMER.md` 和最新相关的 `docs/updates/*.md`。

`NOTICE.md` records data and license boundaries. `DISCLAIMER.md` records financial-review, extraction-risk, model-use, and no-warranty boundaries.

`NOTICE.md` 记录数据和许可边界。`DISCLAIMER.md` 记录财务复核、识别风险、模型使用和无保证边界。

## Output Expectations / 输出要求

Final user responses should stay short and include:

最终回复应保持简短，并包含：

- local commit hash;
  本地提交哈希；
- pushed remote and branch;
  已推送远端和分支；
- public repository URL when visibility changes;
  可见性变化时的公开仓库地址；
- changed public documentation paths;
  变更的公开文档路径；
- validation commands that passed;
  已通过的验证命令；
- any blocked browser/GitHub action.
  任何受阻的浏览器或 GitHub 操作。
