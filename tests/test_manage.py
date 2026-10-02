"""针对 `fundrives.quark.manage` 公开 API 的正常路径与边界测试。

网络请求均通过 mock 隔离，不会发起真实请求。
"""

from __future__ import annotations

from unittest import mock

import pytest
import requests

from fundrives.quark.manage import (
    MAX_DETAIL_PAGES,
    QuarkPanError,
    QuarkPanManage,
    _mask_share_url,
    generate_random_code,
    get_datetime,
    get_id_from_url,
    safe_copy,
)


@pytest.fixture(autouse=True)
def _no_sleep():
    """重试/轮询里的 `time.sleep` 在测试中直接跳过。"""
    with mock.patch("fundrives.quark.manage.time.sleep"):
        yield


def test_get_id_from_url_matches_pwd_id():
    """能从标准分享链接中提取 pwd_id。"""
    assert get_id_from_url("https://pan.quark.cn/s/abcd1234") == "abcd1234"


def test_get_id_from_url_returns_empty_when_no_match():
    """链接不含 `/s/` 片段时返回空字符串，而不是抛异常。"""
    assert get_id_from_url("https://pan.quark.cn/no-match") == ""


def test_generate_random_code_length():
    """生成的提取码长度符合传入参数。"""
    code = generate_random_code(6)
    assert len(code) == 6
    assert code.isalnum()


def test_generate_random_code_default_length():
    assert len(generate_random_code()) == 4


def test_get_datetime_with_timestamp():
    """传入时间戳时按时间戳格式化，而非使用当前时间。"""
    assert get_datetime(0, fmt="%Y-%m-%d") == "1970-01-01"


def test_get_datetime_without_timestamp_uses_now():
    """未传入合法时间戳时退化为当前时间，格式仍然正确。"""
    from datetime import datetime

    result = get_datetime(None, fmt="%Y-%m-%d")
    assert result == datetime.today().strftime("%Y-%m-%d")  # noqa: DTZ002


def test_mask_share_url_strips_passcode():
    """日志脱敏辅助函数应去掉查询参数（如提取码）。"""
    assert (
        _mask_share_url("https://pan.quark.cn/s/xxx?pwd=abcd")
        == "https://pan.quark.cn/s/xxx"
    )


def test_mask_share_url_without_query_is_unchanged():
    assert _mask_share_url("https://pan.quark.cn/s/xxx") == "https://pan.quark.cn/s/xxx"


def test_get_pwd_id_static():
    assert QuarkPanManage.get_pwd_id("https://pan.quark.cn/s/xxx?extra=1") == "xxx"


def test_extract_urls_returns_first_match():
    text = "分享地址 https://pan.quark.cn/s/xxx 请查收"
    assert QuarkPanManage.extract_urls(text) == "https://pan.quark.cn/s/xxx"


def _make_manage() -> QuarkPanManage:
    return QuarkPanManage(cookies="fake-cookie")


def test_init_sets_cookie_header():
    """初始化时 cookie 应写入请求头。"""
    drive = _make_manage()
    assert drive.headers["cookie"] == "fake-cookie"
    assert drive.base_url == "https://drive-pc.quark.cn/1/clouddrive"


def test_get_stoken_success():
    """转存 stoken 请求成功时返回 stoken 字符串。"""
    drive = _make_manage()
    with mock.patch.object(
        drive,
        "request",
        return_value={"status": 200, "data": {"stoken": "tok-123"}},
    ):
        assert drive.get_stoken("pwd") == "tok-123"


def test_get_stoken_failure_returns_empty_string():
    """接口返回失败状态时不应抛异常，应返回空字符串。"""
    drive = _make_manage()
    with mock.patch.object(
        drive,
        "request",
        return_value={"status": 400, "data": None, "message": "invalid pwd_id"},
    ):
        assert drive.get_stoken("pwd") == ""


