"""夸克网盘 Web API 封装。

基于登录 Cookie 调用夸克网盘的私有 Web API，提供分享链接解析、文件转存、
文件夹批量分享、文件列表与删除等公开能力。
"""

import os
import random
import re
import shutil
import string
import time
from datetime import datetime
from typing import Any

import requests
from farlog import getLogger

logger = getLogger("fundrive")


def get_id_from_url(url: str) -> str:
    """从夸克分享链接中提取 pwd_id。

    参数:
        url: 形如 `https://pan.quark.cn/s/<pwd_id>` 的分享链接。

    返回:
        提取到的 pwd_id；未匹配到时返回空字符串。
    """
    pattern = r"/s/(\w+)"
    match = re.search(pattern, url)
    if match:
        return match.group(1)
    return ""


def safe_copy(src: str, dst: str) -> None:
    """安全复制文件：源文件不存在则跳过，目标已存在则先删除再复制。

    参数:
        src: 源文件路径。
        dst: 目标文件路径。
    """
    if not os.path.exists(src):
        logger.warning(f"源文件不存在，跳过复制：{src}")
        return

    if os.path.exists(dst):
        os.remove(dst)
        logger.info(f"目标文件已存在，已删除：{dst}")

    try:
        shutil.copy(src, dst)
        logger.info(f"文件已复制到：{dst}")
    except Exception as e:
        logger.error(f"备份 share_url.txt 文件错误：{e}")


def generate_random_code(length: int = 4) -> str:
    """生成指定长度的随机字母数字提取码。

    参数:
        length: 提取码长度，默认为 4。

    返回:
        随机生成的提取码字符串。
    """
    characters = string.ascii_letters + string.digits
    return "".join(random.choice(characters) for _ in range(length))


def get_datetime(
    timestamp: float | None = None, fmt: str = "%Y-%m-%d %H:%M:%S"
) -> str:
    """将时间戳格式化为字符串，未传入时间戳时使用当前时间。

    参数:
        timestamp: 秒级时间戳；为 `None` 或非数值类型时使用当前时间。
        fmt: 输出的时间格式，默认 `%Y-%m-%d %H:%M:%S`。

    返回:
        格式化后的时间字符串。
    """
    if timestamp is None or not isinstance(timestamp, (int, float)):
        return datetime.today().strftime(fmt)
    return datetime.fromtimestamp(timestamp).strftime(fmt)


def _mask_share_url(url: str) -> str:
    """脱敏分享链接中可能携带的提取码等查询参数，仅用于日志输出。

    参数:
        url: 原始分享链接，可能形如 `https://.../s/xxx?pwd=yyyy`。

    返回:
        去除 `?` 及之后查询参数的链接；不影响函数返回给调用方的原始链接。
    """
    return url.split("?", 1)[0]


