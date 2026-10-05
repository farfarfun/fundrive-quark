# CHANGELOG

本项目遵循[语义化版本](https://semver.org/lang/zh-CN/)，变更记录按版本倒序排列。

> **发布状态**：本包**从未发布到 PyPI**（`https://pypi.org/pypi/fundrive-quark/json` 返回 404），
> 下面各版本号只是仓库内部的里程碑标记，不存在任何已对外发布的公开 API 契约，
> 因此也不存在需要用主版本号保护的线上兼容负担。安装方式见 README。

## [未发布]

### 修复

- `QuarkPanManage.get_pwd_id` 对不含 `/s/` 段的链接抛裸 `IndexError`（原实现
  `split("/s/")[1]`），调用方拿不到任何上下文；改为抛带原链接的 `QuarkPanError`，
  与 `store()` 走的 `get_id_from_url` 行为对齐
- `get_file_list` 把 Python 的 `bool` 直接当 `_fetch_total` 查询参数传给 `requests`，
  会被编码成 `_fetch_total=True`。夸克接口的开关参数一律是 `"1"`/`"0"`（同文件的
  `_fetch_sub_dirs`、`_is_hl`、`force` 以及 `search_file` 的 `_fetch_total` 都是），
  服务端不识别 `True` 时 `metadata._total` 可能缺失，依赖它翻页的 `share()` 就只会
  处理第一页；改为显式传 `"1"`/`"0"`
- `share()` 的目录 ID 解析 `share_url.rsplit("/", 1)[1].split("-")[0]` 在地址形状不对时
  会悄悄算出一个无意义的字符串（甚至空串）拿去当目录 ID 翻页，错得毫无提示；抽成
  `_parse_pdir_fid()` 并在解析不出非空 fid 时抛 `QuarkPanError`
- `share()` 的 `folder_id` 参数此前声明了却从未使用（docstring 写着「保留参数」）；
  现在给了就优先使用，不给才回落到从网页地址解析
- `store()` 里的 `detail.get("title") or detail.get("file_name", "")`：`get_detail`
  组装的条目只有 `file_name` 没有 `title`，左分支是永远走不到的死代码，已移除

### 变更

- README 补充「环境要求」小节：Python `>=3.10`、运行时依赖、必需的夸克登录 Cookie
  及其取法与保密提醒、需直连 `pan.quark.cn` / `drive-pc.quark.cn`、无系统依赖
- README 快速开始示例改为从环境变量读取 Cookie，不再出现写死凭据的写法；
  批量分享示例的文件夹地址改成真实形状 `<目录fid>-<目录名>`
- `uv.lock` 按组织规范不再纳入版本管理，已从仓库移除并保留在 `.gitignore` 中（`9440310`）。
  `[1.1.0]` / `[1.0.4]` 里关于 `uv.lock` 的记述只反映当时状态，不代表现行做法。

### 测试

- 新增 7 条用例：`get_pwd_id` 的两条失败路径、`_parse_pdir_fid` 的正常/边界/失败路径、
  `share()` 对 `folder_id` 的优先使用与 URL 回落、`_fetch_total` 的参数编码

## [1.1.0] - 2026-10-02

### 修复

- **`get_share_task_id` 必定崩溃**：原本调用 `self.request("share", "post", json=...)`，
  而 `request()` 内部已经用 `json=data` 传体，`requests.request()` 因此收到两个
  `json` 关键字参数，每次调用都抛 `TypeError: got multiple values for keyword
  argument 'json'`。该函数是 `share()` / `share_retry()` 的必经路径，意味着
  「文件夹批量分享」这两个对外能力此前 **100% 不可用**，而外层的
  `except Exception` 把它压成一行「分享失败」日志，从外面完全看不出功能已全挂。
  现改为 `data=json_data`
- **`get_detail` 隐式返回 `None`**：`for page in range(1, 100)` 耗尽后函数直接落到
  末尾，与 `-> tuple[...]` 标注不符，`save_shared` 会在解包时报
  `cannot unpack non-sequence NoneType`。现翻到 `MAX_DETAIL_PAGES` 上限仍未取完时
  抛带 `pwd_id` / `pdir_fid` / 已取条数的 `QuarkPanError`，既不静默截断也不返回 `None`
- **`task()` 把 task_id 放进 GET 请求的 JSON body**（`data=` 而非 `params=`），
  服务端根本收不到，等于每次都在查一个空任务；同目录的 `submit_task` /
  `get_share_id` 都是用 `params=`。现统一为 `params=`，并把
  「任务已完成」的判定从 `if status:`（status=1 的中间态也会被当成完成）收紧为
  `status == 2`，否则 `store()` 取 `data.save_as` 时会 `KeyError`
- **轮询耗尽返回假值**：`submit_task` 返回 `None`、`task()` 返回 `False`，
  而 `save_shared` 根本不看返回值、`store()` 直接 `.get()` 下钻 —— 转存失败会被
  当成成功，或者炸在无关的位置。两者现在都抛 `QuarkPanError`
- **裸 `requests` 调用缺 timeout**：`get_user_info` 的 `requests.get`、
  `get_share_link` 的 `requests.post` 都没有超时，服务端不响应即无限挂起。
  `get_user_info` 补上 `timeout`（可配），`get_share_link` 改为复用已带超时的
  `self.request`（两者本就是同一个 `share/password` 接口）
- **非 JSON 响应没有上下文**：网关错误页会让 `.json()` 抛一句
  `Expecting value: line 1 column 1`。现统一经 `_parse_json` 转为 `QuarkPanError`，
  信息里带接口路径、HTTP 状态码和响应片段。夸克的业务错误是 HTTP 200 + JSON 里的
  `status`/`code`，所以**不**加 `raise_for_status`，以免把可处理的业务错误变成异常
- **`safe_copy` 吞掉 `OSError`**：复制失败只记一条日志就正常返回，调用方无法区分
  「源文件不存在所以跳过」和「复制真的失败了」。现在跳过用返回值 `False` 表达，
  复制失败原样抛 `OSError`
- 删除 `share()` 里凭空创建、从未被使用的 `share` 目录副作用；修正循环内把入参
  `share_url` 覆盖成分享结果链接的写法

### 变更

- **返回值语义**（本包尚未发布，无线上兼容负担）：`save_shared` 返回 `bool`、
  `store` 返回新分享链接、`share` 返回失败条目列表（格式可直接喂给
  `share_retry`）、`share_retry` 返回仍失败的行、`safe_copy` 返回 `bool`
- 批量流程的 `except Exception` 收窄为 `RECOVERABLE_API_ERRORS`
  （`requests.RequestException` / `QuarkPanError` / `KeyError` / `IndexError`），
  `TypeError`、`AttributeError` 这类编程错误不再被重试循环吞掉 ——
  上面那个 `json=` 传参 bug 正是被它掩盖了整个版本周期；`share()` 外层改为抛出
  带当前目录与进度的 `QuarkPanError`，不再静默返回让调用方以为全部分享成功
- `share` / `share_retry` 重复的三次重试逻辑抽成 `_share_folder_with_retry`
- 新增 `QuarkPanError` 领域异常，`fundrives.quark` 包导出公开 API
- 恢复 `fundrives` 为 PEP 420 隐式命名空间包（上一轮自动整改把
  `src/fundrives/__init__.py` 重新加了回来，与 todo-list#394 的结论相反；
  该目录由 fundrive-alipan/baidu/lanzou/quark 共享，带 `__init__.py` 会相互覆盖），
  并补充测试防止再次漂移
- `py.typed` 从共享的命名空间目录移到 `fundrives/quark/`，避免多个驱动包的
  PEP 561 标记文件互相覆盖
- 补齐 `ruff`：加入开发依赖并新增 `[tool.ruff]` / `[tool.ruff.lint]` 配置
  （`ruff check` 与 `ruff format` 均通过）；开发依赖的 pytest 下限抬到 `>=8.0.0`
- `uv.lock` 升级传递依赖 urllib3 `2.7.0` → `2.8.0`（修复 `HTTPResponse.stream()`
  无界缓冲等已知漏洞），不涉及本包代码
- README 的安装方式改为从仓库安装（本包尚未发布到 PyPI），并补充返回值/异常约定

### 测试

- 新增 25 条用例，覆盖上述每一条修复；全部做过反向验证（把实现逐条回退到修复前，
  确认对应用例真的失败），其中包括批量分享崩溃、分页上限、GET 参数位置、
  轮询状态判定、超时参数、非 JSON 响应、命名空间包布局与 `py.typed` 位置

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