def test_get_detail_paginates_until_total_reached():
    """`get_detail` 应在拿完所有数据后返回，而不是死循环到 100 页。"""
    drive = _make_manage()
    page_response = {
        "data": {
            "is_owner": 0,
            "list": [
                {
                    "fid": "f1",
                    "file_name": "a.txt",
                    "file_type": 1,
                    "dir": False,
                    "pdir_fid": "0",
                    "share_fid_token": "tok",
                    "status": 1,
                }
            ],
        },
        "metadata": {"_total": 1, "_size": 50, "_count": 1},
    }
    with mock.patch.object(drive, "request", return_value=page_response):
        is_owner, file_list = drive.get_detail("pwd", "tok")

    assert is_owner == 0
    assert len(file_list) == 1
    assert file_list[0]["file_name"] == "a.txt"


def test_get_detail_empty_result():
    """`_total` 为 0 时应直接返回空列表。"""
    drive = _make_manage()
    empty_response = {
        "data": {"is_owner": 0, "list": []},
        "metadata": {"_total": 0, "_size": 50, "_count": 0},
    }
    with mock.patch.object(drive, "request", return_value=empty_response):
        _is_owner, file_list = drive.get_detail("pwd", "tok")

    assert file_list == []


def test_save_shared_returns_early_without_stoken():
    """获取 stoken 失败时应直接返回，不再继续转存流程。"""
    drive = _make_manage()
    with (
        mock.patch.object(drive, "get_stoken", return_value=""),
        mock.patch.object(drive, "get_detail") as mocked_get_detail,
    ):
        drive.save_shared("https://pan.quark.cn/s/xxx", folder_id="0")

    mocked_get_detail.assert_not_called()


def test_save_shared_skips_when_already_owned():
    """网盘中已存在该分享内容（is_owner=1）时应跳过转存，不发起保存任务。"""
    drive = _make_manage()
    data_list = [
        {
            "fid": "f1",
            "file_name": "a.txt",
            "dir": False,
            "share_fid_token": "tok",
        }
    ]
    with (
        mock.patch.object(drive, "get_stoken", return_value="tok"),
        mock.patch.object(drive, "get_detail", return_value=(1, data_list)),
        mock.patch.object(drive, "get_share_save_task_id") as mocked_task,
    ):
        drive.save_shared("https://pan.quark.cn/s/xxx", folder_id="0")

    mocked_task.assert_not_called()


def test_get_file_download_url_success():
    drive = _make_manage()
    resp = {"status": 200, "data": [{"download_url": "https://example.com/f"}]}
    with mock.patch.object(drive, "request", return_value=resp):
        assert drive.get_file_download_url("fid") == "https://example.com/f"


def test_get_file_download_url_failure_returns_none():
    drive = _make_manage()
    resp = {"status": 400, "data": None, "message": "not found"}
    with mock.patch.object(drive, "request", return_value=resp):
        assert drive.get_file_download_url("fid") is None


def test_get_file_list_builds_expected_params():
    drive = _make_manage()
    with mock.patch.object(drive, "request", return_value={"data": {"list": []}}) as m:
        drive.get_file_list(pdir_fid="0", page=2, size=10)

    args, kwargs = m.call_args
    assert args[0] == "file/sort"
    assert kwargs["params"]["pdir_fid"] == "0"
    assert kwargs["params"]["_page"] == 2
    assert kwargs["params"]["_size"] == 10


def test_get_user_info_uses_authenticated_request():
    drive = _make_manage()
    response = mock.Mock()
    response.json.return_value = {"data": {"nickname": "tester"}}
    with mock.patch(
        "fundrives.quark.manage.requests.get", return_value=response
    ) as get:
        assert drive.get_user_info() == {"data": {"nickname": "tester"}}
    get.assert_called_once()


def test_create_dir_sends_parent_and_name():
    drive = _make_manage()
    with mock.patch.object(drive, "request", return_value={"status": 200}) as request:
        assert drive.create_dir("docs", "parent") == {"status": 200}
    request.assert_called_once_with(
        "file",
        "post",
        data={
            "pdir_fid": "parent",
            "file_name": "docs",
            "dir_path": "",
            "dir_init_lock": False,
        },
    )


