import requests
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
import re
from collections import defaultdict

# ---------- 配置 ----------
SEARCH_URL = "https://725998.com/keywords.html"   # 搜索提交地址
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://725998.com/",
    "Content-Type": "application/x-www-form-urlencoded",
}
TIMEOUT = 10

# ---------- 工具函数 ----------
def get_domain(url):
    """提取网址的域名（用于归类）"""
    parsed = urlparse(url)
    return parsed.netloc

def is_playable_url(url):
    """初步判断是否为可播放链接（m3u8/mp4/flv）"""
    return re.search(r'\.(m3u8|mp4|flv)(\?|$)', url, re.I) is not None

def resolve_redirect(url):
    """尝试跟随跳转，获取最终地址（若为302跳转）"""
    try:
        resp = requests.head(url, headers=HEADERS, allow_redirects=True, timeout=5)
        return resp.url
    except:
        return url

# ---------- 核心搜索与归类 ----------
def search_and_classify(keyword):
    """
    搜索影片，返回按域名归类的播放链接列表
    返回格式: {
        "keyword": "影片名",
        "sources": {
            "域名1": ["url1", "url2"],
            "域名2": ["url3"]
        }
    }
    """
    # 1. 发送搜索请求
    try:
        resp = requests.post(SEARCH_URL, data={"keywords": keyword}, headers=HEADERS, timeout=TIMEOUT)
        resp.encoding = "utf-8"
        resp.raise_for_status()
    except Exception as e:
        return {"error": f"搜索请求失败: {e}"}

    soup = BeautifulSoup(resp.text, "html.parser")
    
    # 2. 提取所有外部链接（非725998.com）
    external_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("http") and "725998.com" not in href:
            # 过滤掉明显不是播放链接的（如广告、分享等）
            if any(k in href.lower() for k in ["play", "video", "watch", "v.", "m3u8", "mp4", "flv", "vod"]):
                external_links.append(href)
    
    # 3. 去重并归类
    classified = defaultdict(list)
    for link in external_links:
        # 尝试解析跳转（有些链接是302到真正播放页）
        final_url = resolve_redirect(link)
        domain = get_domain(final_url)
        # 如果已经是可播放直链，直接加入；否则保留原链接（可能是个播放页）
        if is_playable_url(final_url):
            classified[domain].append(final_url)
        else:
            # 若不是直链，也保留，后续可再次解析
            classified[domain].append(link)
    
    # 4. 如果分类结果为空，尝试从 script 中直接提取 m3u8/mp4（备用）
    if not classified:
        scripts = soup.find_all("script")
        for script in scripts:
            if script.string:
                matches = re.findall(r'["\'](https?://[^"\']+\.(?:m3u8|mp4|flv)[^"\']*)["\']', script.string)
                for m in matches:
                    domain = get_domain(m)
                    classified[domain].append(m)
    
    # 5. 整理返回数据
    result = {
        "keyword": keyword,
        "sources": dict(classified)
    }
    return result

# ---------- 适配影视壳软件的标准接口 ----------
def get_play_info(movie_name):
    """
    影视壳软件调用此函数，返回标准 JSON
    """
    data = search_and_classify(movie_name)
    if "error" in data:
        return {"code": 0, "msg": data["error"], "data": None}
    
    # 构造播放源列表
    source_list = []
    for domain, urls in data["sources"].items():
        # 每个源取第一个链接（影视壳通常只取一个，但可扩展）
        source_list.append({
            "source": domain,
            "url": urls[0] if urls else "",
            "all_urls": urls   # 保留全部以备后用
        })
    
    return {
        "code": 1,
        "msg": "success",
        "data": {
            "name": movie_name,
            "sources": source_list
        }
    }

# ---------- 测试 ----------
if __name__ == "__main__":
    test_keyword = "流浪地球"
    result = get_play_info(test_keyword)
    import json
    print(json.dumps(result, indent=2, ensure_ascii=False))