class QuarkPanManage:
    """夸克网盘 Web API 客户端。

    使用已登录账号的 Cookie 调用分享、转存、文件管理等私有接口。
    """

    def __init__(self, cookies: str, *args: Any, **kwargs: Any) -> None:
        """初始化客户端。

        参数:
            cookies: 已登录夸克网盘账号的 Cookie 字符串。
        """
        # self.base_url = 'https://drive.quark.cn/1/clouddrive'
        self.base_url = "https://drive-pc.quark.cn/1/clouddrive"

        self.headers: dict[str, str] = {
            "user-agent": "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko)"
            " Chrome/94.0.4606.71 Safari/537.36 Core/1.94.225.400 QQBrowser/12.2.5544.400",
            "origin": "https://pan.quark.cn",
            "referer": "https://pan.quark.cn/",
            "accept-language": "zh-CN,zh;q=0.9",
            "cookie": cookies,
        }

    @staticmethod
    def get_pwd_id(share_url: str) -> str:
        """从分享链接中提取 pwd_id。

        参数:
            share_url: 形如 `https://pan.quark.cn/s/<pwd_id>` 的分享链接。

        返回:
            提取到的 pwd_id。
        """
        return share_url.split("?")[0].split("/s/")[1]

    @staticmethod
    def extract_urls(text: str) -> str:
        """从文本中提取出现的第一个 URL。

        参数:
            text: 待提取的文本内容。

        返回:
            匹配到的第一个 URL。

        异常:
            IndexError: 文本中不包含任何可识别的 URL 时抛出。
        """
        url_pattern = r'https?://[^\s<>"]+|www\.[^\s<>"]+'
        return re.findall(url_pattern, text)[0]

    def request(
        self,
        uri: str,
        method: str = "GET",
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        data: Any = None,
        timeout: int = 10,
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """向夸克网盘私有接口发起请求并返回解析后的 JSON。

        参数:
            uri: 接口路径，会拼接在 `base_url` 之后。
            method: HTTP 方法，默认为 `GET`。
            params: 请求的查询参数，会自动补充签名相关的公共参数。
            headers: 请求头，默认为客户端初始化时的 `self.headers`。
            data: 作为 JSON body 发送的请求数据。
            timeout: 请求超时时间（秒）。

        返回:
            接口返回的 JSON 反序列化结果。
        """
        url = f"{self.base_url}/{uri}"
        params = params or {}
        params.update(
            {
                "pr": "ucpro",
                "fr": "pc",
                "uc_param_str": "",
                "__dt": random.randint(100, 9999),
                "__t": int(time.time()) * 1000,
            }
        )
        return requests.request(
            method,
            url,
            params=params,
            headers=headers or self.headers,
            json=data,
            timeout=timeout,
            *args,
            **kwargs,
        ).json()

    def get_stoken(self, pwd_id: str) -> str:
        """获取分享链接对应的 stoken，用于后续访问分享详情与转存。

        参数:
            pwd_id: 分享链接的 pwd_id。

        返回:
            stoken 字符串；获取失败时返回空字符串。
        """
        data = {"pwd_id": pwd_id, "passcode": ""}
        json_data = self.request(
            "share/sharepage/token",
            "post",
            data=data,
        )
        if json_data["status"] == 200 and json_data["data"]:
            stoken = json_data["data"]["stoken"]
        else:
            stoken = ""
            logger.info(f"文件转存失败，{json_data['message']}")
        return stoken

    def get_detail(
        self,
        pwd_id: str,
        stoken: str,
        pdir_fid: str = "0",
        size: int = 50,
        sort: str = "file_type:asc,updated_at:desc",
    ) -> tuple[str, list[dict[str, int | str]]]:
        """分页获取分享链接下的文件/文件夹详情。

        参数:
            pwd_id: 分享链接的 pwd_id。
            stoken: `get_stoken` 获取到的访问令牌。
            pdir_fid: 起始目录 ID，默认为根目录 `"0"`。
            size: 每页数量，默认 50。
            sort: 排序方式。

        返回:
            `(is_owner, file_list)` 二元组：`is_owner` 表示当前账号是否已拥有
            该分享内容，`file_list` 为文件/文件夹信息列表。
        """
        file_list: list[dict[str, int | str]] = []
        for page in range(1, 100):
            params = {
                "pwd_id": pwd_id,
                "stoken": stoken,
                "pdir_fid": pdir_fid,
                "force": "0",
                "_page": page,
                "_size": size,
                "_sort": sort,
            }

            json_data = self.request("share/sharepage/detail", "get", params=params)
            is_owner = json_data["data"]["is_owner"]
            _total = json_data["metadata"]["_total"]
            if _total < 1:
                return is_owner, file_list

            _size = json_data["metadata"]["_size"]  # 每页限制数量
            _count = json_data["metadata"]["_count"]  # 当前页数量

            _list = json_data["data"]["list"]

            for file in _list:
                file_list.append(
                    {
                        "fid": file["fid"],
                        "file_name": file["file_name"],
                        "file_type": file["file_type"],
                        "dir": file["dir"],
                        "pdir_fid": file["pdir_fid"],
                        "include_items": file["include_items"]
                        if "include_items" in file
                        else "",
                        "share_fid_token": file["share_fid_token"],
                        "status": file["status"],
                    }
                )
            if _total <= _size or _count < _size:
                return is_owner, file_list

    def get_user_info(self) -> Any:
        """获取当前登录账号的用户信息。

        返回:
            接口返回的用户信息 JSON。
        """
        params = {
            "fr": "pc",
            "platform": "pc",
        }
        return requests.get(
            "https://pan.quark.cn/account/info", params=params, headers=self.headers
        ).json()

    def create_dir(self, pdir_name: str = "新建文件夹", pdir_fid: str = "") -> Any:
        """创建文件夹。

        参数:
            pdir_name: 文件夹名称，默认为“新建文件夹”。
            pdir_fid: 父目录 ID，默认为根目录。

        返回:
            接口返回的创建结果 JSON。
        """
        json_data = {
            "pdir_fid": pdir_fid,
            "file_name": pdir_name,
            "dir_path": "",
            "dir_init_lock": False,
        }
        return self.request("file", "post", data=json_data)

    def save_shared(
        self,
        share_url: str,
        folder_id: str | None = None,
    ) -> None:
        """将他人分享的文件/文件夹转存到自己网盘的指定目录。

        参数:
            share_url: 待转存的分享链接。
            folder_id: 目标目录 ID；为空时会跳过转存并提示重新获取。
        """
        logger.info(f"文件分享链接：{_mask_share_url(share_url)}")
        pwd_id = self.get_pwd_id(share_url)
        stoken = self.get_stoken(pwd_id)
        if not stoken:
            return
        is_owner, data_list = self.get_detail(pwd_id, stoken)
        files_count = 0
        folders_count = 0
        files_list: list[str] = []
        folders_list: list[str] = []
        files_id_list = []

        if data_list:
            total_files_count = len(data_list)
            for data in data_list:
                if data["dir"]:
                    folders_count += 1
                    folders_list.append(data["file_name"])
                else:
                    files_count += 1
                    files_list.append(data["file_name"])
                    files_id_list.append((data["fid"], data["file_name"]))

            logger.info(
                f"转存总数：{total_files_count}，文件数：{files_count}，文件夹数：{folders_count} | 支持嵌套"
            )
            logger.info(f"文件转存列表：{files_list}")
            logger.info(f"文件夹转存列表：{folders_list}")

            fid_list = [i["fid"] for i in data_list]
            share_fid_token_list = [i["share_fid_token"] for i in data_list]

            if not folder_id:
                logger.info(
                    "保存目录ID不合法，请重新获取，如果无法获取，请输入0作为文件夹ID"
                )
                return

            if is_owner == 1:
                logger.info("网盘中已经存在该文件，无需再次转存")
                return
            task_id = self.get_share_save_task_id(
                pwd_id, stoken, fid_list, share_fid_token_list, to_pdir_fid=folder_id
            )
            self.submit_task(task_id)

    def get_file_download_url(self, fid: str) -> str | None:
        """获取文件的下载直链。

        参数:
            fid: 文件 ID。

        返回:
            下载直链；获取失败时返回 `None`。
        """
        data = {"fids": [fid]}
        json_data = self.request("file/download", "post", data=data)
        data_list = json_data.get("data", None)

        if json_data["status"] != 200:
            logger.error(f"文件下载地址列表获取失败，{json_data['message']}")
            return None
        elif data_list:
            logger.info("文件下载地址列表获取成功")
        return data_list[0]["download_url"]

    def get_share_save_task_id(
        self,
        pwd_id: str,
        stoken: str,
        first_ids: list[str],
        share_fid_tokens: list[str],
        to_pdir_fid: str = "0",
    ) -> str:
        """提交分享转存请求并返回对应的异步任务 ID。

        参数:
            pwd_id: 分享链接的 pwd_id。
            stoken: 访问令牌。
            first_ids: 待转存的文件/文件夹 ID 列表。
            share_fid_tokens: 与 `first_ids` 一一对应的 share_fid_token 列表。
            to_pdir_fid: 转存到的目标目录 ID，默认为根目录。

        返回:
            转存任务的 task_id。
        """
        data = {
            "fid_list": first_ids,
            "fid_token_list": share_fid_tokens,
            "to_pdir_fid": to_pdir_fid,
            "pwd_id": pwd_id,
            "stoken": stoken,
            "pdir_fid": "0",
            "scene": "link",
        }

        response = self.request("share/sharepage/save", "post", data=data)
        json_data = response.json()
        task_id = json_data["data"]["task_id"]
        logger.info(f"获取任务ID：{task_id}")
        return task_id

    def submit_task(
        self, task_id: str, retry: int = 50
    ) -> bool | dict[str, str | dict[str, int | str]] | None:
        """轮询提交异步任务直至完成或达到重试上限。

        参数:
            task_id: 异步任务 ID。
            retry: 最大轮询次数，默认 50。

        返回:
            任务完成时返回接口的 JSON 结果；超过重试次数仍未完成则返回 `None`。
        """
        for i in range(retry):
            # 随机暂停100-50毫秒
            time.sleep(random.randint(500, 1000) / 1000)
            logger.info(f"第{i + 1}次提交任务")
            params = {"task_id": task_id, "retry_index": i}
            json_data = self.request("task", "get", headers=self.headers, params=params)

            if json_data["message"] != "ok":
                if (
                    json_data["code"] == 32003
                    and "capacity limit" in json_data["message"]
                ):
                    logger.info(
                        "转存失败，网盘容量不足！请注意当前已成功保存的个数，避免重复保存",
                    )
                elif json_data["code"] == 41013:
                    logger.info(
                        "网盘文件夹不存在，请重新运行按3切换保存目录后重试！",
                    )
                else:
                    logger.info(
                        f"错误信息：{json_data['message']}",
                    )
                continue

            if json_data["data"]["status"] != 2:
                continue

            if json_data["data"]["task_title"] == "分享-转存":
                logger.info(f"结束任务ID：{task_id}")
                to_pdir_name = (
                    json_data.get("data", {}).get("save_as", {}).get("to_pdir_name")
                )
                logger.info(f"文件保存位置：{to_pdir_name or '根目录'} 文件夹")
            return json_data
        return None

    def get_share_task_id(
        self,
        fid: str,
        file_name: str,
        url_type: int = 1,
        expired_type: int = 2,
        password: str = "",
    ) -> str:
        """创建分享任务并返回对应的异步任务 ID。

        参数:
            fid: 待分享的文件/文件夹 ID。
            file_name: 分享标题。
            url_type: 链接类型，`2` 表示带提取码。
            expired_type: 过期类型。
            password: 指定提取码；`url_type=2` 且未指定时自动生成随机提取码。

        返回:
            分享任务的 task_id。
        """
        json_data = {
            "fid_list": [fid],
            "title": file_name,
            "url_type": url_type,
            "expired_type": expired_type,
        }
        if url_type == 2:
            json_data["passcode"] = password or generate_random_code()
        json_data = self.request("share", "post", json=json_data)
        return json_data["data"]["task_id"]

    def get_share_id(self, task_id: str) -> str:
        """根据分享任务 ID 查询分享结果 ID。

        参数:
            task_id: `get_share_task_id` 返回的任务 ID。

        返回:
            分享结果的 share_id。
        """
        params = {
            "task_id": task_id,
            "retry_index": "0",
        }
        json_data = self.request("task", "get", params=params)
        return json_data["data"]["share_id"]

    def submit_share(self, share_id: str) -> str:
        """提交分享请求，获取最终可访问的分享链接。

        参数:
            share_id: `get_share_id` 返回的分享结果 ID。

        返回:
            分享链接；如设置了提取码，链接会附带 `?pwd=` 查询参数。
        """
        json_data = {
            "share_id": share_id,
        }
        json_data = self.request(
            "share/password",
            "post",
            data=json_data,
        )
        share_url = json_data["data"]["share_url"]
        if "passcode" in json_data["data"]:
            share_url = share_url + f"?pwd={json_data['data']['passcode']}"
        return share_url

    def share(
        self,
        share_url: str,
        folder_id: str | None = None,
        url_type: int = 1,
        expired_type: int = 2,
        password: str = "",
    ) -> None:
        """批量分享指定网盘文件夹页面下的二级子文件夹。

        遍历 `share_url` 对应目录下的一级、二级子文件夹并逐个创建分享链接。

        参数:
            share_url: 网盘文件夹网页地址。
            folder_id: 保留参数，当前实现未使用。
            url_type: 分享链接类型，`2` 表示带提取码。
            expired_type: 过期类型。
            password: 指定提取码，为空时按 `url_type` 自动生成。
        """
        first_dir = ""
        second_dir = ""
        try:
            logger.info(f"文件夹网页地址：{_mask_share_url(share_url)}")
            pwd_id = share_url.rsplit("/", maxsplit=1)[1].split("-")[0]

            first_page = 1
            n = 0
            error = 0
            os.makedirs("share", exist_ok=True)

            while True:
                json_data = self.get_file_list(
                    pwd_id, page=first_page, size=50, fetch_total=True
                )
                for i1 in json_data["data"]["list"]:
                    if not i1["dir"]:
                        continue

                    first_dir = i1["file_name"]
                    second_page = 1
                    while True:
                        logger.info(
                            f"正在获取{first_dir}第{first_page}页，二级目录第{second_page}页，目前共分享{n}文件"
                        )
                        json_data2 = self.get_file_list(
                            i1["fid"],
                            page=second_page,
                            size=50,
                            fetch_total=True,
                        )
                        for i2 in json_data2["data"]["list"]:
                            if not i2["dir"]:
                                continue

                            n += 1
                            share_success = False

                            fid = ""
                            share_error_msg: Exception | None = None
                            for i in range(3):
                                try:
                                    second_dir = i2["file_name"]
                                    logger.info(
                                        f"{n}.开始分享 {first_dir}/{second_dir} 文件夹"
                                    )
                                    random_time = random.choice([0.5, 1, 1.5, 2])
                                    time.sleep(random_time)
                                    fid = i2["fid"]
                                    task_id = self.get_share_task_id(
                                        fid,
                                        second_dir,
                                        url_type=url_type,
                                        expired_type=expired_type,
                                        password=password,
                                    )
                                    share_id = self.get_share_id(task_id)
                                    share_url = self.submit_share(share_id)
                                    logger.info(
                                        f"{n} | {first_dir} | {second_dir} | {_mask_share_url(share_url)}"
                                    )
                                    logger.info(
                                        f"{n}.分享成功 {first_dir}/{second_dir} 文件夹"
                                    )
                                    share_success = True
                                    break

                                except Exception as e:
                                    share_error_msg = e
                                    error += 1

                                if not share_success:
                                    logger.error(f"分享失败：{share_error_msg}")
                                    logger.error(
                                        f"{error}.{first_dir}/{second_dir} 文件夹"
                                    )
                                    logger.error(f"{n} | {first_dir} | {second_dir} | {fid}")

                        second_total = json_data2["metadata"]["_total"]
                        second_size = json_data2["metadata"]["_size"]
                        second_page = json_data2["metadata"]["_page"]
                        if second_size * second_page >= second_total:
                            break
                        second_page += 1

                second_total = json_data["metadata"]["_total"]
                second_size = json_data["metadata"]["_size"]
                second_page = json_data["metadata"]["_page"]
                if second_size * second_page >= second_total:
                    break
                first_page += 1
            logger.info(f"总共分享了 {n} 个文件夹")

        except Exception as e:
            logger.error(f"分享失败：{e}")
            logger.error(f"{first_dir}/{second_dir} 文件夹")

    def share_retry(
        self,
        retry_url: str,
        url_type: int = 1,
        expired_type: int = 2,
        password: str = "",
    ) -> None:
        """根据 `share` 失败时记录的日志行重新尝试分享。

        参数:
            retry_url: 多行文本，每行形如
                `序号 | 一级目录 | 二级目录 | 文件夹ID`。
            url_type: 分享链接类型，`2` 表示带提取码。
            expired_type: 过期类型。
            password: 指定提取码，为空时按 `url_type` 自动生成。
        """
        data_list = retry_url.split("\n")

        error = 0
        error_data = []
        for n, i1 in enumerate(data_list):
            data = i1.split(" | ")
            if data and len(data) == 4:
                first_dir = data[-3]
                second_dir = data[-2]
                fid = data[-1]
                share_success = False
                for i in range(3):
                    try:
                        task_id = self.get_share_task_id(
                            fid,
                            second_dir,
                            url_type=url_type,
                            expired_type=expired_type,
                            password=password,
                        )
                        logger.debug(f"获取到任务ID：{task_id}")
                        share_id = self.get_share_id(task_id)
                        logger.debug(f"获取到分享ID：{share_id}")
                        share_url = self.submit_share(share_id)
                        logger.info(
                            f"{n} | {first_dir} | {second_dir} | {_mask_share_url(share_url)}"
                        )
                        logger.info(f"{n}.分享成功 {first_dir}/{second_dir} 文件夹")
                        share_success = True
                        break
                    except Exception as e:
                        logger.error(f"分享失败：{e}")
                        error += 1

                if not share_success:
                    error_data.append(i1)
        error_content = "\n".join(error_data)
        logger.error(error_content)

    def search_file(
        self,
        file_name: str,
        page: int = 1,
        size: int = 50,
        sort: str = "file_type:desc,updated_at:desc",
    ) -> Any:
        """按文件名搜索网盘内文件。

        参数:
            file_name: 搜索关键字。
            page: 页码，默认 1。
            size: 每页数量，默认 50。
            sort: 排序方式。

        返回:
            接口返回的搜索结果 JSON。
        """
        logger.info("正在从网盘搜索文件")
        params = {
            "q": file_name,
            "_page": page,
            "_size": size,
            "_sort": sort,
            "_fetch_total": 1,
            "_is_hl": "1",
        }
        return self.request("file/search", params=params)

    def get_file_list(
        self,
        pdir_fid: str = "0",
        page: int = 1,
        size: int = 100,
        fetch_total: bool = False,
        sort: str = "file_type:asc,file_name:asc",
    ) -> dict[str, Any]:
        """获取指定目录下的文件列表。

        参数:
            pdir_fid: 目录 ID，默认为根目录。
            page: 页码，默认 1。
            size: 每页数量，默认 100。
            fetch_total: 是否返回总数统计。
            sort: 排序方式。

        返回:
            接口返回的文件列表 JSON。
        """
        params = {
            "pdir_fid": pdir_fid,
            "_page": page,
            "_size": size,
            "_fetch_total": fetch_total,
            "_fetch_sub_dirs": "1",
            "_sort": sort,
        }

        return self.request("file/sort", "get", params=params)

    def del_file(self, file_id: str) -> Any:
        """删除指定文件/文件夹。

        参数:
            file_id: 文件/文件夹 ID。

        返回:
            接口返回的删除结果 JSON。
        """
        logger.debug("正在删除文件")
        data = {"action_type": 2, "filelist": [file_id], "exclude_fids": []}
        return self.request("file/delete", "post", data=data)

    def store(self, url: str) -> None:
        """转存分享链接指定的单个文件并重新生成分享链接。

        参数:
            url: 待转存的分享链接。
        """
        pwd_id = get_id_from_url(url)
        stoken = self.get_stoken(pwd_id)
        detail = self.get_detail(pwd_id, stoken)[1][0]
        file_name = detail.get("title")

        first_id, share_fid_token, file_type = (
            detail.get("fid"),
            detail.get("share_fid_token"),
            detail.get("file_type"),
        )
        task = self.save_task_id(pwd_id, stoken, first_id, share_fid_token)
        data = self.task(task)
        file_id = data.get("data").get("save_as").get("save_as_top_fids")[0]
        share_task_id = self.share_task_id(file_id, file_name)
        share_id = self.task(share_task_id).get("data").get("share_id")
        share_link = self.get_share_link(share_id)
        logger.info(
            f"file_id={file_id} file_name={file_name} file_type={file_type} "
            f"share_link={_mask_share_url(share_link)}"
        )

    def save_task_id(
        self,
        pwd_id: str,
        stoken: str,
        first_id: str,
        share_fid_token: str,
        to_pdir_fid: str | int = 0,
    ) -> str:
        """提交单文件转存请求并返回异步任务 ID。

        参数:
            pwd_id: 分享链接的 pwd_id。
            stoken: 访问令牌。
            first_id: 待转存的文件 ID。
            share_fid_token: 对应的 share_fid_token。
            to_pdir_fid: 转存到的目标目录 ID，默认为根目录。

        返回:
            转存任务的 task_id。
        """
        logger.info("获取保存文件的TASKID")

        data = {
            "fid_list": [first_id],
            "fid_token_list": [share_fid_token],
            "to_pdir_fid": to_pdir_fid,
            "pwd_id": pwd_id,
            "stoken": stoken,
            "pdir_fid": "0",
            "scene": "link",
        }
        response = self.request("share/sharepage/save", "POST", data=data)
        json_data = response.json()
        task_id = json_data.get("data").get("task_id")
        logger.debug(f"获取到转存任务ID：{task_id}")
        return task_id

    def task(self, task_id: str, trice: int = 10) -> Any:
        """根据 task_id 轮询任务结果。

        参数:
            task_id: 异步任务 ID。
            trice: 最大轮询次数，默认 10。

        返回:
            任务完成时返回接口的 JSON 结果；超过轮询次数仍未完成则返回 `False`。
        """
        logger.info("根据TASKID执行任务")
        for i in range(trice):
            data = {"task_id": task_id, "retry_index": "range"}
            response = self.request("task", "get", headers=self.headers, data=data)
            status = response.get("data", {}).get("status")
            logger.debug(f"task_id={task_id} 第{i + 1}次查询，status={status}")
            if status:
                return response
        return False

    def share_task_id(self, file_id: str, file_name: str) -> str:
        """创建单文件分享任务，返回异步任务 ID。

        参数:
            file_id: 待分享的文件 ID。
            file_name: 分享标题。

        返回:
            分享任务的 task_id。
        """
        data = {
            "fid_list": [file_id],
            "title": file_name,
            "url_type": 1,
            "expired_type": 1,
        }
        response = self.request("share", "POST", data=data)
        return response.get("data").get("task_id")

    def get_share_link(self, share_id: str) -> str:
        """根据分享结果 ID 获取最终分享链接。

        参数:
            share_id: 分享结果 ID。

        返回:
            分享链接。
        """
        url = "https://drive-pc.quark.cn/1/clouddrive/share/password?pr=ucpro&fr=pc&uc_param_str="
        data = {"share_id": share_id}
        response = requests.post(url=url, json=data, headers=self.headers)
        return response.json().get("data").get("share_url")