def test_search_file_builds_search_params():
    drive = _make_manage()
    with mock.patch.object(drive, "request", return_value={"data": []}) as request:
        drive.search_file("report", page=2, size=10)
    assert request.call_args.args[0] == "file/search"
    assert request.call_args.kwargs["params"] == {
        "q": "report",
        "_page": 2,
        "_size": 10,
        "_sort": "file_type:desc,updated_at:desc",
        "_fetch_total": 1,
        "_is_hl": "1",
    }


def test_del_file_sends_file_id():
    drive = _make_manage()
    with mock.patch.object(drive, "request", return_value={"status": 200}) as request:
        assert drive.del_file("fid") == {"status": 200}
    request.assert_called_once_with(
        "file/delete",
        "post",
        data={"action_type": 2, "filelist": ["fid"], "exclude_fids": []},
    )


def test_get_share_save_task_id_accepts_json_request_result():
    drive = _make_manage()
    with mock.patch.object(
        drive, "request", return_value={"data": {"task_id": "task-1"}}
    ) as request:
        assert (
            drive.get_share_save_task_id("pwd", "token", ["fid"], ["share-token"])
            == "task-1"
        )
    assert request.call_args.args[0] == "share/sharepage/save"


def test_save_task_id_accepts_json_request_result():
    drive = _make_manage()
    with mock.patch.object(
        drive, "request", return_value={"data": {"task_id": "task-1"}}
    ):
        assert drive.save_task_id("pwd", "token", "fid", "share-token") == "task-1"


def test_task_returns_completed_response_and_stops_polling():
    drive = _make_manage()
    result = {"data": {"status": 2}}
    with mock.patch.object(drive, "request", return_value=result) as request:
        assert drive.task("task-1") == result
    request.assert_called_once()


def test_task_raises_after_retry_limit():
    """轮询耗尽必须抛领域异常：返回假值会让 `store` 在后续取字段时炸在别处。"""
    drive = _make_manage()
    with (
        mock.patch.object(
            drive, "request", return_value={"data": {"status": 0}}
        ) as request,
        pytest.raises(QuarkPanError, match="轮询 2 次"),
    ):
        drive.task("task-1", trice=2)
    assert request.call_count == 2


def test_task_keeps_polling_until_status_done():
    """任务未完成（status != 2）时必须继续轮询，不能把中间态当成完成。"""
    drive = _make_manage()
    done = {"data": {"status": 2, "save_as": {"save_as_top_fids": ["new-fid"]}}}
    with mock.patch.object(
        drive,
        "request",
        side_effect=[{"data": {"status": 1}}, done],
    ) as request:
        assert drive.task("task-1", trice=5) is done
    assert request.call_count == 2


def test_task_sends_task_id_as_query_params():
    """`task` 是 GET 接口，task_id 必须走 query params，放进 JSON body 服务端收不到。"""
    drive = _make_manage()
    with mock.patch.object(
        drive, "request", return_value={"data": {"status": 2}}
    ) as request:
        drive.task("task-1")
    kwargs = request.call_args.kwargs
    assert kwargs["params"]["task_id"] == "task-1"
    assert "data" not in kwargs


def test_share_task_id_and_result_helpers():
    drive = _make_manage()
    with mock.patch.object(
        drive,
        "request",
        side_effect=[
            {"data": {"task_id": "task-1"}},
            {"data": {"share_id": "share-1"}},
            {"data": {"share_url": "https://pan.quark.cn/s/abc"}},
        ],
    ):
        task_id = drive.share_task_id("fid", "name")
        assert task_id == "task-1"
        assert drive.get_share_id(task_id) == "share-1"
        assert drive.get_share_link("share-1") == "https://pan.quark.cn/s/abc"


def test_get_share_link_goes_through_request_wrapper():
    """`get_share_link` 必须复用带 timeout 的 `request`，不能裸调 requests.post。"""
    drive = _make_manage()
    with (
        mock.patch.object(
            drive, "request", return_value={"data": {"share_url": "https://x/s/a"}}
        ) as request,
        mock.patch("fundrives.quark.manage.requests.post") as post,
    ):
        assert drive.get_share_link("share-1") == "https://x/s/a"
    assert request.call_args.args[0] == "share/password"
    post.assert_not_called()


