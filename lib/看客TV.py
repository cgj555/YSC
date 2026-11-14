#coding=utf-8
#!/usr/bin/python
"""
TVBox Python 爬虫源 - 看客 (神马壳 com.shenma.tvlauncher 接口转换)
适用: TVBox / 影视仓 / OK影视 / PickTV / PeekPro / FongMi 等兼容壳

接口链 (2026-10-23 静态逆向 + 联网实测, 详见 hk53_analysis 报告):
1. 引导: GET https://app-1361125462.cos.ap-guangzhou.myqcloud.com/azapp2.json
   正文是纯文本 host, root = "http://" + body + "/"。
2. 主配置: GET <root>/extend/api/set.php?type=set (明文 JSON)
   下发 pgdz (影视数据基址) / miyao (数据 RC4 密钥) / rck (播放签名密钥)。
   pgdz 会轮换 (实测一天内从 43.248.117.148:1234 变到 43.248.117.4:1122),
   所以本源启动时走自举链现拉, 写死值仅作自举失败兜底, ext 可覆盖。
3. 影视数据: <pgdz>/api.php/localhost/* (苹果CMS 风格)
   Category / vod/?ac=list&class=&page= / vod/?ac=detail&ids= /
   So/?ac=list&zm=中文原词 / vod/?&ac=flitter (筛选表)。
   响应多为 hex 串: 明文 = RC4(HexDecode(正文), key=miyao), 标准 RC4;
   detail 外层是明文 JSON, 但其 player 字段与每集 url 仍是 hex+RC4。
4. 播放 (App 内 VideoDetailPlayActivity -> viewmodel/a$b 定案):
   POST <root>/Client<clientNumber>/?url=<详情下发的每集原始 hex 串>
   表单: app=10000, key=hex(RC4(签名串, key=rck))
   签名串 = "&account=520720&password=<redacted>&series="
            + JavaURLEncoder.encode(片名 + "-" + 集名, "UTF-8") + "&edition=2.9"
   clientNumber: App 在详情页首次播放时为空串 (字段 R=0), 本源同此。
   响应同样是 hex+RC4(miyao) 的 JSON:
   {"code":200, "url":"http://.../xxx.m3u8", "UserAgent":" Lavf/57.83.100",
    "Client":1, "maxClient":1, "timeout":"8"}
   url 即真实播放地址, UserAgent 为该地址要求的播放 UA。
   2026-10-23 沙箱实测: 《非常营救》第 01 集解析成功, m3u8 返回
   #EXTM3U / HTTP 200 / application/vnd.apple.mpegurl (待真机复核)。

筛选: flitter 回传拼法已实测生效 (movie+喜剧+2025+内地:
videonum 由约 16 万降至 63), 故启用 filter, 维度与取值全部来自
flitter 实时下发, 不硬编码。

挂载片段见 kanke_site.json (type 3, ext 留空即可, 源内自举)。


{
"key": "py_kanke",
"name": "看客〔PY〕",
"type": 3,
"api": "py_kanke",
"searchable": 1,
"quickSearch": 1,
"filterable": 1,
"ext": ""
}


引号/括号闭合、参数必用、异常分级、__main__ 自检 (含 1 集真实播放验证)。
"""

import base64
import copy
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request

