#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
jma_http.py — JMA 取得スクリプト (fetch.py / fetch_chart.py / fetch_marine.py) 共通の
HTTP 取得 + 書き込みユーティリティ。

L3 効率化:
  - 条件付きGET (If-None-Match / If-Modified-Since)。JMA bosai は ETag / Last-Modified を返し、
    未更新には 304 Not Modified を返す。未更新時はローカルキャッシュの本文を返してダウンロードを省略。
    リクエスト数は変わらないが、未更新時は本文0バイト + 書き込みスキップで帯域/処理を激減。
    鮮度リスクはゼロ (変更があれば 200 が返り即反映)。
  - write_record_if_changed: updatedAt 以外が既存ファイルと同一なら書かない (churn 防止)。

キャッシュは このファイルと同じディレクトリの const/http_cache/ に置く
(= data/jma-weather/const/http_cache)。3 スクリプトは同じ場所に配置される前提。
"""

from __future__ import annotations

import hashlib
import json
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

UA = "mulmoclaude-jma-weather/1.0"
_HTTP_CACHE_DIR = Path(__file__).resolve().parent / "const" / "http_cache"


def _ssl_context():
    """ホストの Python に CA バンドルが無い場合 (macOS の Python.framework 等) は certifi に
    フォールバックして検証を維持する。Docker サンドボックス内では default で通る。"""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        try:
            return ssl.create_default_context()
        except Exception:
            return None


def _cache_paths(url: str) -> "tuple[Path, Path]":
    h = hashlib.sha1(url.encode("utf-8")).hexdigest()
    return _HTTP_CACHE_DIR / h, _HTTP_CACHE_DIR / (h + ".meta")


def http_get(url: str, timeout: int = 15, user_agent: str = UA) -> bytes:
    """条件付きGET。200 なら本文+ETag を保存して返す。304 ならキャッシュ本文を返す。"""
    body_path, meta_path = _cache_paths(url)
    headers = {"User-Agent": user_agent}
    if meta_path.exists() and body_path.exists():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("etag"):
                headers["If-None-Match"] = meta["etag"]
            if meta.get("last_modified"):
                headers["If-Modified-Since"] = meta["last_modified"]
        except Exception:
            pass
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            body = resp.read()
            etag = resp.headers.get("ETag")
            lm = resp.headers.get("Last-Modified")
            try:
                _HTTP_CACHE_DIR.mkdir(parents=True, exist_ok=True)
                body_path.write_bytes(body)
                meta_path.write_text(
                    json.dumps({"etag": etag, "last_modified": lm, "url": url}, ensure_ascii=False),
                    encoding="utf-8")
            except Exception as e:
                print(f"[warn] http cache write failed for {url}: {e}", file=sys.stderr)
            return body
    except urllib.error.HTTPError as e:
        if e.code == 304 and body_path.exists():
            return body_path.read_bytes()  # 未更新: キャッシュ本文を返す
        raise


def fetch_json(url: str, timeout: int = 15, user_agent: str = UA) -> object:
    return json.loads(http_get(url, timeout, user_agent))


def fetch_bytes(url: str, timeout: int = 15, user_agent: str = UA) -> bytes:
    return http_get(url, timeout, user_agent)


def write_record_if_changed(path: Path, rec: dict, newline: bool = True) -> bool:
    """レコードを書き出す。ただし updatedAt 以外が既存ファイルと完全一致なら書かずに False を返す。
    毎時の無駄な書き込み churn と updatedAt の無意味な更新を防ぐ。
    返り値: 実際に書いたら True。"""
    text = json.dumps(rec, ensure_ascii=False, indent=2) + ("\n" if newline else "")
    if path.exists():
        try:
            old = json.loads(path.read_text(encoding="utf-8"))
            if {k: v for k, v in old.items() if k != "updatedAt"} == \
               {k: v for k, v in rec.items() if k != "updatedAt"}:
                return False
        except Exception:
            pass
    path.write_text(text, encoding="utf-8")
    return True
