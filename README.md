# fundrive-quark

夸克网盘（Quark）Web API 封装，提供分享链接解析、文件转存、文件列表、
文件夹批量分享等常用能力，供 [fundrive](https://github.com/farfarfun/fundrive)
及其他项目以统一命名空间接入夸克网盘。

## 环境要求

| 项目 | 要求 |
|---|---|
| Python | `>=3.10`（用到 `str \| None` 等原生联合类型写法） |
| 运行时依赖 | `requests>=2.28`、`farlog>=1.1.7`，随安装自动拉取 |
| 账号凭据 | 已登录夸克网盘账号的浏览器 Cookie 字符串，必填 |
| 网络 | 需能直连 `pan.quark.cn` 与 `drive-pc.quark.cn`（调用的是夸克的私有 Web API，非官方开放平台接口） |
| 系统依赖 | 无，纯 Python |

Cookie 的取法：浏览器登录 <https://pan.quark.cn>，开发者工具 → Network →
任意一个 `drive-pc.quark.cn` 请求 → 复制请求头里的 `cookie` 整行。
该 Cookie 等同于账号登录态，**不要写进代码或提交到仓库**，建议走环境变量读取。

## 安装

```bash
pip install fundrive-quark
# 或
uv add fundrive-quark
```

## 包名与导入路径

| 名字 | 值 |
|---|---|
| GitHub 仓库 | `farfarfun/fundrive-quark` |
| PyPI 发布名 | `fundrive-quark` |
| 导入路径 | `fundrives.quark` |

发布名与仓库名一致；导入路径里的 `fundrives`（带 `s`，与主包 `fundrive` 不是同一个名字）
是 `fundrive-alipan`、`fundrive-baidu`、`fundrive-lanzou`、`fundrive-quark` 这组驱动包
**共用的 PEP 420 隐式命名空间**，每个发布包只占其下一个子目录（本包是 `fundrives/quark/`），
这样多个驱动可以同时安装、合并到同一个 `fundrives` 命名空间下。

因此 `src/fundrives/` 下**不要**放 `__init__.py`：一旦放了，隐式命名空间会退化成常规包，
先安装的 wheel 会直接屏蔽其余兄弟驱动。该布局是组织层面的决议
（见 [farfarfun/todo-list#394](https://github.com/farfarfun/todo-list/issues/394)），
`tests/test_manage.py` 里的 `test_fundrives_is_pep420_namespace_package` 守住这个契约。
PEP 561 的 `py.typed` 同理放在 `fundrives/quark/` 而不是共享的命名空间根目录。

## 快速开始

```python
from fundrives.quark import QuarkPanError, QuarkPanManage

import os

# cookies 为已登录夸克网盘账号的浏览器 Cookie 字符串，从环境变量读取，不要写死在代码里
drive = QuarkPanManage(cookies=os.environ["QUARK_COOKIES"])

# 将他人分享的文件转存到自己网盘的根目录
# 返回 True 表示转存任务确实完成，False 表示被跳过（网盘中已存在、分享为空等）
saved = drive.save_shared("https://pan.quark.cn/s/xxxxxxxx", folder_id="0")

# 列出根目录文件
file_list = drive.get_file_list(pdir_fid="0")
for item in file_list["data"]["list"]:
    print(item["file_name"])

# 批量分享：传网盘的文件夹网页地址，末段形如 `<目录fid>-<目录名>`
# 返回失败条目，可直接喂回 share_retry 重试
try:
    failed = drive.share("https://pan.quark.cn/list#/list/all/<目录fid>-<目录名>")
    if failed:
        drive.share_retry("\n".join(failed))
except QuarkPanError as e:
    print(f"批量分享中断：{e}")
```

## 主要能力

- 分享链接解析、转存（单文件 `store`、批量 `save_shared`）
- 文件/文件夹列表、搜索、删除、新建目录
- 文件夹批量分享（`share`/`share_retry`），支持提取码与失败重试

## 返回值与异常约定

- 「跳过」用返回值表达（`save_shared` 返回 `False`、`safe_copy` 返回 `False`、
  `share`/`share_retry` 返回失败条目列表），调用方据此判断是否真的做了事。
- 「失败」一律抛异常：接口返回非 JSON、异步任务轮询超时、分页超过上限等都抛
  `QuarkPanError`，异常信息带接口路径 / task_id / 目录等上下文；网络层错误直接
  透出 `requests.RequestException`。不会出现「记一条日志后装作成功」的情况。

---

## 关于 farfarfun

[farfarfun](https://github.com/farfarfun) 是一个专注于实用工具库的开源组织，
涵盖云存储、数据处理、AI、多媒体与开发工具链等方向。

- 🏠 组织主页：<https://github.com/farfarfun>
- 📦 PyPI：<https://pypi.org/user/niuliangtao/>
- 📧 联系：farfarfun@qq.com

本项目基于 [MIT](LICENSE) 协议开源。
