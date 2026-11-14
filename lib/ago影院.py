#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ago影院 (agoys.com) TVBox / 影视仓 Python 适配源
=================================================
特性:
  1. 五个一级分类: 电影 / 剧集 / 综艺 / 动漫 / 短剧
  2. 二级分类筛选: 类型 - 地区 - 年份 - 排序 (站点原生筛选段, 全部可用)
  3. 搜索: 第 1 页 HTML 解析, 第 2 页起走站点 JSON 接口, 支持翻页
  4. 加载速度优化:
     - requests.Session 连接池复用 (keep-alive)
     - 列表第 2 页起直接用轻量 JSON 接口 (单次响应 ~12KB, 远小于 HTML)
     - 详情页只请求一次, 播放地址靠 POST /source/ 单步直出 m3u8
     - 无 pyquery/lxml 依赖, 纯正则解析, 模块加载更快
  5. 域名跟踪: 内置候选域名列表, 启动时自动探活并缓存 (TTL 30 分钟),
     主域名失效自动切换候选域名; 也支持在壳子 extend 里传 {"domain": "https://xxx"}
     手动指定新域名
站点结构速记 (维护用):
  首页            https://agoys.com/
  筛选列表        /filter/{mtype}/{type}-{area}-{year}-{sort}?page=N   (page=1 返回 HTML, >=2 返回 JSON)
  搜索            /search?wd={关键词}&page=N                           (page=1 返回 HTML, >=2 返回 JSON)
  详情            /{mtype}/{vid}                                       mtype: film|tvseries|varietyshow|anime|shortplay
  播放地址        POST /source/  data: id={sid}                        返回 m3u8 直链文本
  详情页数据      内联 var urlList = {'source': [线路...], 'url_list': [[剧集...]...]}