# --- 回归：get_share_task_id 曾把 payload 传成 request(json=...) ----------------


def test_get_share_task_id_builds_share_payload():
    """`get_share_task_id` 必须能正常走通，并把分享参数放进请求体。

    历史缺陷：这里写成 `self.request("share", "post", json=json_data)`，而
    `request()` 内部已经用了 `json=data`，于是 `requests.request()` 收到两个
    `json` 关键字参数，**每次调用必定 TypeError**，`share`/`share_retry`
    这两个对外能力 100% 不可用。
    """
    drive = _make_manage()
    response = mock.Mock()
    response.json.return_value = {"data": {"task_id": "task-9"}}
    with mock.patch(
        "fundrives.quark.manage.requests.request", return_value=response
    ) as request:
        assert drive.get_share_task_id("fid-1", "标题", url_type=2) == "task-9"

    payload = request.call_args.kwargs["json"]
    assert payload["fid_list"] == ["fid-1"]
    assert payload["title"] == "标题"
    assert payload["url_type"] == 2
    assert len(payload["passcode"]) == 4


def test_share_retry_collects_failures_and_returns_them():
    """单条分享重试耗尽时应被收集进返回值，而不是只写日志。"""
    drive = _make_manage()
    with mock.patch.object(
        drive,
        "get_share_task_id",
        side_effect=requests.ConnectionError("网络不通"),
    ) as task_id:
        failed = drive.share_retry("1 | 一级 | 二级 | fid-1")
    assert failed == ["1 | 一级 | 二级 | fid-1"]
    assert task_id.call_count == 3


def test_share_retry_succeeds_and_returns_empty_list():
    drive = _make_manage()
    with (
        mock.patch.object(drive, "get_share_task_id", return_value="t1"),
        mock.patch.object(drive, "get_share_id", return_value="s1"),
        mock.patch.object(drive, "submit_share", return_value="https://x/s/a?pwd=ab12"),
    ):
        assert drive.share_retry("1 | 一级 | 二级 | fid-1") == []


def test_share_retry_does_not_swallow_programming_errors():
    """`TypeError` 这类编程错误必须冒出来。

    此前整个重试循环是 `except Exception`，把上面那个 `json=` 传参 bug 变成
    一行「分享失败」日志，调用方和审计都看不出功能已经全挂。
    """
    drive = _make_manage()
    with (
        mock.patch.object(
            drive, "get_share_task_id", side_effect=TypeError("got multiple values")
        ),
        pytest.raises(TypeError),
    ):
        drive.share_retry("1 | 一级 | 二级 | fid-1")


def test_share_returns_failed_records_for_retry():
    """`share` 把失败条目作为返回值交给调用方，格式可直接喂给 `share_retry`。"""
    drive = _make_manage()
    first_level = {
        "data": {"list": [{"dir": True, "file_name": "一级", "fid": "d1"}]},
        "metadata": {"_total": 1, "_size": 50, "_page": 1},
    }
    second_level = {
        "data": {"list": [{"dir": True, "file_name": "二级", "fid": "d2"}]},
        "metadata": {"_total": 1, "_size": 50, "_page": 1},
    }
    with (
        mock.patch.object(
            drive, "get_file_list", side_effect=[first_level, second_level]
        ),
        mock.patch.object(
            drive, "get_share_task_id", side_effect=requests.ConnectionError("x")
        ),
    ):
        failed = drive.share("https://pan.quark.cn/list/#/all-0")
    assert failed == ["1 | 一级 | 二级 | d2"]


def test_share_raises_with_context_when_listing_fails():
    """目录列表拿不到时抛带上下文的领域异常，而不是静默返回让调用方以为成功。"""
    drive = _make_manage()
    with (
        mock.patch.object(
            drive, "get_file_list", side_effect=requests.ConnectionError("断网")
        ),
        pytest.raises(QuarkPanError, match="批量分享中断"),
    ):
        drive.share("https://pan.quark.cn/list/#/all-0")


