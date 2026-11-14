# -*- coding: utf-8 -*-
"""
极速追剧 - jisuzhuiju.com
自定义 PHP 站（nginx，非 MacCMS），服务端渲染 + 单独的播放解析接口，无签名。

关键结论（已实测验证）：
1. 首页 / ：卡片 .vod-card，内 <a href="/detail/{id}.html"> + <img src="封面" alt="片名封面图片">
   + p.vod-title 片名 + p.vod-subtitle 主演。
2. 分类 /type/{slug}.html（slug：dianying 电影 / dianshiju 电视剧 / dongman 动漫 / zongyi 综艺）。
   分类页为若干「榜单板块」（如电视剧页含 最热国产剧/最热日韩剧/最热欧美剧/最热港台剧，
   共约 48~96 张卡片），**站点无传统翻页参数**（?page= 无效），一次性渲染。
3. 详情 /detail/{id}.html ：
   - 结构化数据 <script type="application/ld+json"> 的 @graph 内 @type=Movie，
     含 name/description/image/datePublished(年份)/genre/actor/director/countryOfOrigin。
   - HTML 侧：h1.detail-title、span.tag-pill(类型)、div.meta-item>span.meta-label+span.meta-value
     （别名/主演/导演/地区/年份）。
   - 线路：button.source-tab[data-target=source-N]>线路名；对应
     div.source-panel#source-N 内 div.episode-grid > a.episode-btn[href=/vodplay/{id}-{playFrom}-{index}.html]
     （playFrom 为线路代码，index 从 1 开始；同一 panel 内 playFrom 一致）。
4. 播放（核心）：**服务端解析接口**（前端不持有原始地址）
   GET /api/play-url?vodId={id}&playFrom={code}&index={n}
   → JSON {"mode":"native","code":200,"url":"https://.../index.m3u8"}；url 即真实直链，
   实测 200 application/vnd.apple.mpegurl，可直连播放（mode 也可能为 iframe，此时 url 为网页）。
5. 搜索 /search?keyword={关键词}（**参数名是 keyword**；传 wd/name 无效）。
"""
import re
import json
import time
import requests
from urllib.parse import quote

try:
    from base.spider import Spider
except ImportError:
    class Spider:
        def __init__(self):
            pass