"""

import re
import sys
import ast
import json
import time
import threading
from urllib.parse import quote

import requests
from requests.adapters import HTTPAdapter

try:
    from urllib3 import disable_warnings
    from urllib3.exceptions import InsecureRequestWarning
    disable_warnings(InsecureRequestWarning)
except Exception:  # noqa: BLE001 - 老版本 urllib3 没有这些符号时静默跳过
    pass

sys.path.append('..')
from base.spider import Spider  # noqa: E402

# ------------------------------------------------------------------
# 可维护配置: 候选域名 (失效时在此追加新域名即可)
# ------------------------------------------------------------------
DOMAIN_CANDIDATES = [
    'https://agoys.com',
    'https://www.agoys.com',
]
DOMAIN_TTL = 1800          # 域名探活结果缓存秒数
HTTP_TIMEOUT = (6, 15)     # (连接超时, 读取超时)
UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36')

# 站点筛选段映射 (与网页 /filter/ 选项一致)
CLASS_LIST = [
    ('电影', 'film'),
    ('剧集', 'tvseries'),
    ('综艺', 'varietyshow'),
    ('动漫', 'anime'),
    ('短剧', 'shortplay'),
]
_GENRES_21 = {'剧情': '1', '喜剧': '2', '动作': '3', '爱情': '4', '科幻': '5',
              '悬疑': '6', '惊悚': '7', '恐怖': '8', '犯罪': '9', '同性': '10',
              '音乐': '11', '歌舞': '12', '传记': '13', '历史': '14', '战争': '15',
              '西部': '16', '奇幻': '17', '冒险': '18', '灾难': '19', '武侠': '20',
              '伦理': '21'}
_TYPE_MAPS = {
    'film': _GENRES_21,
    'tvseries': _GENRES_21,
    'anime': _GENRES_21,
    'varietyshow': {'真人秀': '1', '脱口秀': '2', '纪录片': '3', '传记': '4', '歌舞': '5'},
    'shortplay': {'剧情': '1', '喜剧': '2', '动作': '3', '爱情': '4',
                  '科幻': '5', '悬疑': '6', '惊悚': '7', '恐怖': '8'},
}
_AREA_MAPS = {
    'film': {'中国大陆': '1', '美国': '2', '香港': '3', '台湾': '4', '日本': '5',
             '韩国': '6', '英国': '7', '法国': '8', '德国': '9', '意大利': '10',
             '西班牙': '11', '印度': '12', '泰国': '13', '俄罗斯': '14', '伊朗': '15',
             '加拿大': '16', '澳大利亚': '17', '爱尔兰': '18', '瑞典': '19',
             '巴西': '20', '丹麦': '21'},
    'tvseries': {'中国大陆': '1', '美国': '2', '香港': '3', '台湾': '4', '日本': '5',
                 '韩国': '6', '英国': '7', '法国': '8', '德国': '9', '意大利': '10',
                 '西班牙': '11', '印度': '12', '泰国': '13', '俄罗斯': '14', '伊朗': '15',
                 '加拿大': '16', '澳大利亚': '17', '爱尔兰': '18', '瑞典': '19',
                 '巴西': '20', '丹麦': '21'},
    'varietyshow': {'中国大陆': '1', '美国': '2', '香港': '3', '台湾': '4',
                    '日本': '5', '韩国': '6'},
    'anime': {'中国大陆': '1', '美国': '2', '日本': '3', '韩国': '4'},
    'shortplay': {'中国大陆': '1'},
}
_YEAR_OPTIONS = ([('全部', '0')]
                 + [(str(y), str(y)) for y in range(2026, 2004, -1)]
                 + [('其他', '1')])
_SORT_OPTIONS = [('新上线', '0'), ('热播榜', '1'), ('好评榜', '2')]


class Spider(Spider):

    # 类级域名探活缓存 (所有实例共享)
    _domain_lock = threading.Lock()
    _domain_cache = {'base': None, 'ts': 0.0}

    def __init__(self):
        super().__init__()
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': UA,
            'Referer': f'{DOMAIN_CANDIDATES[0]}/',
            'X-Requested-With': 'XMLHttpRequest',
        })
        self.session.verify = False
        adapter = HTTPAdapter(pool_connections=20, pool_maxsize=20, max_retries=1)
        self.session.mount('https://', adapter)
        self.session.mount('http://', adapter)
        self._base = None

    # ==============================================================
    #  域名跟踪: 探活 / 缓存 / 切换
    # ==============================================================
    def _domain_ok(self, base):
        """探活: 首页能 200 且特征词命中才算存活"""
        try:
            r = self.session.get(f'{base}/', timeout=HTTP_TIMEOUT, allow_redirects=True)
            head = r.text[:6000]
            return r.status_code == 200 and ('ago' in head or '影院' in head)
        except Exception:  # noqa: BLE001
            return False

    def _resolve_base(self, force=False):
        now = time.time()
        with self._domain_lock:
            cached = self._domain_cache['base']
            if cached and not force and now - self._domain_cache['ts'] < DOMAIN_TTL:
                return cached
            candidates = ([self._base] if self._base else []) + DOMAIN_CANDIDATES
            for dom in candidates:
                if self._domain_ok(dom):
                    self._domain_cache.update(base=dom, ts=now)
                    return dom
            # 全部探活失败: 沿用旧值或首个候选, 不让源直接崩掉
            fallback = cached or DOMAIN_CANDIDATES[0]
            self._domain_cache.update(base=fallback, ts=now)
            return fallback

    def _base_url(self):
        if not self._base:
            self._base = self._resolve_base()
        return self._base

    @staticmethod
    def _custom_domain(extend):
        """壳子 extend 里允许传 http(s) 域名或 {"domain": "..."} 手动指定新域名"""
        if not extend:
            return None
        s = extend if isinstance(extend, str) else json.dumps(extend, ensure_ascii=False)
        s = s.strip()
        if s.startswith('{'):
            try:
                s = str(json.loads(s).get('domain') or '')
            except Exception:  # noqa: BLE001
                return None
        if s.startswith('http'):
            return s.rstrip('/')
        return None

    # ==============================================================
    #  生命周期
    # ==============================================================
    def init(self, extend=""):
        custom = self._custom_domain(extend)
        if custom:
            self._base = custom if self._domain_ok(custom) else None
        self._base_url()

    def getName(self):
        return "ago影院"

    def isVideoFormat(self, url):
        pass

    def manualVideoCheck(self):
        pass

    def localProxy(self, params):
        return [200, "text/plain", {}, ""]

    # ==============================================================
    #  首页
    # ==============================================================
    def homeContent(self, filter):
        result = {'class': [], 'filters': {}, 'list': [], 'parse': 0, 'jx': 0}
        for name, tid in CLASS_LIST:
            result['class'].append({'type_id': tid, 'type_name': name})
        result['filters'] = self._build_filters()
        try:
            html = self._get(f'{self._base_url()}/')
            result['list'] = self._parse_grid_cards(html)[:40]
        except Exception as e:  # noqa: BLE001
            print(f'homeContent error: {e}')
        return result

    def homeVideoContent(self):
        try:
            html = self._get(f'{self._base_url()}/')
            cards = self._parse_grid_cards(html)
            if cards:
                return {'list': cards[:12]}
        except Exception as e:  # noqa: BLE001
            print(f'homeVideoContent error: {e}')
        return {'list': []}

    # ==============================================================
    #  分类 (含二级筛选)
    # ==============================================================
    def categoryContent(self, tid, pg, filter, extend):
        result = {'list': [], 'parse': 0, 'jx': 0}
        page = int(pg) if str(pg or '').isdigit() else 1
        ext = self._ext_string(extend)
        base = self._base_url()
        try:
            if page <= 1:
                # 第 1 页: 站点返回完整 HTML, 解析卡片
                html = self._get(f'{base}/filter/{tid}/{ext}')
                vods = self._parse_grid_cards(html)
            else:
                # 第 2 页起: 站点滚动加载的 JSON 接口, 又小又快
                data = self._get_json(f'{base}/filter/{tid}/{ext}?page={page}')
                vods = [self._json_item(x) for x in (data or {}).get('list', [])]
            vods = [v for v in vods if v]
            result['list'] = vods
        except Exception as e:  # noqa: BLE001
            print(f'categoryContent error: {e}')
        n = len(result['list'])
        result.update(page=page, pagecount=page + 1 if n else page,
                      limit=n, total=n)
        return result

    # ==============================================================
    #  详情
    # ==============================================================
    def detailContent(self, ids):
        result = {'list': [], 'parse': 0, 'jx': 0}
        raw = str(ids[0]) if ids else ''
        if not raw:
            return result
        if '-' in raw:
            mtype, vid = raw.split('-', 1)
            page_pairs = [(mtype, vid)]
        else:
            # 兼容纯数字 id: 依次尝试常见大类
            page_pairs = [(t, raw) for t in ('film', 'tvseries', 'anime', 'varietyshow')]
        for mtype, vid in page_pairs:
            try:
                html = self._get(f'{self._base_url()}/{mtype}/{vid}')
                vod = self._parse_detail(html, vid)
                if vod and vod.get('vod_play_url'):
                    result['list'].append(vod)
                    return result
                if vod:
                    result['list'].append(vod)
                    return result
            except Exception as e:  # noqa: BLE001
                print(f'detailContent {mtype}/{vid} error: {e}')
        return result

    # ==============================================================
    #  播放
    # ==============================================================
    def playerContent(self, flag, id, vipFlags):
        header = {'User-Agent': UA}
        result = {'parse': 0, 'jx': 0, 'header': header, 'url': ''}
        try:
            sid = str(id).strip()
            url = self._fetch_source(sid)
            if not (url and url.startswith('http')):
                # 域名可能刚切换过, 强制重新探活再试一次
                self._base = self._resolve_base(force=True)
                url = self._fetch_source(sid)
            if url and url.startswith('http'):
                result['url'] = url.strip()
                return result
        except Exception as e:  # noqa: BLE001
            print(f'playerContent error: {e}')
        result['parse'] = 1
        return result

    # ==============================================================
    #  搜索
    # ==============================================================
    def searchContent(self, key, quick, pg='1'):
        result = {'list': [], 'parse': 0, 'jx': 0}
        page = int(pg) if str(pg or '').isdigit() else 1
        base = self._base_url()
        try:
            if page <= 1:
                html = self._get(f'{base}/search?wd={quote(str(key))}')
                vods = self._parse_search_cards(html)
            else:
                data = self._get_json(f'{base}/search?wd={quote(str(key))}&page={page}')
                vods = [self._json_item(x) for x in (data or {}).get('list', [])]
            result['list'] = [v for v in vods if v]
        except Exception as e:  # noqa: BLE001
            print(f'searchContent error: {e}')
        n = len(result['list'])
        result.update(page=page, pagecount=page + 1 if n else page,
                      limit=n, total=n)
        return result

    def searchContentPage(self, key, quick, pg='1'):
        return self.searchContent(key, quick, pg)

    # ==============================================================
    #  二级筛选配置
    # ==============================================================
    def _build_filters(self):
        filters = {}
        for _, tid in CLASS_LIST:
            values = []
            values.append({'key': 'type', 'name': '类型',
                           'value': self._kv([('全部', '0')], _TYPE_MAPS[tid])})
            values.append({'key': 'area', 'name': '地区',
                           'value': self._kv([('全部', '0')], _AREA_MAPS[tid])})
            values.append({'key': 'year', 'name': '年份',
                           'value': [{'n': n, 'v': v} for n, v in _YEAR_OPTIONS]})
            values.append({'key': 'sort', 'name': '排序',
                           'value': [{'n': n, 'v': v} for n, v in _SORT_OPTIONS]})
            filters[tid] = values
        return filters

    @staticmethod
    def _kv(head, mapping):
        out = [{'n': n, 'v': v} for n, v in head]
        out += [{'n': n, 'v': v} for n, v in mapping.items()]
        return out

    @staticmethod
    def _ext_string(extend):
        """把壳子回传的 extend 统一成站点筛选段 'type-area-year-sort'"""
        if extend:
            s = extend.strip() if isinstance(extend, str) else ''
            if s and re.fullmatch(r'[\d]+(?:-[\d]+){0,3}', s):
                parts = (s.split('-') + ['0', '0', '0', '0'])[:4]
                return '-'.join(parts)
            d = {}
            if isinstance(extend, dict):
                d = extend
            elif s.startswith('{'):
                try:
                    d = json.loads(s)
                except Exception:  # noqa: BLE001
                    d = {}
            if d:
                return '-'.join(str(d.get(k, '0') or '0')
                                for k in ('type', 'area', 'year', 'sort'))
        return '0-0-0-0'

    # ==============================================================
    #  HTTP 辅助
    # ==============================================================
    def _get(self, url):
        r = self.session.get(url, timeout=HTTP_TIMEOUT, verify=False)
        r.encoding = 'utf-8'
        return r.text

    def _get_json(self, url):
        try:
            r = self.session.get(url, timeout=HTTP_TIMEOUT, verify=False)
            return r.json()
        except Exception:  # noqa: BLE001
            return None

    def _fetch_source(self, sid):
        """POST /source/ 直取 m3u8 播放直链"""
        r = self.session.post(f'{self._base_url()}/source/', data={'id': sid},
                              timeout=HTTP_TIMEOUT, verify=False)
        return (r.text or '').strip()

    # ==============================================================
    #  解析: 列表卡片 (首页 / 筛选页 HTML)
    # ==============================================================
    _CARD_ANCHOR = re.compile(r'<div class="flex flex-col items-center desktop-hover-effect">')
    _CARD_HREF = re.compile(r'href="/(film|tvseries|varietyshow|anime|shortplay)/(\d+)"')
    _CARD_IMG = re.compile(r'src="(https?://[^"]+)"')
    _CARD_TITLE = re.compile(r'font-bold">([^<]+)<')
    _CARD_NOTE = re.compile(r'class="text-xs truncate">\s*([^<]+?)\s*<')

    def _parse_grid_cards(self, html):
        cards, seen = [], set()
        marks = list(self._CARD_ANCHOR.finditer(html))
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else min(len(html), m.start() + 9000)
            seg = html[m.start():end]
            href = self._CARD_HREF.search(seg)
            if not href:
                continue
            vid = f'{href.group(1)}-{href.group(2)}'
            if vid in seen:
                continue
            title = self._CARD_TITLE.search(seg)
            if not title:
                continue
            pic = self._CARD_IMG.search(seg)
            note = self._CARD_NOTE.search(seg)
            seen.add(vid)
            cards.append({
                'vod_id': vid,
                'vod_name': title.group(1).strip(),
                'vod_pic': pic.group(1).strip() if pic else '',
                'vod_remarks': note.group(1).strip() if note else '',
            })
        return cards

    # ==============================================================
    #  解析: 搜索结果卡片 (HTML)
    # ==============================================================
    _SEARCH_ANCHOR = re.compile(
        r'<div class="col-lg-6 col-md-12 mt-2 mb-2 cursor-pointer"><a\s+'
        r'href="/(film|tvseries|varietyshow|anime|shortplay)/(\d+)"')
    _SEARCH_TITLE = re.compile(r'<div class="topic-details-title">([^<]*)</div>')
    _SEARCH_IMG = re.compile(r'<img[^>]*src="(https?://[^"]+)"')
    _SEARCH_META = re.compile(
        r'class="topic-details-title-sub text-secondary "\s*>\s*(\d{4}[^<]{0,80})')

    def _parse_search_cards(self, html):
        cards, seen = [], set()
        marks = list(self._SEARCH_ANCHOR.finditer(html))
        for i, m in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else min(len(html), m.start() + 9000)
            seg = html[m.start():end]
            vid = f'{m.group(1)}-{m.group(2)}'
            if vid in seen:
                continue
            title = self._SEARCH_TITLE.search(seg)
            if not title or not title.group(1).strip():
                continue
            pic = self._SEARCH_IMG.search(seg)
            meta = self._SEARCH_META.search(seg)
            remarks = re.sub(r'\s+', ' ', meta.group(1)).strip() if meta else ''
            seen.add(vid)
            cards.append({
                'vod_id': vid,
                'vod_name': title.group(1).strip(),
                'vod_pic': pic.group(1).strip() if pic else '',
                'vod_remarks': remarks,
            })
        return cards

    # ==============================================================
    #  解析: 站点 JSON 列表项 (筛选/搜索第 2 页起)
    # ==============================================================
    @staticmethod
    def _json_item(x):
        url = str(x.get('url') or '')
        m = re.search(r'/(film|tvseries|varietyshow|anime|shortplay)/(\d+)', url)
        if not m:
            return None
        title = str(x.get('title') or '').strip()
        if not title:
            return None
        note = str(x.get('note') or '').strip()
        if not note:
            year = str(x.get('year') or '').strip()
            label = str(x.get('label') or '').strip()
            note = f'{year} {label}'.strip()
        return {
            'vod_id': f'{m.group(1)}-{m.group(2)}',
            'vod_name': title,
            'vod_pic': str(x.get('image') or '').strip(),
            'vod_remarks': note,
        }

    # ==============================================================
    #  解析: 详情页
    # ==============================================================
    _VAR_RE = re.compile(r"var (\w+) = ('(?:[^'\\]|\\.)*'|[-\d.]+|true|false|null)\s*;")
    _URLLIST_RE = re.compile(r"var urlList = (\{.*?\})\s*;\s*\n", re.S)

    def _parse_detail(self, html, vid):
        # vid 形如 'film-133049' 或 '133049'; 详情接口只要数字 id
        raw = str(vid).split('-')[-1]
        fields = {}
        for m in self._VAR_RE.finditer(html):
            name, raw = m.group(1), m.group(2)
            if raw.startswith("'"):
                try:
                    fields[name] = ast.literal_eval(raw)
                except Exception:  # noqa: BLE001
                    fields[name] = raw[1:-1]
            else:
                fields[name] = raw
        if not fields.get('title'):
            return None

        play_from, play_url = [], []
        m = self._URLLIST_RE.search(html)
        if m:
            try:
                data = ast.literal_eval(m.group(1))
                for i, line in enumerate(data.get('source') or []):
                    eps = (data.get('url_list') or [])[i] if i < len(data.get('url_list') or []) else []
                    if not eps:
                        continue
                    # TVBox 标准格式: 每集为 集名$sid , 集与集用 # 连接, 线路间用 $$$ 连接
                    eps_parts = []
                    for e in eps:
                        ep_name = str(e.get('title') or '').strip().replace('$', ' ').replace('#', ' ')
                        ep_sid = str(e.get('sid') or '').strip()
                        if ep_name and ep_sid:
                            eps_parts.append(f'{ep_name}${ep_sid}')
                    if not eps_parts:
                        continue
                    play_from.append(str(line))
                    play_url.append('#'.join(eps_parts))
            except Exception as e:  # noqa: BLE001
                print(f'parse urlList error: {e}')

        eps = fields.get('note') or ''
        return {
            'vod_id': str(raw),
            'vod_name': fields.get('title', ''),
            'vod_pic': fields.get('pic', ''),
            'type_name': fields.get('label', ''),
            'vod_year': fields.get('year', ''),
            'vod_area': fields.get('area', ''),
            'vod_remarks': eps,
            'vod_actor': fields.get('actor', ''),
            'vod_director': fields.get('director', ''),
            'vod_content': fields.get('description', ''),
            'vod_play_from': '$$$'.join(play_from),
            'vod_play_url': '$$$'.join(play_url),
        }
