#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
resolve_city.py — 指定地域 (CITIES_CONFIG の1エントリ) を JMA の公式コード体系から
決定論的に解決するヘルパー。

principle 2「最も細かいアンカー1つ (class20 の市) を選び、そこから official な階層で
class10 / office / 予報気温地点 / AMeDAS地点 を決定論的に導出する」を実装したもの。
手書きでコードを拾うと class10 と class20 と観測点が食い違い、別の場所の警報・気温を
サイレントに表示するバグになり得る。これを構造的に防ぐ。

データソース (すべて気象庁の非公式 bosai JSON。コード体系自体は公式 XML 技術資料と同体系):
  - 地域階層:   https://www.jma.go.jp/bosai/common/const/area.json
                (centers > offices > class10s > class15s > class20s)
  - 予報気温:   https://www.jma.go.jp/bosai/forecast/data/forecast/<office>.json
                週間予報セクション [1] の気温 timeSeries の area.code
  - AMeDAS地点: https://www.jma.go.jp/bosai/amedas/const/amedastable.json
                (kjName 一致。AMeDAS 取得を無効化する構成なら不要 = --no-amedas)

使い方:
  python3 resolve_city.py 熊本            # 都市名で解決
  python3 resolve_city.py 横浜            # 政令市の分割 (北部/南部) も束ねる
  python3 resolve_city.py 熊本 --no-amedas
  python3 resolve_city.py --pick 4310000  # class20 コードを直接指定 (曖昧時)