# ==================== 配置中心 (可变值集中于此, 禁止散落硬编码) ====================
DEFAULT_CONFIG = {
    "name": "看客",
    # 自举链入口 (App 内 SplashActivity 硬编码的引导地址)
    "bootstrap_url": "https://app-1361125462.cos.ap-guangzhou.myqcloud.com/azapp2.json",
    # 兜底快照 (2026-10-23 实测值; 自举成功时一律以服务端现拉为准)
    "fallback": {
        "root_url": "http://43.248.117.45:9001",
        "pgdz": "http://43.248.117.148:1234",
        "miyao": "520720ygq",
        "rck": "GN8ZGa4DmaHQrHhSTyQ3FwnhCQt68EXQ",
    },
    "endpoints": {
        "set": "/extend/api/set.php?type=set",
        "category": "/api.php/localhost/Category",
        "list": "/api.php/localhost/vod/?ac=list",
        "detail": "/api.php/localhost/vod/?ac=detail",
        "search": "/api.php/localhost/So/?ac=list",
        "flitter": "/api.php/localhost/vod/?&ac=flitter",
        "client": "/Client{client}/?url={url}",
    },
    # 播放签名常量 (逆向自 App viewmodel/a$b, 为 App 写死值, 非服务端下发)
    "client_api": {
        "app": "10000",
        "account": "520720",
        "password": "password",
        "edition": "2.9",
        "client_number": "",
    },
    "headers": {
        "User-Agent": "Mozilla/5.0 (Linux; Android 13; Pixel 7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/129.0.0.0 Mobile Safari/537.36",
        "Accept": "*/*",
    },
    "pagination": {"page_size": 20},
    # 自举失败且 Category 不可达时的分类兜底 (运营方下发原样, 含其错位映射)
    "fallback_classes": [
        {"type_id": "tvplay", "type_name": "电视剧"},
        {"type_id": "movie", "type_name": "电影"},
        {"type_id": "tvshow", "type_name": "综艺"},
        {"type_id": "comic", "type_name": "动漫"},
        {"type_id": "oumeiju", "type_name": "海外剧"},
        {"type_id": "hanguoju", "type_name": "4K"},
        {"type_id": "movie_4k", "type_name": "少儿"},
        {"type_id": "movie_ZB", "type_name": "短剧"},
    ],
    # flitter 三个维度的 field 名 (筛选回传参数名)
    "filter_fields": ["type", "year", "area"],
}

_VIDEO_EXTS = [".m3u8", ".mp4", ".flv", ".mkv", ".avi", ".ts", ".mpg", ".mov", ".webm"]
_HEX_RE = re.compile(r"^[0-9a-fA-F]+$")
_IDS_RE = re.compile(r"[?&]ids=(\d+)")


def _rc4(data, key):
    """标准 RC4 (与 App utils/g 的 KSA/PRGA 一致, 密钥流每次从 0 对齐)。"""
    if isinstance(data, str):
        data = data.encode("utf-8")
    if isinstance(key, str):
        key = key.encode("utf-8")
    s_box = list(range(256))
    j = 0
    for i in range(256):
        j = (j + s_box[i] + key[i % len(key)]) & 0xFF
        s_box[i], s_box[j] = s_box[j], s_box[i]
    i = j = 0
    out = bytearray()
    for byte in data:
        i = (i + 1) & 0xFF
        j = (j + s_box[i]) & 0xFF
        s_box[i], s_box[j] = s_box[j], s_box[i]
        out.append(byte ^ s_box[(s_box[i] + s_box[j]) & 0xFF])
    return bytes(out)


def _java_urlencode(text):
    """复刻 Java URLEncoder.encode(text, "UTF-8"): 安全集 [A-Za-z0-9.-*_], 空格转 +。"""
    safe = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-*_"
    out = []
    for byte in str(text).encode("utf-8"):
        ch = chr(byte)
        if ch in safe:
            out.append(ch)
        elif ch == " ":
            out.append("+")
        else:
            out.append("%%%02X" % byte)
    return "".join(out)


