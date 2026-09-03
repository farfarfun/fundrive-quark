# fundrive-quark

夸克网盘（Quark）Web API 封装，提供分享链接解析、文件转存、文件列表、
文件夹批量分享等常用能力，供 [fundrive](https://github.com/farfarfun/fundrive)
及其他项目以统一命名空间接入夸克网盘。

## 安装

```bash
pip install fundrive-quark
# 或
uv add fundrive-quark
```

## 快速开始

```python
from fundrives.quark.manage import QuarkPanManage

# cookies 为已登录夸克网盘账号的浏览器 Cookie 字符串
drive = QuarkPanManage(cookies="your-cookie-string")

# 将他人分享的文件转存到自己网盘的根目录
drive.save_shared("https://pan.quark.cn/s/xxxxxxxx", folder_id="0")

# 列出根目录文件
file_list = drive.get_file_list(pdir_fid="0")
for item in file_list["data"]["list"]:
    print(item["file_name"])
```

## 主要能力

- 分享链接解析、转存（单文件 `store`、批量 `save_shared`）
- 文件/文件夹列表、搜索、删除、新建目录
- 文件夹批量分享（`share`/`share_retry`），支持提取码与失败重试

---

## 关于 farfarfun

[farfarfun](https://github.com/farfarfun) 是一个专注于实用工具库的开源组织，
涵盖云存储、数据处理、AI、多媒体与开发工具链等方向。

- 🏠 组织主页：<https://github.com/farfarfun>
- 📦 PyPI：<https://pypi.org/user/niuliangtao/>
- 📧 联系：farfarfun@qq.com

本项目基于 [MIT](LICENSE) 协议开源。
