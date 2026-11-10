# -*- coding: utf-8 -*-
"""
hive_client.py —— Hive 查询封装（pyhive）

统一出口：所有 Python 脚本通过 query_df / query_rows 取数，
不在业务脚本里直接写连接代码。
"""
import json
from decimal import Decimal

import numpy as np
import pandas as pd
from pyhive import hive

from config import HIVE_HOST, HIVE_PORT, HIVE_USER


class _JSONEncoder(json.JSONEncoder):
    """处理 Hive 返回的 Decimal / datetime，避免 json.dump 报错。"""
    def default(self, o):
        if isinstance(o, Decimal):
            return float(o)
        if hasattr(o, "isoformat"):
            return o.isoformat()
        return super().default(o)


def json_safe(obj):
    """递归把 Decimal / numpy 数值转成原生 Python 类型。"""
    if isinstance(obj, dict):
        return {k: json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [json_safe(v) for v in obj]
    if isinstance(obj, Decimal):
        return float(obj)
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating,)):
        return float(obj)
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def dump_json(obj, path):
    """统一出口：保证 Decimal 等类型可序列化。"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(json_safe(obj), f, ensure_ascii=False, indent=2, cls=_JSONEncoder)


def get_conn(database="default"):
    """建立 HiveServer2 连接（NOSASL 之外的默认方式，本环境无需密码）。"""
    return hive.Connection(
        host=HIVE_HOST,
        port=HIVE_PORT,
        username=HIVE_USER,
        database=database,
    )


def query_df(sql, database="default"):
    """执行 SQL 返回 DataFrame（列名取 Hive 返回的 col_name）。"""
    conn = get_conn(database)
    try:
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        cols = [d[0].split(".")[-1] for d in cur.description] if cur.description else []
        return pd.DataFrame(rows, columns=cols)
    finally:
        conn.close()


def query_rows(sql, database="default"):
    """执行 SQL 返回 (列名, 行列表)。"""
    conn = get_conn(database)
    try:
        cur = conn.cursor()
        cur.execute(sql)
        rows = cur.fetchall()
        cols = [d[0].split(".")[-1] for d in cur.description] if cur.description else []
        return cols, rows
    finally:
        conn.close()


def ping():
    """连通性检查，返回 (bool, 说明)。"""
    try:
        _, rows = query_rows("select 1 as ok")
        return True, "HiveServer2 连接正常"
    except Exception as e:
        return False, f"HiveServer2 连接失败：{type(e).__name__}: {e}"
