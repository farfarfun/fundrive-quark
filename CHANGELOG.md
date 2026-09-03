# CHANGELOG

本项目遵循[语义化版本](https://semver.org/lang/zh-CN/)，变更记录按版本倒序排列。

## [1.0.4] - 2026-09-03

### 修复

- 日志统一迁移到组织自有包 `farlog`，移除对 `funutil` 的直接依赖
- 删除 `manage.py` 中的诊断性 `print`，改用 `farlog` 记录日志
- 分享链接、任务响应等日志输出做脱敏处理：分享链接中的提取码（`?pwd=`）不再明文写入日志，任务轮询不再整段打印原始响应
- 类型标注统一改为 Python 3.10 原生泛型/联合类型写法（`str | None`、`list[...]`、`dict[...]`），移除 `typing.Optional/List/Dict/Union/Tuple`
- 为 `manage.py` 全部公开函数、公开类与方法补充中文 docstring，说明用途、参数与返回值
- 运行时依赖补充 `requests`，`pyproject.toml` 显式声明版本下限

### 新增

- 补充 `CHANGELOG.md`
- 重新生成并提交 `uv.lock`，同步 `requires-python`（`>=3.10`），保证可复现构建
- README 补充项目简介、安装命令、最小可运行示例
- `tests/` 新增基于 mock 的正常路径与边界测试，覆盖 `get_id_from_url`、`generate_random_code`、`get_datetime`、`QuarkPanManage` 的分享/转存/文件列表等公开方法

### 变更

- `pyproject.toml` 补充 `[project] license = "MIT"` 与 `license-files`，移除冗余的 `[tool.setuptools] license-files = []`
- README 末尾追加组织介绍区块

## [1.0.3] 及更早版本

早期版本未系统记录变更，详见 Git 提交历史。
