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

#: `get_detail` 分页的硬上限，超过视为接口异常或调用参数有误。
MAX_DETAIL_PAGES = 100

#: 单个文件夹分享的最大尝试次数。
SHARE_ATTEMPTS = 3

#: `task` 接口中代表「任务已完成」的 status 值。
TASK_STATUS_DONE = 2


class QuarkPanError(RuntimeError):
    """夸克网盘接口调用失败时抛出的领域异常。"""


#: 批量流程中可以重试/跳过的错误：网络层失败、非 JSON 响应、响应结构缺字段。
#: 故意**不含** `Exception`，这样 `TypeError`/`AttributeError` 这类编程错误会
#: 直接冒出来，而不是被重试循环当成「接口不稳定」反复吞掉。
RECOVERABLE_API_ERRORS = (
    requests.RequestException,
    QuarkPanError,
    KeyError,
    IndexError,
)


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


def safe_copy(src: str, dst: str) -> bool:
    """安全复制文件：源文件不存在则跳过，目标已存在则先删除再复制。

    参数:
        src: 源文件路径。
        dst: 目标文件路径。

    返回:
        `True` 表示复制完成；`False` 表示源文件不存在、已跳过。

    异常:
        OSError: 复制过程本身失败（权限、磁盘空间等）时原样抛出，
            调用方必须能区分「跳过」和「失败」。
    """
    if not os.path.exists(src):
        logger.warning(f"源文件不存在，跳过复制：{src}")
        return False

    if os.path.exists(dst):
        os.remove(dst)
        logger.info(f"目标文件已存在，已删除：{dst}")

    shutil.copy(src, dst)
    logger.info(f"文件已复制到：{dst}")
    return True


def generate_random_code(length: int = 4) -> str:
    """生成指定长度的随机字母数字提取码。

    参数:
        length: 提取码长度，默认为 4。

    返回:
        随机生成的提取码字符串。
    """
    characters = string.ascii_letters + string.digits
    return "".join(random.choice(characters) for _ in range(length))


def get_datetime(timestamp: float | None = None, fmt: str = "%Y-%m-%d %H:%M:%S") -> str:
    """将时间戳格式化为字符串，未传入时间戳时使用当前时间。

    参数:
        timestamp: 秒级时间戳；为 `None` 或非数值类型时使用当前时间。
        fmt: 输出的时间格式，默认 `%Y-%m-%d %H:%M:%S`。

    返回:
        格式化后的时间字符串。
    """
    if timestamp is None or not isinstance(timestamp, (int, float)):
        return datetime.now().astimezone().strftime(fmt)
    return datetime.fromtimestamp(timestamp).astimezone().strftime(fmt)


