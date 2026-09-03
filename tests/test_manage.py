"""针对 `fundrives.quark.manage` 公开 API 的正常路径与边界测试。

网络请求均通过 mock 隔离，不会发起真实请求。
"""

from __future__ import annotations

from unittest import mock

from fundrives.quark.manage import (
    QuarkPanManage,
    _mask_share_url,
    generate_random_code,
    get_datetime,
    get_id_from_url,
)


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