class Spider:
    """看客爬虫源 (模式 B: 零依赖基类, 标准库-only, 不继承宿主 Spider)。"""

    def __init__(self):
        self.config = copy.deepcopy(DEFAULT_CONFIG)
        fb = self.config["fallback"]
        self.root_url = fb["root_url"].rstrip("/")
        self.pgdz = fb["pgdz"].rstrip("/")
        self.miyao = fb["miyao"]
        self.rck = fb["rck"]
        self._boot_ok = False
        self._boot_tried = False
        self._classes_cache = None
        self._filters_cache = None
        self._player_map = {}  # 线路代码 -> 显示名 (来自详情 player 表)

    # ==================== 基础契约 ====================
    def getDependence(self):
        return []

    def init(self, extend=""):
        """extend: 空 / JSON 字符串 / dict, 可覆盖 root_url/pgdz/miyao/rck 等。"""
        print("============{0}============".format(extend))
        ext = {}
        if isinstance(extend, dict):
            ext = extend
        elif isinstance(extend, str) and extend.strip().startswith("{"):
            try:
                parsed = json.loads(extend)
                if isinstance(parsed, dict):
                    ext = parsed
            except (json.JSONDecodeError, ValueError) as e:
                print("[WARN] extend 不是合法 JSON: {0}".format(e))
        self._bootstrap()
        # ext 覆盖在自举之后生效 (用户显式配置优先于服务端下发)
        for key in ("root_url", "pgdz", "miyao", "rck"):
            if ext.get(key):
                setattr(self, key, str(ext[key]).rstrip("/") if key != "miyao" and key != "rck" else str(ext[key]))
                print("[INFO] ext 覆盖 {0}".format(key))
        if ext.get("bootstrap_url"):
            self.config["bootstrap_url"] = str(ext["bootstrap_url"])
        if ext.get("client_number") is not None:
            self.config["client_api"]["client_number"] = str(ext["client_number"])
        print("[INFO] 看客自举结果: root={0} pgdz={1} boot_ok={2}".format(
            self.root_url, self.pgdz, self._boot_ok))

    def getName(self):
        return self.config.get("name", "看客")

    def isVideoFormat(self, url):
        return any(ext in (url or "").lower() for ext in _VIDEO_EXTS)

    def manualVideoCheck(self):
        return False

    def destroy(self):
        self._classes_cache = None
        self._filters_cache = None
        return None

    def action(self, action):
        print("[INFO] action 未实现具体动作: {0}".format(action))
        return {}

    # ==================== 自举 (azapp2 -> set.php 现拉 pgdz/miyao/rck) ====================
    def _bootstrap(self):
        """拉取根地址与主配置; 任一步失败保留兜底值并记录, 不抛异常。"""
        if self._boot_tried:
            return
        self._boot_tried = True
        status, text = self._fetch(self.config["bootstrap_url"], timeout=10)
        if status == 200 and text and text.strip():
            host = text.strip()
            self.root_url = ("http://" + host).rstrip("/") if not host.startswith("http") \
                else host.rstrip("/")
        else:
            print("[WARN] azapp2 引导失败 (HTTP {0}), 使用兜底 root".format(status))
        set_url = self.root_url + self.config["endpoints"]["set"]
        status, text = self._fetch(set_url, timeout=10)
        if status != 200 or not text:
            print("[WARN] set.php 拉取失败 (HTTP {0}), 使用兜底 pgdz/miyao".format(status))
            return
        try:
            resp = json.loads(text)
        except (json.JSONDecodeError, ValueError) as e:
            print("[ERROR] set.php JSON 解析失败: {0}".format(e))
            return
        data = resp.get("data") if isinstance(resp, dict) else None
        if not isinstance(data, dict):
            data = resp if isinstance(resp, dict) else {}
        if data.get("pgdz"):
            self.pgdz = str(data["pgdz"]).rstrip("/")
        if data.get("miyao"):
            self.miyao = str(data["miyao"])
        if data.get("rck"):
            self.rck = str(data["rck"])
        self._boot_ok = bool(data.get("pgdz") and data.get("miyao"))
        if not self._boot_ok:
            print("[WARN] set.php 缺 pgdz/miyao 字段, 自举不完整")

    def _ensure_boot(self):
        if not self._boot_tried:
            self._bootstrap()

    # ==================== HTTP (异常分级) ====================
    def _fetch(self, url, data=None, timeout=15):
        """返回 (status, text); status=0 表示网络层失败。data 为 dict 时按表单 POST。"""
        headers = dict(self.config["headers"])
        body = None
        if data is not None:
            body = urllib.parse.urlencode(data).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(url, data=body, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            try:
                return e.code, e.read().decode("utf-8", "replace")
            except (OSError, ValueError) as read_err:
                print("[WARN] HTTP {0} 且响应体读取失败 {1}: {2}".format(e.code, url, read_err))
                return e.code, ""
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print("[WARN] 网络错误 {0}: {1}".format(url, e))
            return 0, ""

    # ==================== 数据层 (hex+RC4 / 明文 JSON 双兼容) ====================
    def _decode_payload(self, text, what="data"):
        """明文 JSON 直接解析; 纯 hex 串走 RC4(miyao) 解密后再解析。失败返回 None。"""
        if text is None:
            return None
        body = str(text).strip()
        if not body:
            print("[WARN] {0} 响应为空".format(what))
            return None
        if body[0] in "{[":
            try:
                return json.loads(body)
            except (json.JSONDecodeError, ValueError) as e:
                print("[ERROR] {0} 明文 JSON 解析失败: {1}".format(what, e))
                return None
        if len(body) % 2 == 0 and _HEX_RE.match(body):
            try:
                plain = _rc4(bytes.fromhex(body), self.miyao)
            except ValueError as e:
                print("[ERROR] {0} hex 解码失败: {1}".format(what, e))
                return None
            try:
                plain_text = plain.decode("utf-8")
            except UnicodeDecodeError as e:
                print("[ERROR] {0} RC4 解密结果非 UTF-8 (miyao 可能已轮换): {1}".format(what, e))
                return None
            try:
                return json.loads(plain_text)
            except (json.JSONDecodeError, ValueError) as e:
                print("[ERROR] {0} 解密后 JSON 解析失败: {1}".format(what, e))
                return None
        print("[WARN] {0} 响应形态未知 (非 JSON 非 hex)".format(what))
        return None

    def _api_get(self, path, what="data"):
        """GET pgdz 数据接口并解码; 失败返回 None。"""
        self._ensure_boot()
        url = self.pgdz + path
        status, text = self._fetch(url)
        if status != 200:
            print("[WARN] {0} 请求失败 HTTP {1}: {2}".format(what, status, url))
            return None
        return self._decode_payload(text, what)

    # ==================== 分类与筛选 ====================
    def _fetch_classes(self):
        """Category 实时下发 (tid=type_en, tname=type_name, 运营方错位映射原样收录)。"""
        if self._classes_cache is not None:
            return self._classes_cache
        data = self._api_get(self.config["endpoints"]["category"], "Category")
        classes = []
        if isinstance(data, list):
            for item in data:
                if not isinstance(item, dict):
                    continue
                tid = str(item.get("type_en") or "").strip()
                tname = str(item.get("type_name") or "").strip()
                if not tid:
                    continue
                if str(item.get("type_status", "1")) not in ("1", "True", "true"):
                    continue
                classes.append({"type_id": tid, "type_name": tname or tid})
        if not classes:
            print("[WARN] Category 拉取失败, 使用兜底分类")
            classes = copy.deepcopy(self.config["fallback_classes"])
        self._classes_cache = classes
        return classes

    def _fetch_filters(self):
        """flitter 实时下发筛选表 -> TVBox filters (按分类代码分组, 取值去重)。"""
        if self._filters_cache is not None:
            return self._filters_cache
        data = self._api_get(self.config["endpoints"]["flitter"], "flitter")
        filters = {}
        if isinstance(data, dict):
            for tid, dims in data.items():
                if not isinstance(dims, list):
                    continue
                groups = []
                for dim in dims:
                    if not isinstance(dim, dict):
                        continue
                    field = str(dim.get("field") or "").strip()
                    name = str(dim.get("name") or field).strip()
                    values = dim.get("values") or []
                    if field not in self.config["filter_fields"] or not isinstance(values, list):
                        continue
                    seen = set()
                    opts = [{"n": "全部", "v": ""}]
                    for val in values:
                        val = str(val).strip()
                        if not val or val in seen:
                            continue
                        seen.add(val)
                        opts.append({"n": val, "v": val})
                    if len(opts) > 1:
                        groups.append({"key": field, "name": name, "init": "", "value": opts})
                if groups:
                    filters[str(tid)] = groups
        if not filters:
            print("[WARN] flitter 拉取失败, 本次不挂 filter")
        self._filters_cache = filters
        return filters

    # ==================== 首页 ====================
    def homeContent(self, filter):
        result = {"class": self._fetch_classes()}
        if filter:
            filters = self._fetch_filters()
            if filters:
                result["filters"] = filters
        return result

    def homeVideoContent(self):
        """首页推荐: 复用 movie 分类第一页 (目录实时, 不依赖 recommend 的写死 key)。"""
        videos, _pc, _total, _page = self._list_videos("movie", 1, {})
        return {"list": videos}

    # ==================== 列表 / 分类 / 搜索 ====================
    def _item_to_vod(self, item):
        """列表条目 -> TVBox 列表项; vod_id 从 nextlink 的 ids= 提取。"""
        if not isinstance(item, dict):
            return None
        nextlink = str(item.get("nextlink") or "")
        matched = _IDS_RE.search(nextlink)
        vod_id = matched.group(1) if matched else ""
        title = str(item.get("title") or "").strip()
        if not vod_id or not title:
            return None
        remarks = str(item.get("state") or "").strip()
        if not remarks and item.get("score"):
            remarks = "评分 {0}".format(item["score"])
        return {
            "vod_id": vod_id,
            "vod_name": title,
            "vod_pic": str(item.get("pic") or ""),
            "vod_remarks": remarks,
        }

    def _list_videos(self, tid, pg, extend):
        """ac=list 通用查询, 返回 (videos, pagecount, total, page)。"""
        try:
            page = int(pg)
        except (TypeError, ValueError):
            page = 1
        if page < 1:
            page = 1
        params = [("ac", "list"), ("class", tid)]
        if isinstance(extend, dict):
            for field in self.config["filter_fields"]:
                value = extend.get(field)
                if value:
                    params.append((field, str(value)))
        params.append(("page", str(page)))
        path = self.config["endpoints"]["list"] + "&" + urllib.parse.urlencode(params[1:])
        data = self._api_get(path, "list")
        if not isinstance(data, dict):
            return [], 0, 0, page
        videos = []
        for item in data.get("data") or []:
            vod = self._item_to_vod(item)
            if vod:
                videos.append(vod)
        try:
            pagecount = int(data.get("totalpage") or 0)
        except (TypeError, ValueError):
            pagecount = 0
        try:
            total = int(data.get("videonum") or 0)
        except (TypeError, ValueError):
            total = 0
        try:
            cur_page = int(data.get("pageindex") or page)
        except (TypeError, ValueError):
            cur_page = page
        return videos, pagecount, total, cur_page

    def categoryContent(self, tid, pg, filter, extend):
        """filter 为 False 时忽略 extend 筛选值 (与 homeContent 的 filter 声明一致)。"""
        if not isinstance(extend, dict):
            extend = {}
        effective_extend = extend if filter else {}
        videos, pagecount, total, page = self._list_videos(tid, pg, effective_extend)
        return {
            "list": videos,
            "page": page,
            "pagecount": pagecount,
            "limit": self.config["pagination"]["page_size"],
            "total": total,
        }

    def searchContent(self, key, quick, pg):
        """So 接口 zm 传中文原词 (实测原词命中最准, 拼音首字母仅宽匹配)。"""
        _ = quick  # 快速搜索与普通搜索走同一接口, 保留参数以满足契约
        keyword = str(key or "").strip()
        if not keyword:
            return {"list": [], "page": 1, "pagecount": 0, "limit": 20, "total": 0}
        try:
            page = int(pg)
        except (TypeError, ValueError):
            page = 1
        if page < 1:
            page = 1
        path = self.config["endpoints"]["search"] + "&zm=" + urllib.parse.quote(keyword)
        data = self._api_get(path, "search")
        videos = []
        pagecount, total = 1, 0
        if isinstance(data, dict):
            for item in data.get("data") or []:
                vod = self._item_to_vod(item)
                if vod:
                    videos.append(vod)
            try:
                pagecount = int(data.get("totalpage") or 1)
            except (TypeError, ValueError):
                pagecount = 1
            try:
                total = int(data.get("videonum") or len(videos))
            except (TypeError, ValueError):
                total = len(videos)
        return {
            "list": videos,
            "page": page,
            "pagecount": pagecount,
            "limit": self.config["pagination"]["page_size"],
            "total": total,
        }

    # ==================== 详情 ====================
    @staticmethod
    def _first_text(value):
        """actor/director/area/type 是单元素数组 (元素内逗号拼接), 取 [0] 并容忍 null。"""
        if isinstance(value, list):
            value = value[0] if value else ""
        if value is None:
            return ""
        return str(value).strip()

    def _parse_player_table(self, player_field):
        """player 字段是 hex+RC4 的全局线路表, 解出 {from: show} 并缓存。"""
        table = {}
        if isinstance(player_field, str) and player_field.strip():
            rows = self._decode_payload(player_field, "player表")
            if isinstance(rows, list):
                for row in rows:
                    if isinstance(row, dict) and row.get("from"):
                        table[str(row["from"])] = str(row.get("show") or row["from"])
        if table:
            self._player_map.update(table)
        return table

    @staticmethod
    def _clean_ep_name(name, idx):
        """集名兜底并剔除会破坏 TVBox 分隔符 ($ #) 的字符。"""
        text = str(name or "").strip().replace("$", " ").replace("#", " ")
        return text if text else "第{0}集".format(idx + 1)

    def detailContent(self, ids):
        """详情: player 表查线路显示名, videolist 转 $$$/#/$ 播放串; 空壳记录容错。"""
        if isinstance(ids, (list, tuple)):
            vid = str(ids[0]) if ids else ""
        else:
            vid = str(ids or "")
        if not vid:
            return {"list": []}
        path = self.config["endpoints"]["detail"] + "&ids=" + urllib.parse.quote(vid)
        data = self._api_get(path, "detail")
        if not isinstance(data, dict):
            return {"list": []}
        title = str(data.get("title") or "").strip()
        if not title:
            # 空壳详情 (服务端该条未同步, 字段全 null), 返回空而不报错
            print("[WARN] 详情 {0} 为空壳记录 (title 为空), 跳过".format(vid))
            return {"list": []}
        player_table = self._parse_player_table(data.get("player"))
        videolist = data.get("videolist")
        if not isinstance(videolist, dict):
            videolist = {}
        # 线路顺序: 优先按 player 表下发顺序 (即 App 展示顺序), 其余按 videolist 原序
        ordered_froms = [f for f in player_table.keys() if f in videolist]
        ordered_froms += [f for f in videolist.keys() if f not in ordered_froms]
        play_from_parts = []
        play_url_parts = []
        for from_code in ordered_froms:
            episodes = videolist.get(from_code)
            if not isinstance(episodes, list):
                continue
            ep_parts = []
            for idx, ep in enumerate(episodes):
                if not isinstance(ep, dict):
                    continue
                raw_url = str(ep.get("url") or "").strip()
                if not raw_url:
                    continue  # 空集 (空壳线路) 跳过
                ep_name = self._clean_ep_name(ep.get("title"), idx)
                payload = {"u": raw_url, "n": title, "t": ep_name, "f": from_code}
                play_id = base64.urlsafe_b64encode(
                    json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")
                ep_parts.append("{0}${1}".format(ep_name, play_id))
            if not ep_parts:
                continue
            show = player_table.get(from_code) or self._player_map.get(from_code) or from_code
            play_from_parts.append(str(show).replace("$", " ").replace("#", " "))
            play_url_parts.append("#".join(ep_parts))
        vod = {
            "vod_id": vid,
            "vod_name": title,
            "vod_pic": str(data.get("img_url") or ""),
            "vod_year": str(data.get("pubtime") or ""),
            "vod_area": self._first_text(data.get("area")),
            "vod_actor": self._first_text(data.get("actor")),
            "vod_director": self._first_text(data.get("director")),
            "vod_type": self._first_text(data.get("type")),
            "vod_content": str(data.get("intro") or data.get("trunk") or ""),
            "vod_remarks": "",
            "vod_play_from": "$$$".join(play_from_parts),
            "vod_play_url": "$$$".join(play_url_parts),
        }
        cur_episode = data.get("cur_episode")
        if cur_episode:
            vod["vod_remarks"] = "共{0}集".format(cur_episode)
        return {"list": [vod]}

    # ==================== 播放 ====================
    def _resolve_play_url(self, raw_token, video_name, ep_title, timeout):
        """按 App 协议向 <root>/Client 解析真实地址, 返回 (url, user_agent, err)。"""
        api = self.config["client_api"]
        series = _java_urlencode("{0}-{1}".format(video_name, ep_title))
        sign_plain = ("&account=" + api["account"]
                      + "&password=" + api["password"]
                      + "&series=" + series
                      + "&edition=" + api["edition"])
        if not self.rck:
            return "", "", "缺 rck (自举失败且无兜底), 无法签名"
        key = _rc4(sign_plain.encode("utf-8"), self.rck).hex()
        path = self.config["endpoints"]["client"].format(
            client=api["client_number"],
            url=urllib.parse.quote(str(raw_token), safe=""))
        url = self.root_url + path
        status, text = self._fetch(url, data={"app": api["app"], "key": key}, timeout=timeout)
        if status != 200:
            return "", "", "Client 解析 HTTP {0}".format(status)
        resp = self._decode_payload(text, "Client响应")
        if not isinstance(resp, dict):
            return "", "", "Client 响应解码失败"
        if resp.get("code") not in (200, "200"):
            return "", "", "Client 业务码 {0}: {1}".format(resp.get("code"), resp.get("msg", ""))
        play_url = str(resp.get("url") or "").strip()
        if not play_url.startswith("http"):
            return "", "", "Client 未返回 http 播放地址"
        user_agent = str(resp.get("UserAgent") or "").strip()
        return play_url, user_agent, ""

    def playerContent(self, flag, id, vipFlags):
        """flag=线路显示名, id=detail 打包的播放 id (base64 JSON), vipFlags=壳下发的会员标记。

        解析成功返回真实直链 (parse=0) 与响应指定的 User-Agent;
        失败返回空 url 并在日志写明原因 (播放链已实测打通, 失败多为网络/服务端波动)。
        """
        is_vip = False
        if isinstance(vipFlags, (list, tuple)):
            is_vip = any(bool(x) for x in vipFlags)
        timeout = 30 if is_vip else 20
        raw_token, video_name, ep_title = "", "", ""
        play_id = str(id or "").strip()
        if play_id:
            try:
                payload = json.loads(base64.urlsafe_b64decode(
                    play_id.encode("ascii")).decode("utf-8"))
                if isinstance(payload, dict):
                    raw_token = str(payload.get("u") or "")
                    video_name = str(payload.get("n") or "")
                    ep_title = str(payload.get("t") or "")
            except (ValueError, UnicodeDecodeError) as e:
                print("[WARN] 播放 id 非本源打包格式, 按原始令牌处理: {0}".format(e))
                raw_token = play_id
        if not raw_token:
            print("[ERROR] playerContent 缺少播放令牌 flag={0}".format(flag))
            return {"parse": 0, "jx": 0, "playUrl": "", "url": "", "header": {}}
        if raw_token.startswith("http"):
            return {"parse": 0, "jx": 0, "playUrl": "", "url": raw_token, "header": {}}
        self._ensure_boot()
        url, user_agent, err = self._resolve_play_url(raw_token, video_name, ep_title, timeout)
        if not url:
            print("[ERROR] 播放解析失败 flag={0} title={1} 集={2}: {3}".format(
                flag, video_name, ep_title, err))
            return {"parse": 0, "jx": 0, "playUrl": "", "url": "", "header": {}}
        result = {
            "parse": 0,
            "jx": 0,
            "playUrl": "",
            "url": url,
            "header": {"User-Agent": user_agent or self.config["headers"]["User-Agent"]},
        }
        if ".m3u8" in url.lower():
            result["format"] = "application/x-mpegURL"
        return result

    # ==================== 本地代理 ====================
    def localProxy(self, param):
        """本源播放走直链, 无需本地代理; 按 param 类型做最小校验后返回 404。"""
        if isinstance(param, str):
            try:
                param = json.loads(param)
            except (json.JSONDecodeError, ValueError):
                param = {}
        if isinstance(param, dict) and param.get("type") == "m3u8":
            print("[INFO] localProxy 收到 m3u8 请求但本源未启用代理, 返回 404")
        return [404, "text/plain", b"Not Found", {}]

    # ==================== 配置字典 ====================
    config = {
        "player": {},
        "filter": {},
    }


# ==================== 自检 ====================
def self_test():
    """命令行自检: python3 py_kanke.py

    覆盖自举/分类/筛选/列表/翻页/详情/搜索/播放全链; 播放只验 1 集即停。
    沙箱结论待 TVBox 真机确认 (运营方可能按来源 IP 区别对待)。
    """
    sp = Spider()
    sp.init("")
    checks = []

    def _check(name, cond, extra=""):
        checks.append((name, bool(cond)))
        print("[{0}] {1} {2}".format("PASS" if cond else "FAIL", name, extra))

    _check("init自举(pgdz/miyao)", bool(sp.pgdz and sp.miyao and sp._boot_ok),
           "pgdz={0}".format(sp.pgdz))
    _check("getName", sp.getName() == "看客", sp.getName())
    home = sp.homeContent(True)
    _check("homeContent分类数", len(home.get("class", [])) == 8,
           "classes={0}".format(len(home.get("class", []))))
    movie_filters = (home.get("filters") or {}).get("movie") or []
    filter_keys = [g.get("key") for g in movie_filters]
    _check("flitter筛选", all(k in filter_keys for k in ("type", "year", "area")),
           "movie维度={0}".format(filter_keys))
    hv = sp.homeVideoContent()
    _check("homeVideoContent", len(hv.get("list", [])) > 0,
           "list={0}".format(len(hv.get("list", []))))
    cat1 = sp.categoryContent("movie", "1", True, {})
    _check("category首页", len(cat1.get("list", [])) >= 1 and cat1.get("pagecount", 0) > 1,
           "list={0} pagecount={1} total={2}".format(
               len(cat1.get("list", [])), cat1.get("pagecount"), cat1.get("total")))
    cat2 = sp.categoryContent("movie", "2", True, {})
    ids1 = {v["vod_id"] for v in cat1.get("list", [])}
    ids2 = {v["vod_id"] for v in cat2.get("list", [])}
    _check("category翻页不重合", bool(ids2) and not (ids1 & ids2),
           "p2={0}条 交集={1}".format(len(ids2), len(ids1 & ids2)))
    catf = sp.categoryContent("movie", "1", True, {"type": "喜剧", "year": "2025", "area": "内地"})
    _check("category筛选生效",
           0 < catf.get("total", 0) < (cat1.get("total") or 0),
           "筛选后total={0} 未筛选={1}".format(catf.get("total"), cat1.get("total")))
    det, det_vod = {"list": []}, None
    for cand in cat1.get("list", [])[:5]:
        det = sp.detailContent(cand["vod_id"])
        if det.get("list") and det["list"][0].get("vod_play_url"):
            det_vod = det["list"][0]
            break
    ok_det = bool(det_vod)
    _check("detailContent线路/集数", ok_det,
           "from={0}".format(det_vod.get("vod_play_from", "") if ok_det else ""))
    sch = sp.searchContent("哪吒", False, "1")
    sch_titles = [v["vod_name"] for v in sch.get("list", [])]
    _check("searchContent哪吒", any("哪吒" in t for t in sch_titles),
           "命中={0} 首条={1}".format(len(sch_titles), sch_titles[0] if sch_titles else ""))
    play_url = ""
    if ok_det:
        flag = det_vod["vod_play_from"].split("$$$")[0]
        first_ep = det_vod["vod_play_url"].split("$$$")[0].split("#")[0]
        play_id = first_ep.split("$", 1)[1] if "$" in first_ep else ""
        pc = sp.playerContent(flag, play_id, [])
        play_url = pc.get("url") or ""
        _check("playerContent解析直链", play_url.startswith("http"),
               "url={0}".format(play_url[:80]))
    else:
        _check("playerContent解析直链", False, "detail 无可播集, 跳过解析")
    alive = False
    if play_url:
        # 验活判据: m3u8 看 #EXTM3U; 其他流看 Range GET 的 status 与 Content-Type
        try:
            req = urllib.request.Request(
                play_url, headers={"User-Agent": "Lavf/57.83.100", "Range": "bytes=0-1023"})
            with urllib.request.urlopen(req, timeout=30) as resp:
                head = resp.read(512)
                ctype = (resp.headers.get("Content-Type") or "").lower()
                if ".m3u8" in play_url.lower():
                    alive = resp.status in (200, 206) and b"#EXTM3U" in head
                else:
                    alive = resp.status in (200, 206) and "text/html" not in ctype
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print("[WARN] 播放地址验活失败: {0}".format(e))
    _check("播放地址验活(仅1集)", alive, "status/Content-Type 校验" if alive else "未通过/未执行")
    _check("isVideoFormat",
           sp.isVideoFormat("https://x/y.m3u8") and not sp.isVideoFormat("https://x/y.html"))
    _check("manualVideoCheck", sp.manualVideoCheck() is False)
    _check("getDependence", sp.getDependence() == [])
    _check("localProxy", sp.localProxy({})[0] == 404)
    failed = [name for name, ok in checks if not ok]
    print("自检完成: {0}/{1} 通过".format(len(checks) - len(failed), len(checks)))
    if failed:
        print("失败项: {0}".format(", ".join(failed)))
    return not failed


if __name__ == "__main__":
    import sys
    ok = self_test()
    sys.exit(0 if ok else 1)