# --- get_detail 分页耗尽 --------------------------------------------------------


def test_get_detail_raises_when_page_cap_exhausted():
    """翻到分页上限仍未取完时必须抛领域异常。

    历史缺陷：`for page in range(1, 100)` 跑完后函数直接落到末尾，隐式返回
    `None`，与 `-> tuple[...]` 标注不符；`save_shared` 的
    `is_owner, data_list = self.get_detail(...)` 会在解包时报
    `cannot unpack non-sequence NoneType`，看不出根因。
    """
    drive = _make_manage()
    never_ending = {
        "data": {
            "is_owner": 0,
            "list": [
                {
                    "fid": "f",
                    "file_name": "a",
                    "file_type": 1,
                    "dir": False,
                    "pdir_fid": "0",
                    "share_fid_token": "t",
                    "status": 1,
                }
            ],
        },
        # _total 远大于 _size，且 _count == _size，永远满足「还有下一页」
        "metadata": {"_total": 10**6, "_size": 1, "_count": 1},
    }
    with (
        mock.patch.object(drive, "request", return_value=never_ending) as request,
        pytest.raises(QuarkPanError, match="分页超过上限"),
    ):
        drive.get_detail("pwd-x", "tok")
    assert request.call_count == MAX_DETAIL_PAGES - 1


# --- safe_copy 不再吞掉 OSError -------------------------------------------------


def test_safe_copy_returns_false_when_source_missing(tmp_path):
    assert safe_copy(str(tmp_path / "missing.txt"), str(tmp_path / "dst.txt")) is False


def test_safe_copy_returns_true_on_success(tmp_path):
    src = tmp_path / "a.txt"
    src.write_text("x")
    dst = tmp_path / "b.txt"
    assert safe_copy(str(src), str(dst)) is True
    assert dst.read_text() == "x"


def test_safe_copy_propagates_oserror(tmp_path):
    """复制本身失败必须抛出来，而不是记一条 error 日志后装作成功。"""
    src = tmp_path / "a.txt"
    src.write_text("x")
    with (
        mock.patch("fundrives.quark.manage.shutil.copy", side_effect=OSError("磁盘满")),
        pytest.raises(OSError, match="磁盘满"),
    ):
        safe_copy(str(src), str(tmp_path / "b.txt"))


# --- 超时与非 JSON 响应 ---------------------------------------------------------


def test_get_user_info_sets_timeout():
    """裸 `requests.get` 必须带 timeout，否则服务端不响应会无限挂起。"""
    drive = _make_manage()
    response = mock.Mock()
    response.json.return_value = {"data": {}}
    with mock.patch(
        "fundrives.quark.manage.requests.get", return_value=response
    ) as get:
        drive.get_user_info()
    assert get.call_args.kwargs["timeout"] == 10


def test_request_sets_timeout_by_default():
    drive = _make_manage()
    response = mock.Mock()
    response.json.return_value = {}
    with mock.patch(
        "fundrives.quark.manage.requests.request", return_value=response
    ) as request:
        drive.request("file/sort")
    assert request.call_args.kwargs["timeout"] == 10


def test_request_raises_domain_error_on_non_json_response():
    """网关错误页不是 JSON，要转成带状态码和响应片段的领域异常。"""
    drive = _make_manage()
    response = mock.Mock()
    response.json.side_effect = ValueError("Expecting value")
    response.status_code = 502
    response.text = "<html>502 Bad Gateway</html>"
    with (
        mock.patch("fundrives.quark.manage.requests.request", return_value=response),
        pytest.raises(QuarkPanError) as excinfo,
    ):
        drive.request("file/sort")
    message = str(excinfo.value)
    assert "file/sort" in message
    assert "502" in message
    assert "Bad Gateway" in message


# --- 返回值语义：调用方能区分成功与跳过 ----------------------------------------


