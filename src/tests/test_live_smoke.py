# tests/test_live_smoke.py
# live 冒烟（review P2 根因建议：mock 只能锁定"我们相信的契约"，真实容器的
# 回归要靠实连一次）。只做只读操作（登录 + 列库/列文档），不改任何服务端状态。
#
# 默认离线套件不会收集本文件——pytest.ini 的 addopts 是 -m "not live"。
# 显式运行：
#   RAGFLOW_EMAIL=... RAGFLOW_PASSWORD=... python3 -m pytest -m live
import os
import sys

sys.path.insert(0, "..")

import pytest

from config import PUBLIC_PEM, RAGFLOW_EMAIL, RAGFLOW_PASSWORD
from ragflow_client import RAGFlowClient

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(
        not os.getenv("RAGFLOW_EMAIL") or not os.getenv("RAGFLOW_PASSWORD"),
        reason="RAGFLOW_EMAIL / RAGFLOW_PASSWORD 未设置——live 冒烟需真实凭据",
    ),
]


def test_login_and_list_datasets():
    """登录加密与 code==0 信封检查对真实 v0.27.0 端点成立。"""
    client = RAGFlowClient(RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM)
    datasets = client.list_datasets()
    assert isinstance(datasets, list)


def test_document_listing_contract():
    """/documents 响应按 data.docs 解析且翻页参数 page/page_size 被接受。"""
    client = RAGFlowClient(RAGFLOW_EMAIL, RAGFLOW_PASSWORD, PUBLIC_PEM)
    datasets = client.list_datasets()
    if not datasets:
        pytest.skip("实例内暂无数据集")
    docs = client.list_documents(datasets[0]["id"])
    assert isinstance(docs, list)