def _parse_pdir_fid(folder_url: str) -> str:
    """从夸克网盘的文件夹网页地址里解析出目录 fid。

    地址末段形如 `<fid>-<目录名>`（例如
    `https://pan.quark.cn/list#/list/all/0a1b2c3d4e5f-我的资料`），取第一个
    `-` 之前的部分；也允许直接传一个裸 fid。

    参数:
        folder_url: 文件夹网页地址，或直接就是目录 fid。

    返回:
        目录 fid。

    异常:
        QuarkPanError: 解析不出非空 fid。原实现是裸的
            `url.rsplit("/", 1)[1].split("-")[0]`，地址形状不对时会悄悄算出一个
            无意义的字符串（甚至空串）拿去当目录 ID 翻页，错得毫无提示。
    """
    tail = folder_url.split("?", 1)[0].rstrip("/").rsplit("/", maxsplit=1)[-1]
    pdir_fid = tail.split("-", 1)[0].strip()
    if not pdir_fid:
        raise QuarkPanError(
            f"无法从文件夹网页地址解析目录 fid：{folder_url!r}；"
            "期望末段形如 `<目录fid>-<目录名>`，或直接传目录 fid"
        )
    return pdir_fid


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

        异常:
            QuarkPanError: 链接里没有 `/s/` 段或 pwd_id 为空。原实现直接
                `split("/s/")[1]`，非法链接会抛一句没有上下文的 `IndexError`，
                调用方看不出是链接写错了。
        """
        head = share_url.split("?", 1)[0]
        parts = head.split("/s/", 1)
        pwd_id = parts[1].strip("/") if len(parts) == 2 else ""
        if not pwd_id:
            raise QuarkPanError(f"无法从链接中解析 pwd_id：{head}")
        return pwd_id

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

        异常:
            requests.RequestException: 网络层失败（连接、超时等）。
            QuarkPanError: 响应体不是合法 JSON（典型为网关错误页），
                异常信息中带上接口路径、HTTP 状态码与响应片段。
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
        response = requests.request(
            method,
            url,
            *args,
            params=params,
            headers=headers or self.headers,
            json=data,
            timeout=timeout,
            **kwargs,
        )
        return self._parse_json(response, uri)

    @staticmethod
    def _parse_json(response: requests.Response, uri: str) -> Any:
        """解析响应 JSON，失败时抛出带上下文的 `QuarkPanError`。

        夸克的业务错误是以 HTTP 200 + JSON 里的 `status`/`code` 返回的，
        因此这里不做 `raise_for_status`，只在响应根本不是 JSON（网关错误页、
        限流拦截页）时转换成领域异常，避免调用方只拿到一句
        `Expecting value: line 1 column 1`。

        参数:
            response: `requests` 的响应对象。
            uri: 发起请求的接口路径，用于异常上下文。

        返回:
            响应体的 JSON 反序列化结果。

        异常:
            QuarkPanError: 响应体不是合法 JSON。
        """
        try:
            return response.json()
        except ValueError as e:
            snippet = (response.text or "")[:200]
            raise QuarkPanError(
                f"接口 {uri} 返回了非 JSON 响应（HTTP {response.status_code}）：{snippet!r}"
            ) from e

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

        异常:
            QuarkPanError: 翻到 `MAX_DETAIL_PAGES` 页仍未取完，说明接口分页
                元数据异常。此时宁可报错，也不能静默返回截断的列表或 `None`。
        """
        file_list: list[dict[str, int | str]] = []
        for page in range(1, MAX_DETAIL_PAGES):
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
                        "include_items": file.get("include_items", ""),
                        "share_fid_token": file["share_fid_token"],
                        "status": file["status"],
                    }
                )
            if _total <= _size or _count < _size:
                return is_owner, file_list

        raise QuarkPanError(
            f"分享详情分页超过上限 {MAX_DETAIL_PAGES - 1} 页仍未取完"
            f"（pwd_id={pwd_id} pdir_fid={pdir_fid} 已取 {len(file_list)} 条），"
            "疑似接口分页元数据异常"
        )

    def get_user_info(self, timeout: int = 10) -> Any:
        """获取当前登录账号的用户信息。

        参数:
            timeout: 请求超时时间（秒）。

        返回:
            接口返回的用户信息 JSON。
        """
        params = {
            "fr": "pc",
            "platform": "pc",
        }
        response = requests.get(
            "https://pan.quark.cn/account/info",
            params=params,
            headers=self.headers,
            timeout=timeout,
        )
        return self._parse_json(response, "account/info")

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
    ) -> bool:
        """将他人分享的文件/文件夹转存到自己网盘的指定目录。

        参数:
            share_url: 待转存的分享链接。
            folder_id: 目标目录 ID；为空时会跳过转存并提示重新获取。

        返回:
            `True` 表示转存任务已成功完成；`False` 表示被跳过（stoken 获取失败、
            分享内容为空、未指定目标目录、网盘中已存在该内容）。调用方据此判断
            是否真的转存过，不要只看有没有抛异常。

        异常:
            QuarkPanError: 转存任务提交后未在轮询次数内完成。
        """
        logger.info(f"文件分享链接：{_mask_share_url(share_url)}")
        pwd_id = self.get_pwd_id(share_url)
        stoken = self.get_stoken(pwd_id)
        if not stoken:
            return False
        is_owner, data_list = self.get_detail(pwd_id, stoken)
        files_count = 0
        folders_count = 0
        files_list: list[str] = []
        folders_list: list[str] = []
        files_id_list = []

        if not data_list:
            logger.info("分享内容为空，无需转存")
            return False

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
            return False

        if is_owner == 1:
            logger.info("网盘中已经存在该文件，无需再次转存")
            return False
        task_id = self.get_share_save_task_id(
            pwd_id, stoken, fid_list, share_fid_token_list, to_pdir_fid=folder_id
        )
        self.submit_task(task_id)
        return True

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
        if data_list:
            logger.info("文件下载地址列表获取成功")
        return data_list[0]["download_url"] if data_list else None

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

        json_data = self.request("share/sharepage/save", "post", data=data)
        task_id = json_data["data"]["task_id"]
        logger.info(f"获取任务ID：{task_id}")
        return task_id

    def submit_task(self, task_id: str, retry: int = 50) -> dict[str, Any]:
        """轮询提交异步任务直至完成或达到重试上限。

        参数:
            task_id: 异步任务 ID。
            retry: 最大轮询次数，默认 50。

        返回:
            任务完成时接口返回的 JSON。

        异常:
            QuarkPanError: 超过重试次数任务仍未完成。此前返回 `None` 会让
                `save_shared` 这类调用方把「转存失败」当成「转存成功」。
        """
        last_message = ""
        for i in range(retry):
            # 随机暂停100-50毫秒
            time.sleep(random.randint(500, 1000) / 1000)
            logger.info(f"第{i + 1}次提交任务")
            params = {"task_id": task_id, "retry_index": i}
            json_data = self.request("task", "get", headers=self.headers, params=params)

            if json_data["message"] != "ok":
                last_message = json_data["message"]
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

            if json_data["data"]["status"] != TASK_STATUS_DONE:
                last_message = f"status={json_data['data']['status']}"
                continue

            if json_data["data"]["task_title"] == "分享-转存":
                logger.info(f"结束任务ID：{task_id}")
                to_pdir_name = (
                    json_data.get("data", {}).get("save_as", {}).get("to_pdir_name")
                )
                logger.info(f"文件保存位置：{to_pdir_name or '根目录'} 文件夹")
            return json_data
        raise QuarkPanError(
            f"任务 task_id={task_id} 轮询 {retry} 次后仍未完成"
            f"（最后一次：{last_message or '无'}）"
        )

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
        response = self.request("share", "post", data=json_data)
        return response["data"]["task_id"]

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

    def _share_folder_with_retry(
        self,
        fid: str,
        title: str,
        url_type: int,
        expired_type: int,
        password: str,
        attempts: int = SHARE_ATTEMPTS,
    ) -> str:
        """为单个文件夹创建分享链接，可恢复错误按次数重试。

        参数:
            fid: 待分享文件夹 ID。
            title: 分享标题。
            url_type: 分享链接类型，`2` 表示带提取码。
            expired_type: 过期类型。
            password: 指定提取码，为空时按 `url_type` 自动生成。
            attempts: 最大尝试次数。

        返回:
            分享链接。

        异常:
            QuarkPanError: 重试耗尽仍未成功，异常链上保留最后一次原始错误。
                注意只有网络/响应格式这类可恢复错误才会重试，编程错误
                （`TypeError`/`AttributeError` 等）直接向上抛出，不被重试掩盖。
        """
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                time.sleep(random.choice([0.5, 1, 1.5, 2]))
                task_id = self.get_share_task_id(
                    fid,
                    title,
                    url_type=url_type,
                    expired_type=expired_type,
                    password=password,
                )
                logger.debug(f"获取到任务ID：{task_id}")
                share_id = self.get_share_id(task_id)
                logger.debug(f"获取到分享ID：{share_id}")
                return self.submit_share(share_id)
            except RECOVERABLE_API_ERRORS as e:
                last_error = e
                logger.warning(
                    f"第 {attempt}/{attempts} 次分享「{title}」（fid={fid}）失败：{e}"
                )
        raise QuarkPanError(
            f"分享「{title}」（fid={fid}）失败，已重试 {attempts} 次：{last_error}"
        ) from last_error

    def share(
        self,
        share_url: str,
        folder_id: str | None = None,
        url_type: int = 1,
        expired_type: int = 2,
        password: str = "",
    ) -> list[str]:
        """批量分享指定网盘文件夹页面下的二级子文件夹。

        遍历 `share_url` 对应目录下的一级、二级子文件夹并逐个创建分享链接。

        参数:
            share_url: 网盘文件夹网页地址，末段形如 `<目录fid>-<目录名>`
                （例如 `https://pan.quark.cn/list#/list/all/<fid>-<名称>`）。
                也可以直接传目录 fid。
            folder_id: 直接指定要遍历的目录 fid；给了就优先用它，
                不再从 `share_url` 解析。
            url_type: 分享链接类型，`2` 表示带提取码。
            expired_type: 过期类型。
            password: 指定提取码，为空时按 `url_type` 自动生成。

        返回:
            失败条目列表，每项形如 `序号 | 一级目录 | 二级目录 | 文件夹ID`，
            可直接作为 `share_retry` 的输入；全部成功时返回空列表。

        异常:
            QuarkPanError: 目录 fid 解析不出来，或目录列表获取失败等无法继续的
                错误，异常信息中带上当前所在目录；不再静默吞掉让调用方以为已
                全部分享成功。
        """
        logger.info(f"文件夹网页地址：{_mask_share_url(share_url)}")
        # `folder_id` 原先声明了却从未使用；这里让它真正生效，没给才回落到解析 URL。
        pdir_fid = folder_id or _parse_pdir_fid(share_url)

        first_dir = ""
        second_dir = ""
        first_page = 1
        n = 0
        failures: list[str] = []
        try:
            while True:
                json_data = self.get_file_list(
                    pdir_fid, page=first_page, size=50, fetch_total=True
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
                            second_dir = i2["file_name"]
                            fid = i2["fid"]
                            record = f"{n} | {first_dir} | {second_dir} | {fid}"
                            logger.info(f"{n}.开始分享 {first_dir}/{second_dir} 文件夹")
                            try:
                                share_link = self._share_folder_with_retry(
                                    fid,
                                    second_dir,
                                    url_type=url_type,
                                    expired_type=expired_type,
                                    password=password,
                                )
                            except QuarkPanError as e:
                                logger.error(f"分享失败：{e}")
                                logger.error(record)
                                failures.append(record)
                                continue
                            logger.info(
                                f"{n} | {first_dir} | {second_dir} | {_mask_share_url(share_link)}"
                            )
                            logger.info(f"{n}.分享成功 {first_dir}/{second_dir} 文件夹")

                        second_total = json_data2["metadata"]["_total"]
                        second_size = json_data2["metadata"]["_size"]
                        second_page = json_data2["metadata"]["_page"]
                        if second_size * second_page >= second_total:
                            break
                        second_page += 1

                total = json_data["metadata"]["_total"]
                size = json_data["metadata"]["_size"]
                page = json_data["metadata"]["_page"]
                if size * page >= total:
                    break
                first_page += 1
        except RECOVERABLE_API_ERRORS as e:
            raise QuarkPanError(
                f"批量分享中断（pdir_fid={pdir_fid} 当前目录={first_dir}/{second_dir} "
                f"已分享 {n} 个，失败 {len(failures)} 个）：{e}"
            ) from e

        logger.info(f"总共分享了 {n} 个文件夹，失败 {len(failures)} 个")
        return failures

    def share_retry(
        self,
        retry_url: str,
        url_type: int = 1,
        expired_type: int = 2,
        password: str = "",
    ) -> list[str]:
        """根据 `share` 失败时记录的日志行重新尝试分享。

        参数:
            retry_url: 多行文本，每行形如
                `序号 | 一级目录 | 二级目录 | 文件夹ID`。
            url_type: 分享链接类型，`2` 表示带提取码。
            expired_type: 过期类型。
            password: 指定提取码，为空时按 `url_type` 自动生成。

        返回:
            重试后仍然失败的原始行列表；全部成功时返回空列表。
        """
        error_data: list[str] = []
        for n, line in enumerate(retry_url.split("\n")):
            data = line.split(" | ")
            if len(data) != 4:
                continue

            first_dir = data[-3]
            second_dir = data[-2]
            fid = data[-1]
            try:
                share_link = self._share_folder_with_retry(
                    fid,
                    second_dir,
                    url_type=url_type,
                    expired_type=expired_type,
                    password=password,
                )
            except QuarkPanError as e:
                logger.error(f"分享失败：{e}")
                error_data.append(line)
                continue
            logger.info(
                f"{n} | {first_dir} | {second_dir} | {_mask_share_url(share_link)}"
            )
            logger.info(f"{n}.分享成功 {first_dir}/{second_dir} 文件夹")

        if error_data:
            logger.error("以下条目重试后仍然失败：\n" + "\n".join(error_data))
        return error_data

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
            # 夸克接口的布尔开关一律用 "1"/"0"（同本文件 `_fetch_sub_dirs`、
            # `_is_hl`、`force`）。直接把 Python 的 bool 交给 requests 会被编码成
            # `_fetch_total=True`，服务端不识别，`metadata._total` 可能缺失，
            # 依赖它翻页的 `share()` 就只会处理第一页。
            "pdir_fid": pdir_fid,
            "_page": page,
            "_size": size,
            "_fetch_total": "1" if fetch_total else "0",
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

    def store(self, url: str) -> str:
        """转存分享链接指定的单个文件并重新生成分享链接。

        参数:
            url: 待转存的分享链接。

        返回:
            转存后新生成的分享链接。

        异常:
            QuarkPanError: 分享链接无效、stoken 获取失败、分享内容为空或
                异步任务未在轮询次数内完成。
        """
        pwd_id = get_id_from_url(url)
        if not pwd_id:
            raise QuarkPanError(f"无法从链接中解析 pwd_id：{_mask_share_url(url)}")
        stoken = self.get_stoken(pwd_id)
        if not stoken:
            raise QuarkPanError(f"获取 stoken 失败（pwd_id={pwd_id}）")

        detail_list = self.get_detail(pwd_id, stoken)[1]
        if not detail_list:
            raise QuarkPanError(f"分享内容为空（pwd_id={pwd_id}）")
        detail = detail_list[0]
        # `get_detail` 组装的条目只有 file_name，没有 title；原先的
        # `detail.get("title") or ...` 永远走不到左分支，是死代码。
        file_name = detail.get("file_name", "")

        first_id, share_fid_token, file_type = (
            detail.get("fid"),
            detail.get("share_fid_token"),
            detail.get("file_type"),
        )
        task = self.save_task_id(pwd_id, stoken, first_id, share_fid_token)
        data = self.task(task)
        file_id = data["data"]["save_as"]["save_as_top_fids"][0]
        share_task_id = self.share_task_id(file_id, file_name)
        share_id = self.task(share_task_id)["data"]["share_id"]
        share_link = self.get_share_link(share_id)
        logger.info(
            f"file_id={file_id} file_name={file_name} file_type={file_type} "
            f"share_link={_mask_share_url(share_link)}"
        )
        return share_link

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
        json_data = self.request("share/sharepage/save", "POST", data=data)
        task_id = json_data.get("data").get("task_id")
        logger.debug(f"获取到转存任务ID：{task_id}")
        return task_id

    def task(self, task_id: str, trice: int = 10) -> dict[str, Any]:
        """根据 task_id 轮询任务结果，直到任务完成。

        参数:
            task_id: 异步任务 ID。
            trice: 最大轮询次数，默认 10。

        返回:
            任务完成（`data.status == 2`）时接口返回的 JSON。

        异常:
            QuarkPanError: 轮询次数耗尽任务仍未完成。调用方（如 `store`）需要
                `data.save_as` 才能继续，返回假值只会让后续取字段时炸在别处。
        """
        logger.info("根据TASKID执行任务")
        status = None
        for i in range(trice):
            params = {"task_id": task_id, "retry_index": i}
            response = self.request("task", "get", headers=self.headers, params=params)
            status = response.get("data", {}).get("status")
            logger.debug(f"task_id={task_id} 第{i + 1}次查询，status={status}")
            if status == TASK_STATUS_DONE:
                return response
            time.sleep(random.randint(500, 1000) / 1000)
        raise QuarkPanError(
            f"任务 task_id={task_id} 轮询 {trice} 次后仍未完成（最后 status={status}）"
        )

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
        response = self.request("share/password", "post", data={"share_id": share_id})
        return response["data"]["share_url"]
