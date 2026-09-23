# 实习招聘投递准备助手 V1

这是一个在本地运行、由求职者审核的实习申请准备工具。它读取用户明确指定的招聘链接或本地材料，整理岗位信息、做确定性匹配初筛、生成证据关联的简历版本和带附件邮件草稿，并停在发送前。

## V1 能做什么

- 读取公开网页、公众号文章公开链接、本地 HTML、Markdown 或纯文本。
- 提取公司、岗位、地点、实习要求、截止日期、投递邮箱、邮件主题和附件命名规则。
- 保存来源快照和哈希，生成稳定 `job_id`，避免重复收录。
- 按经验匹配、硬性要求、个人方向、现实约束和风险项给出 100 分制初筛结果。
- 选择已配置的岗位方向母版，只对已有证据条目进行排序，并记录经历编号。
- 生成 Word、PDF、招聘方要求名称的附件、邮件正文和带附件 `.eml`。
- 更新岗位档案、匹配结果和投递台账，状态只写为“待评估”或“待确认”。

评分用于整理优先级，不代表录取概率。V1 没有邮件发送、招聘平台投递、登录绕过、验证码处理或后台批量爬取功能。

## 代码与个人数据分离

本仓库只保存程序、通用配置示例和虚构测试材料。实际配置、简历、招聘原文、邮件草稿和投递台账应放在仓库外的本地求职档案中。

推荐结构：

```text
career-workspace/
├── internship-agent-v1/       # 本仓库
└── job-search-cases/main/      # 本地个人数据，不进入本仓库
```

## 准备环境

需要 Python 3.11 或更高版本、LibreOffice 命令 `soffice`，以及：

```bash
python -m pip install -r requirements.txt
```

## 配置

1. 将 `config.example.toml` 复制到仓库外，命名为 `config.local.toml`。
2. 将 `sources.example.toml` 复制到同一位置，命名为 `sources.local.toml`。
3. 设置 `JOB_CASE_DIR`，或在本地配置中填写求职档案路径。
4. 为每个岗位方向配置母版 DOCX、岗位关键词、经历编号和可重排段落索引。

配置使用 TOML，可由 Python 标准库读取，不需要额外 YAML 解析依赖。

## 运行

先检查配置：

```bash
python run.py --config /path/to/config.local.toml doctor
```

再运行完整流程：

```bash
python run.py --config /path/to/config.local.toml run
```

检查代码仓库是否混入敏感文件：

```bash
python run.py privacy-check --repo .
```

## 输出

符合准备条件的岗位会在本地求职档案中得到：

```text
application-drafts/<job_id>/
├── <岗位版本>.docx
├── <岗位版本>.pdf
├── attachments/<招聘要求名称>.pdf
├── resume-edit-plan.json
├── resume-qa.json
├── email.md
├── email.eml
└── approval.json
```

`approval.json` 固定记录 `external_action_performed: false`。只有求职者确认并自行完成外部操作后，投递台账才能改为“已投递”。

## 测试

```bash
python -m unittest discover -s tests -v
```

测试覆盖字段提取、岗位去重、简历证据条目排序、邮件附件以及重复运行的幂等性。