def test_save_shared_returns_true_when_task_submitted():
    drive = _make_manage()
    data_list = [
        {"fid": "f1", "file_name": "a.txt", "dir": False, "share_fid_token": "t"}
    ]
    with (
        mock.patch.object(drive, "get_stoken", return_value="tok"),
        mock.patch.object(drive, "get_detail", return_value=(0, data_list)),
        mock.patch.object(drive, "get_share_save_task_id", return_value="task-1"),
        mock.patch.object(drive, "submit_task", return_value={"data": {"status": 2}}),
    ):
        assert drive.save_shared("https://pan.quark.cn/s/xxx", folder_id="0") is True


def test_save_shared_returns_false_when_skipped():
    drive = _make_manage()
    with mock.patch.object(drive, "get_stoken", return_value=""):
        assert drive.save_shared("https://pan.quark.cn/s/xxx", folder_id="0") is False


def test_save_shared_returns_false_without_folder_id():
    drive = _make_manage()
    data_list = [
        {"fid": "f1", "file_name": "a.txt", "dir": False, "share_fid_token": "t"}
    ]
    with (
        mock.patch.object(drive, "get_stoken", return_value="tok"),
        mock.patch.object(drive, "get_detail", return_value=(0, data_list)),
        mock.patch.object(drive, "get_share_save_task_id") as task,
    ):
        assert drive.save_shared("https://pan.quark.cn/s/xxx", folder_id=None) is False
    task.assert_not_called()


def test_submit_task_raises_after_retry_limit():
    """轮询耗尽返回 None 会让 `save_shared` 把失败当成功，必须抛异常。"""
    drive = _make_manage()
    with (
        mock.patch.object(
            drive, "request", return_value={"message": "ok", "data": {"status": 0}}
        ),
        pytest.raises(QuarkPanError, match="仍未完成"),
    ):
        drive.submit_task("task-1", retry=2)


def test_store_raises_on_unparsable_url():
    drive = _make_manage()
    with pytest.raises(QuarkPanError, match="无法从链接中解析"):
        drive.store("https://pan.quark.cn/not-a-share")


def test_store_raises_when_share_is_empty():
    drive = _make_manage()
    with (
        mock.patch.object(drive, "get_stoken", return_value="tok"),
        mock.patch.object(drive, "get_detail", return_value=("0", [])),
        pytest.raises(QuarkPanError, match="分享内容为空"),
    ):
        drive.store("https://pan.quark.cn/s/abcd")


def test_store_returns_share_link():
    drive = _make_manage()
    detail = [
        {"fid": "f1", "file_name": "a.txt", "share_fid_token": "t", "file_type": 1}
    ]
    with (
        mock.patch.object(drive, "get_stoken", return_value="tok"),
        mock.patch.object(drive, "get_detail", return_value=("0", detail)),
        mock.patch.object(drive, "save_task_id", return_value="task-1"),
        mock.patch.object(
            drive,
            "task",
            side_effect=[
                {"data": {"status": 2, "save_as": {"save_as_top_fids": ["new-fid"]}}},
                {"data": {"status": 2, "share_id": "share-1"}},
            ],
        ),
        mock.patch.object(drive, "share_task_id", return_value="task-2"),
        mock.patch.object(drive, "get_share_link", return_value="https://x/s/a"),
    ):
        assert drive.store("https://pan.quark.cn/s/abcd") == "https://x/s/a"


# --- 打包布局 ------------------------------------------------------------------


def test_fundrives_is_pep420_namespace_package():
    """`fundrives` 必须保持隐式命名空间包。

    一旦某个 fundrive-* 发行包往 `fundrives/` 里塞 `__init__.py`，同时安装多个
    驱动包时就会互相覆盖/屏蔽。历史上这个文件被删过一次又被重新加回来。
    """
    import fundrives

    assert getattr(fundrives, "__file__", None) is None


def test_quark_package_ships_py_typed():
    """PEP 561 标记要放在 `fundrives/quark/` 里，而不是共享的命名空间目录。"""
    from pathlib import Path

    import fundrives.quark

    package_dir = Path(fundrives.quark.__file__).parent
    assert (package_dir / "py.typed").is_file()