出力: CITIES_CONFIG に貼れる Python dict 1行 + 内訳の説明。
複数 office にまたがる候補があるときは一覧を出して終了 (要 --pick 指定)。
"""

import argparse
import json
import os
import ssl
import sys
import urllib.request

UA = {"User-Agent": "mulmoclaude-jma-weather/1.0"}
HERE = os.path.dirname(os.path.abspath(__file__))
CONST_DIR = os.path.join(HERE, "const")
AREA_URL = "https://www.jma.go.jp/bosai/common/const/area.json"
AMEDAS_TABLE_URL = "https://www.jma.go.jp/bosai/amedas/const/amedastable.json"
FORECAST_URL = "https://www.jma.go.jp/bosai/forecast/data/forecast/{office}.json"


def _contexts():
    """試行する SSL コンテキストを優先順で返す。
    certifi があればそれを最優先 (macOS の Python.framework は CA バンドル未同梱で
    default だと CERTIFICATE_VERIFY_FAILED になるため)。次に default
    (Docker サンドボックス内ではこれで通る)。"""
    ctxs = []
    try:
        import certifi
        ctxs.append(ssl.create_default_context(cafile=certifi.where()))
    except Exception:
        pass
    try:
        ctxs.append(ssl.create_default_context())
    except Exception:
        pass
    return ctxs or [None]


def fetch_json(url, timeout=20):
    req = urllib.request.Request(url, headers=UA)
    last = None
    for ctx in _contexts():
        try:
            kw = {"timeout": timeout}
            if ctx is not None:
                kw["context"] = ctx
            with urllib.request.urlopen(req, **kw) as resp:
                return json.loads(resp.read())
        except Exception as e:
            last = e
            continue
    raise SystemExit(
        "[error] fetch failed: {}\n  url={}\n  ヒント: `pip3 install certifi` か、"
        "macOS なら Python の Install Certificates.command を実行してください。".format(last, url)
    )


def load_area(refresh=False):
    """area.json を const/ にキャッシュして読む (L0 const の作法)。"""
    path = os.path.join(CONST_DIR, "area.json")
    if refresh or not os.path.exists(path):
        os.makedirs(CONST_DIR, exist_ok=True)
        data = fetch_json(AREA_URL)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return data
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def resolve_chain(area, c20_code):
    """class20 コードから class15 / class10 / office を辿る。"""
    c20 = area["class20s"][c20_code]
    c15_code = c20["parent"]
    c10_code = area["class15s"][c15_code]["parent"]
    office_code = area["class10s"][c10_code]["parent"]
    return {
        "class20": c20_code,
        "class20_name": c20["name"],
        "class15": c15_code,
        "class10": c10_code,
        "class10_name": area["class10s"][c10_code]["name"],
        "office": office_code,
        "office_name": area["offices"][office_code]["name"],
    }


def find_candidates(area, query):
    """query を名前に含む class20 を全部拾い、office ごとにグループ化。"""
    hits = []
    for code, v in area["class20s"].items():
        if query in v["name"]:
            hits.append((code, v["name"]))
    groups = {}  # office -> list of (c20, name, chain)
    for code, name in hits:
        ch = resolve_chain(area, code)
        groups.setdefault(ch["office"], []).append((code, name, ch))
    return groups


def base_city_name(name):
    """'横浜市北部' -> '横浜市' のように方位/部分の接尾辞を落として市町村名の幹を得る。"""
    for suf in ("北部", "南部", "東部", "西部", "中部", "北区", "中央"):
        if name.endswith(suf):
            return name[: -len(suf)]
    return name


def resolve_temp_code(office, city_query):
    """週間予報 [1] の気温 timeSeries から temp_code を決める。
    複数地点があれば都市名一致を優先、無ければ先頭。"""
    d = fetch_json(FORECAST_URL.format(office=office))
    weekly = d[1]
    temp_ts = None
    for ts in weekly["timeSeries"]:
        a0 = ts["areas"][0]
        if "tempsMax" in a0 or "tempsMin" in a0:
            temp_ts = ts
            break
    if not temp_ts:
        return None, None, []
    areas = [(x["area"]["code"], x["area"]["name"]) for x in temp_ts["areas"]]
    chosen = None
    for code, name in areas:
        if city_query in name or name in city_query:
            chosen = (code, name)
            break
    if not chosen:
        chosen = areas[0]
    return chosen[0], chosen[1], areas


def resolve_amedas_code(city_query, temp_name):
    """amedastable.json の kjName 一致で AMeDAS 地点コードを引く (best effort)。
    AMeDAS 取得を無効化する構成では不要。"""
    table = fetch_json(AMEDAS_TABLE_URL)
    targets = [t for t in (temp_name, city_query) if t]
    # 完全一致 -> 前方一致 の順
    for want in targets:
        for code, v in table.items():
            if v.get("kjName") == want:
                return code, v.get("kjName")
    for want in targets:
        for code, v in table.items():
            kj = v.get("kjName", "")
            if kj and (kj.startswith(want) or want.startswith(kj)):
                return code, kj
    return None, None


def main():
    ap = argparse.ArgumentParser(description="JMA 指定地域コードを決定論的に解決する")
    ap.add_argument("query", nargs="?", help="都市名 (例: 熊本 / 横浜)")
    ap.add_argument("--pick", help="class20 コードを直接指定 (曖昧時の確定用)")
    ap.add_argument("--no-amedas", action="store_true", help="amedas_code を解決しない")
    ap.add_argument("--refresh", action="store_true", help="area.json を取り直す")
    ap.add_argument("--order", type=int, default=99, help="config cities[] の order 値")
    ap.add_argument("--write", action="store_true",
                    help="items/config.json の cities[] に upsert する (既存 map 座標は保持)")
    ap.add_argument("--set-default", action="store_true",
                    help="--write と併用。この都市を defaultCity に設定する")
    args = ap.parse_args()

    area = load_area(refresh=args.refresh)

    # --- 対象 class20 群の決定 ---
    if args.pick:
        if args.pick not in area["class20s"]:
            raise SystemExit("[error] class20 コードが見つかりません: " + args.pick)
        ch = resolve_chain(area, args.pick)
        office = ch["office"]
        base = base_city_name(ch["class20_name"])
        # 同 office・同 class10 で同じ市名幹を持つ class20 を束ねる (政令市の北部/南部対応)
        bundle = []
        for code, v in area["class20s"].items():
            c = resolve_chain(area, code)
            if c["office"] == office and c["class10"] == ch["class10"] and base_city_name(v["name"]).startswith(base):
                bundle.append((code, v["name"], c))
        primary = ch
    else:
        if not args.query:
            raise SystemExit("使い方: python3 resolve_city.py <都市名>  または  --pick <class20コード>")
        groups = find_candidates(area, args.query)
        if not groups:
            raise SystemExit("[error] 該当する class20 (市町村) がありません: " + args.query)
        if len(groups) > 1:
            print("[曖昧] 複数の府県予報区に候補があります。--pick <class20コード> で確定してください:\n")
            for office, lst in groups.items():
                oname = area["offices"][office]["name"]
                print(f"  office {office} ({oname}):")
                for code, name, ch in lst:
                    print(f"    --pick {code}   {name}  [{ch['class10_name']}]")
            sys.exit(2)
        office, lst = next(iter(groups.items()))
        # 同じ市名幹で束ねる
        base = base_city_name(lst[0][1])
        bundle = [(c, n, ch) for (c, n, ch) in lst if base_city_name(n).startswith(base)]
        primary = bundle[0][2]

    warning_areas = sorted({code for code, _, _ in bundle})
    bundle_names = [n for _, n, _ in bundle]

    # --- temp_code / amedas_code ---
    base_name = base_city_name(primary["class20_name"]).rstrip("市町村")
    temp_code, temp_name, temp_areas = resolve_temp_code(primary["office"], base_name)
    amedas_code, amedas_name = (None, None)
    if not args.no_amedas:
        amedas_code, amedas_name = resolve_amedas_code(base_name, temp_name)

    # --- 出力 ---
    name_disp = base_city_name(primary["class20_name"]).rstrip("市町村") or primary["class20_name"]
    region = primary["office_name"]
    cfg = {
        "office": primary["office"],
        "name": name_disp,
        "region": region,
        "area_code": primary["class10"],
        "warning_areas": warning_areas,
        "temp_code": temp_code,
    }
    if not args.no_amedas:
        cfg["amedas_code"] = amedas_code
    cfg["order"] = args.order

    print("解決結果:")
    print(f"  office     {primary['office']}  ({primary['office_name']})")
    print(f"  area_code  {primary['class10']}  (class10: {primary['class10_name']})  ← 天気/降水確率/3h予報")
    print(f"  warning    {warning_areas}  ({' / '.join(bundle_names)})  ← class20 警報注意報")
    print(f"  temp_code  {temp_code}  ({temp_name})  ← 週間予報の気温地点" + (
        f"   ※候補 {temp_areas}" if len(temp_areas) > 1 else ""))
    if not args.no_amedas:
        print(f"  amedas     {amedas_code}  ({amedas_name})  ← 現在気温/降水 (AMeDAS取得を使う場合のみ必要)")
    print("\nCITIES_CONFIG に貼れる形:")
    # dict を CITIES_CONFIG 既存スタイルに寄せて1行で出す
    parts = [f'"office": "{cfg["office"]}"', f'"name": "{cfg["name"]}"', f'"region": "{cfg["region"]}"',
             f'"area_code": "{cfg["area_code"]}"', f'"warning_areas": {json.dumps(warning_areas)}',
             f'"temp_code": "{cfg["temp_code"]}"']
    if not args.no_amedas:
        parts.append(f'"amedas_code": "{cfg.get("amedas_code")}"')
    parts.append(f'"order": {cfg["order"]}')
    print("    {" + ", ".join(parts) + "},")
    print("\nJSON:")
    print(json.dumps(cfg, ensure_ascii=False, indent=2))

    if args.write:
        write_config_entry(cfg, set_default=args.set_default, no_amedas=args.no_amedas)


def write_config_entry(cfg, set_default=False, no_amedas=False):
    """resolve 結果を items/config.json の cities[] に upsert する。
    同 office の既存エントリがあれば codes を更新し、map 座標は保持する。"""
    items_dir = os.path.join(HERE, "items")
    os.makedirs(items_dir, exist_ok=True)
    path = os.path.join(items_dir, "config.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            config = json.load(f)
    else:
        config = {"id": "config", "source": "config", "defaultCity": cfg["office"], "cities": []}
    codes = {"area": cfg["area_code"], "temp": cfg["temp_code"], "warning": cfg["warning_areas"]}
    if not no_amedas and cfg.get("amedas_code"):
        codes["amedas"] = cfg["amedas_code"]
    entry = {
        "office": cfg["office"], "name": cfg["name"], "region": cfg["region"],
        "order": cfg["order"], "codes": codes,
    }
    cities = config.setdefault("cities", [])
    for i, c in enumerate(cities):
        if c.get("office") == cfg["office"]:
            if "map" in c:
                entry["map"] = c["map"]  # 著者が付けた地図座標は保持
            cities[i] = entry
            break
    else:
        cities.append(entry)
    cities.sort(key=lambda x: x.get("order", 99))
    if set_default:
        config["defaultCity"] = cfg["office"]
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    print(f"\n[write] items/config.json を更新 ({cfg['office']} {cfg['name']})"
          + (f" / defaultCity={cfg['office']}" if set_default else "")
          + f"  ※全国マップに出すには map 座標 (xPct/yPct/pref/placement) を手動で追記")


if __name__ == "__main__":
    main()