class Spider(Spider):
    # ==================== 基础配置 ====================
    name = "极速追剧"
    base_url = "https://jisuzhuiju.com"
    site_url = "https://jisuzhuiju.com"

    searchable = 1
    quickSearch = 1
    filterable = 1
    changeable = 1

    class_name = ["电影", "电视剧", "动漫", "综艺"]
    class_url = ["dianying", "dianshiju", "dongman", "zongyi"]
    CATEGORY_NAMES = {
        "dianying": "电影",
        "dianshiju": "电视剧",
        "dongman": "动漫",
        "zongyi": "综艺",
    }

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9",
        "Referer": "https://jisuzhuiju.com/",
        "Connection": "keep-alive",
    }

    play_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://jisuzhuiju.com/",
        "Accept": "*/*",
    }

    def __init__(self):
        super().__init__()
        self._session = requests.Session()
        self._session.headers.update(self.headers)
        self._play_cache = {}
        self._cache_ttl = 1800
        self._last_req_time = 0
        self._min_req_interval = 0.8

    # ==================== 工具方法 ====================
    def _log(self, msg):
        print("[%s] %s" % (self.name, msg))

    def _clean_html(self, text):
        if not text:
            return ""
        text = re.sub(r'<br\s*/?>', ' ', text)
        text = re.sub(r'<[^>]+>', '', text)
        text = text.replace('&nbsp;', ' ').replace('&amp;', '&')
        text = re.sub(r'\s+', ' ', text).strip()
        return text

    def _fix_pic(self, pic):
        if not pic:
            return ""
        pic = pic.strip()
        if pic.startswith("//"):
            return "https:" + pic
        if pic.startswith("/"):
            return self.base_url + pic
        return pic

    # ==================== 请求基础 ====================
    def _apply_req_delay(self):
        now = time.time()
        elapsed = now - self._last_req_time
        if 0 < elapsed < self._min_req_interval:
            time.sleep(self._min_req_interval - elapsed)
        self._last_req_time = time.time()

    def fetch(self, url, headers=None, timeout=30):
        self._apply_req_delay()
        return self._session.get(url, headers=headers or {}, timeout=timeout)

    def _is_bad_page(self, html):
        if not html:
            return True
        if '404 - 页面没找到' in html or 'System Error' in html:
            return True
        return False

    def _get(self, url, max_retry=3, timeout=15):
        try:
            for attempt in range(max_retry):
                resp = self.fetch(url, headers=self.headers, timeout=timeout)
                html = resp.text
                if self._is_bad_page(html):
                    self._log("页面异常(%s)，重试 %s" % (resp.status_code, url))
                    if attempt < max_retry - 1:
                        time.sleep(1 + attempt)
                        continue
                    return ""
                return html
            return ""
        except Exception as e:
            self._log("请求失败: %s, %s" % (url, e))
            return ""

    # ==================== 列表解析 ====================
    def _parse_video_list(self, html):
        """解析 .vod-card 卡片（首页/分类/搜索通用）"""
        videos = []
        if not html:
            return videos
        for m in re.finditer(r'<a[^>]*href="(/detail/(\d+)\.html)"[^>]*>(.*?)</a>', html, re.DOTALL):
            vod_id = m.group(2)
            block = m.group(3)
            im = re.search(r'<img[^>]*src="([^"]+)"[^>]*alt="([^"]*)"', block)
            if not im:
                im = re.search(r'<img[^>]*alt="([^"]*)"[^>]*src="([^"]+)"', block)
                if im:
                    alt, pic = im.group(1), im.group(2)
                else:
                    continue
            else:
                pic, alt = im.group(1), im.group(2)
            title = alt.replace("封面图片", "").strip()
            tm = re.search(r'class="vod-title[^"]*"[^>]*>([^<]*)<', block)
            if tm and tm.group(1).strip():
                title = tm.group(1).strip()
            sm = re.search(r'class="vod-subtitle[^"]*"[^>]*>([^<]*)<', block)
            note = sm.group(1).strip() if sm else ""
            if not title:
                continue
            videos.append({
                "vod_id": vod_id,
                "vod_name": title,
                "vod_pic": self._fix_pic(pic),
                "vod_remarks": note,
            })
        # 去重（同一 id 多板块出现）
        seen = set()
        dedup = []
        for v in videos:
            if v["vod_id"] in seen:
                continue
            seen.add(v["vod_id"])
            dedup.append(v)
        return dedup

    # ==================== 播放源解析（详情页） ====================
    def _parse_play_sources(self, html, vod_id):
        """线路 source-tab / source-panel 一一对应；集数 a.episode-btn"""
        sources = []
        if not html:
            return sources
        # 线路名按 data-target 顺序
        tab_map = {}
        for m in re.finditer(r'data-target="(source-\d+)"[^>]*>([^<]*)<', html):
            tab_map[m.group(1)] = self._clean_html(m.group(2))
        pos_list = [(m.start(), m.group(1)) for m in
                    re.finditer(r'<div[^>]*class="source-panel"[^>]*id="(source-\d+)"', html)]
        positions = pos_list
        for k, (pos, pid) in enumerate(positions):
            end = positions[k + 1][0] if k + 1 < len(positions) else len(html)
            body = html[pos:end]
            eps = re.findall(
                r'href="/vodplay/%s-([A-Za-z0-9]+)-(\d+)\.html"[^>]*>([^<]*)</a>' % re.escape(str(vod_id)),
                body, re.DOTALL)
            if not eps:
                continue
            play_from = eps[0][0]
            name = tab_map.get(pid, "线路%s" % (k + 1))
            ep_list = []
            for _pf, index, title in sorted(eps, key=lambda x: int(x[1])):
                ep_name = self._clean_html(title) or ("第%s集" % index)
                ep_list.append({"name": ep_name, "link": "%s-%s-%s" % (vod_id, play_from, index)})
            if ep_list:
                sources.append({"source_name": name, "episodes": ep_list})
        return sources

    # ==================== 播放地址解析 ====================
    def _get_play_url(self, vod_id, play_from, index):
        cache_key = "%s-%s-%s" % (vod_id, play_from, index)
        now = time.time()
        if cache_key in self._play_cache:
            url, ts = self._play_cache[cache_key]
            if now - ts < self._cache_ttl:
                return url
        api = "%s/api/play-url?vodId=%s&playFrom=%s&index=%s" % (
            self.base_url, quote(str(vod_id)), quote(str(play_from)), quote(str(index)))
        try:
            resp = self.fetch(api, headers=self.headers, timeout=15)
            data = resp.json()
            url = (data.get("url") or "").strip()
            if url:
                self._log("播放直链: %s" % url[:90])
                self._play_cache[cache_key] = (url, now)
                return url
            self._log("接口无播放地址: %s" % str(data)[:120])
        except Exception as e:
            self._log("获取播放地址异常: %s" % e)
        return ""

    # ==================== TVBox 核心方法 ====================
    def init(self, extend=''):
        self._log("初始化完成")

    def homeContent(self, filter=False):
        result = {
            "class": [
                {"type_id": tid, "type_name": name}
                for tid, name in self.CATEGORY_NAMES.items()
            ]
        }
        if filter:
            result["filters"] = {}
            result["filter"] = {}
        return result

    def homeVideoContent(self):
        try:
            html = self._get(self.base_url)
            if not html:
                return {"list": []}
            videos = self._parse_video_list(html)
            return {"list": videos[:30]}
        except Exception as e:
            self._log("homeVideoContent异常: %s" % e)
            return {"list": []}

    def categoryContent(self, tid, pg, filter=False, content=None):
        try:
            pg = max(1, int(pg or 1))
            if str(tid) not in self.CATEGORY_NAMES:
                return {"list": [], "page": pg, "pagecount": 1, "limit": 60, "total": 0}
            # 站点无翻页参数，仅在首页返回数据
            if pg > 1:
                return {"list": [], "page": pg, "pagecount": 1, "limit": 60, "total": 0}
            url = "%s/type/%s.html" % (self.base_url, tid)
            self._log("分类请求: " + url)
            html = self._get(url)
            if not html:
                return {"list": [], "page": pg, "pagecount": 1, "limit": 60, "total": 0}
            videos = self._parse_video_list(html)
            return {
                "list": videos,
                "page": pg,
                "pagecount": 1,
                "limit": 60,
                "total": len(videos),
            }
        except Exception as e:
            self._log("categoryContent异常: %s" % e)
            return {"list": [], "page": pg, "pagecount": 1, "limit": 60, "total": 0}

    def searchContent(self, key, quick=False, pg="1"):
        try:
            try:
                pg = max(1, int(pg or 1))
            except (TypeError, ValueError):
                pg = 1
            keyword = str(key or "").strip()
            if not keyword:
                return {"page": pg, "pagecount": 1, "limit": 0, "total": 0, "list": []}
            url = "%s/search?keyword=%s" % (self.base_url, quote(keyword))
            self._log("搜索请求: %s quick=%s" % (url, quick))
            html = self._get(url)
            if not html:
                return {"list": [], "page": pg, "pagecount": 1, "limit": 30, "total": 0}
            videos = self._parse_video_list(html)
            if not quick:
                key_lower = keyword.lower()

                def _score(v):
                    name = v.get('vod_name', '').lower()
                    if name == key_lower:
                        return 0
                    if name.startswith(key_lower):
                        return 1
                    if key_lower in name:
                        return 2
                    return 3
                videos = sorted(videos, key=_score)
            return {
                "list": videos,
                "page": pg,
                "pagecount": 9999,
                "limit": 30,
                "total": 999999,
            }
        except Exception as e:
            self._log("searchContent异常: %s" % e)
            return {"list": [], "page": pg, "pagecount": 1, "limit": 30, "total": 0}

    def detailContent(self, ids):
        try:
            vod_id = ids[0] if isinstance(ids, list) else str(ids)
            url = "%s/detail/%s.html" % (self.base_url, vod_id)
            self._log("详情请求: " + url)
            html = self._get(url)
            if not html:
                return {"list": []}

            vod_name = ""
            vod_pic = ""
            vod_year = vod_area = vod_actor = vod_director = vod_content = ""
            vod_remarks = ""

            # ld+json @graph 内的 Movie
            m = re.search(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL)
            if m:
                try:
                    data = json.loads(m.group(1).strip())
                    graph = data.get("@graph", []) if isinstance(data, dict) else []
                    movie = None
                    for obj in graph:
                        if obj.get("@type") == "Movie":
                            movie = obj
                            break
                    if not movie and isinstance(data, dict) and data.get("@type") == "Movie":
                        movie = data
                    if movie:
                        vod_name = movie.get("name", "") or ""
                        vod_pic = self._fix_pic(movie.get("image", "") or "")
                        vod_year = str(movie.get("datePublished", "") or "")[:4]
                        vod_actor = movie.get("actor", "") or ""
                        vod_director = movie.get("director", "") or ""
                        genre = movie.get("genre", "") or ""
                        vod_remarks = self._clean_html(genre)
                        vod_content = self._clean_html(movie.get("description", "") or "")
                        coo = movie.get("countryOfOrigin", {})
                        if isinstance(coo, dict):
                            vod_area = coo.get("name", "") or ""
                except Exception as e:
                    self._log("ld+json 解析失败: %s" % e)

            # HTML 侧兜底
            if not vod_name:
                tm = re.search(r'<h1[^>]*class="[^"]*detail-title[^"]*"[^>]*>([^<]*)<', html)
                if tm:
                    vod_name = self._clean_html(tm.group(1))
            if not vod_name:
                tm = re.search(r'<title>《?([^》<]*)》?', html)
                if tm:
                    vod_name = self._clean_html(tm.group(1))
            if not vod_pic:
                pm = re.search(r'<img[^>]*src="([^"]+)"[^>]*alt="[^"]*封面', html)
                if pm:
                    vod_pic = self._fix_pic(pm.group(1))

            def _meta(label):
                mm = re.search(r'<span class="meta-label">\s*%s\s*</span>\s*<span class="meta-value">(.*?)</span>' % re.escape(label), html, re.DOTALL)
                return self._clean_html(mm.group(1)) if mm else ""
            vod_actor = vod_actor or _meta("主演：")
            vod_director = vod_director or _meta("导演：")
            vod_year = vod_year or _meta("年份：")
            vod_area = vod_area or _meta("地区：")
            if not vod_content:
                dm = re.search(r'<meta name="description" content="([^"]*)"', html)
                if dm:
                    vod_content = self._clean_html(dm.group(1))

            sources = self._parse_play_sources(html, vod_id)
            if not sources:
                self._log("未能解析到播放源")
                return {"list": []}

            from_list = []
            url_list = []
            for src in sources:
                from_list.append(src["source_name"])
                url_list.append("#".join(
                    "%s$%s" % (ep["name"], ep["link"]) for ep in src["episodes"]))

            video = {
                "vod_id": vod_id,
                "vod_name": vod_name or "未知",
                "vod_pic": vod_pic,
                "vod_year": vod_year,
                "vod_area": vod_area,
                "vod_actor": vod_actor,
                "vod_director": vod_director,
                "vod_content": vod_content,
                "vod_remarks": vod_remarks,
                "vod_play_from": "$$$".join(from_list),
                "vod_play_url": "$$$".join(url_list),
            }
            self._log("详情解析成功: %s 线路: %s" % (video["vod_name"], "$$$".join(from_list)))
            return {"list": [video]}
        except Exception as e:
            self._log("detailContent异常: %s" % e)
            return {"list": []}

    def playerContent(self, flag, id, vipFlags=None):
        try:
            parts = str(id).split("-")
            if len(parts) < 3:
                return {"parse": 0, "url": "", "header": ""}
            vod_id, play_from, index = parts[0], parts[1], parts[2]
            play_url = self._get_play_url(vod_id, play_from, index)
            if not play_url:
                return {"parse": 0, "url": "", "header": ""}
            is_direct = bool(re.search(r'\.(m3u8|mp4|flv|ts|mkv)([?#&]|$)', play_url, re.I))
            if is_direct:
                return {"parse": 0, "url": play_url, "header": self.play_headers.copy()}
            # 非直链（如 iframe 网页）
            return {"parse": 1, "url": play_url, "header": ""}
        except Exception as e:
            self._log("playerContent异常: %s" % e)
            return {"parse": 0, "url": "", "header": ""}

    def getName(self):
        return self.name

    def isVideoFormat(self, url):
        pass

    def manualVideoCheck(self):
        pass

    def destroy(self):
        pass

    def localProxy(self, param):
        pass
