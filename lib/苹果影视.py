#coding=utf-8
#!/usr/bin/python
"""
TVBox 苹果影视(aptv.tv) py 源

站点:     https://aptv.tv/cinema
接口域:   https://api-preview.toulaopao.cc/api   (JS 内含多个容灾域名, 失效时替换)
协议:     POST /web/v1/tv/call, 请求/响应均为 AES-256-CBC + PKCS7 + base64 文本
签名:     请求头 X-Post-Signature = base64(HMAC-SHA256(密文, HMAC_KEY))
游客:     播放前自动 POST /web/user/register/temp 注册临时账号, 换取 JWT accessToken
图片:     封面为 TVIMAGE1 v3 信封(AES-256-GCM + wrapped-key XOR 派生命钥),
          经 localProxy 解密后返回给壳
播放:     playback.session 换取带签名的 m3u8 manifest_url, 直链可拉流

配置 JSON 挂载(站点配置):
{
  "key": "aptv",
  "name": "苹果影视〔PY〕",
  "type": 3,
  "api": "py_aptv",
  "searchable": 1,
  "quickSearch": 1,
  "filterable": 0,
  "ext": "./plugin/aptv.py"
}
"""

import sys
import json
import re
import time
import uuid
import base64
import hashlib
import hmac
from urllib.parse import quote, unquote

sys.path.append('..')

# ---------- 依赖探测 ----------
try:
    import requests as _requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    from Crypto.Cipher import AES as _CAES
    from Crypto.Util.Padding import pad as _cpad, unpad as _cunpad
    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False

# ---------- 站点常量(逆向自前端 CGHqAPWk.js) ----------
API_BASE = "https://api-preview.toulaopao.cc/api"
API_CALL = API_BASE + "/web/v1/tv/call"
API_REGISTER = API_BASE + "/web/user/register/temp"
AES_KEY = bytes.fromhex("11059a4c989dd9d4837778b013d164baa0ad4d7b0076e722369f6f085814293f")
AES_IV = bytes.fromhex("dd3ea319024c649d2ebcd1bda551a307")
HMAC_KEY = bytes.fromhex("51c5b55800a7efd1")
TVIMAGE_MAGIC = b"TVIMAGE1"
TVIMAGE_INFO = b"tv.image-envelope/v3/wrapped-key\0"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
REFERER = "https://aptv.tv/"
PROXY_PREFIX = "http://127.0.0.1:9978/proxy?url="

# ---------- 纯 Python AES-256-CBC 降级实现(环境无 pycryptodome 时使用) ----------
_SBOX = (
0x63,0x7c,0x77,0x7b,0xf2,0x6b,0x6f,0xc5,0x30,0x01,0x67,0x2b,0xfe,0xd7,0xab,0x76,
0xca,0x82,0xc9,0x7d,0xfa,0x59,0x47,0xf0,0xad,0xd4,0xa2,0xaf,0x9c,0xa4,0x72,0xc0,
0xb7,0xfd,0x93,0x26,0x36,0x3f,0xf7,0xcc,0x34,0xa5,0xe5,0xf1,0x71,0xd8,0x31,0x15,
0x04,0xc7,0x23,0xc3,0x18,0x96,0x05,0x9a,0x07,0x12,0x80,0xe2,0xeb,0x27,0xb2,0x75,
0x09,0x83,0x2c,0x1a,0x1b,0x6e,0x5a,0xa0,0x52,0x3b,0xd6,0xb3,0x29,0xe3,0x2f,0x84,
0x53,0xd1,0x00,0xed,0x20,0xfc,0xb1,0x5b,0x6a,0xcb,0xbe,0x39,0x4a,0x4c,0x58,0xcf,
0xd0,0xef,0xaa,0xfb,0x43,0x4d,0x33,0x85,0x45,0xf9,0x02,0x7f,0x50,0x3c,0x9f,0xa8,
0x51,0xa3,0x40,0x8f,0x92,0x9d,0x38,0xf5,0xbc,0xb6,0xda,0x21,0x10,0xff,0xf3,0xd2,
0xcd,0x0c,0x13,0xec,0x5f,0x97,0x44,0x17,0xc4,0xa7,0x7e,0x3d,0x64,0x5d,0x19,0x73,
0x60,0x81,0x4f,0xdc,0x22,0x2a,0x90,0x88,0x46,0xee,0xb8,0x14,0xde,0x5e,0x0b,0xdb,
0xe0,0x32,0x3a,0x0a,0x49,0x06,0x24,0x5c,0xc2,0xd3,0xac,0x62,0x91,0x95,0xe4,0x79,
0xe7,0xc8,0x37,0x6d,0x8d,0xd5,0x4e,0xa9,0x6c,0x56,0xf4,0xea,0x65,0x7a,0xae,0x08,
0xba,0x78,0x25,0x2e,0x1c,0xa6,0xb4,0xc6,0xe8,0xdd,0x74,0x1f,0x4b,0xbd,0x8b,0x8a,
0x70,0x3e,0xb5,0x66,0x48,0x03,0xf6,0x0e,0x61,0x35,0x57,0xb9,0x86,0xc1,0x1d,0x9e,
0xe1,0xf8,0x98,0x11,0x69,0xd9,0x8e,0x94,0x9b,0x1e,0x87,0xe9,0xce,0x55,0x28,0xdf,
0x8c,0xa1,0x89,0x0d,0xbf,0xe6,0x42,0x68,0x41,0x99,0x2d,0x0f,0xb0,0x54,0xbb,0x16)
_INV_SBOX = (
0x52,0x09,0x6a,0xd5,0x30,0x36,0xa5,0x38,0xbf,0x40,0xa3,0x9e,0x81,0xf3,0xd7,0xfb,
0x7c,0xe3,0x39,0x82,0x9b,0x2f,0xff,0x87,0x34,0x8e,0x43,0x44,0xc4,0xde,0xe9,0xcb,
0x54,0x7b,0x94,0x32,0xa6,0xc2,0x23,0x3d,0xee,0x4c,0x95,0x0b,0x42,0xfa,0xc3,0x4e,
0x08,0x2e,0xa1,0x66,0x28,0xd9,0x24,0xb2,0x76,0x5b,0xa2,0x49,0x6d,0x8b,0xd1,0x25,
0x72,0xf8,0xf6,0x64,0x86,0x68,0x98,0x16,0xd4,0xa4,0x5c,0xcc,0x5d,0x65,0xb6,0x92,
0x6c,0x70,0x48,0x50,0xfd,0xed,0xb9,0xda,0x5e,0x15,0x46,0x57,0xa7,0x8d,0x9d,0x84,
0x90,0xd8,0xab,0x00,0x8c,0xbc,0xd3,0x0a,0xf7,0xe4,0x58,0x05,0xb8,0xb3,0x45,0x06,
0xd0,0x2c,0x1e,0x8f,0xca,0x3f,0x0f,0x02,0xc1,0xaf,0xbd,0x03,0x01,0x13,0x8a,0x6b,
0x3a,0x91,0x11,0x41,0x4f,0x67,0xdc,0xea,0x97,0xf2,0xcf,0xce,0xf0,0xb4,0xe6,0x73,
0x96,0xac,0x74,0x22,0xe7,0xad,0x35,0x85,0xe2,0xf9,0x37,0xe8,0x1c,0x75,0xdf,0x6e,
0x47,0xf1,0x1a,0x71,0x1d,0x29,0xc5,0x89,0x6f,0xb7,0x62,0x0e,0xaa,0x18,0xbe,0x1b,
0xfc,0x56,0x3e,0x4b,0xc6,0xd2,0x79,0x20,0x9a,0xdb,0xc0,0xfe,0x78,0xcd,0x5a,0xf4,
0x1f,0xdd,0xa8,0x33,0x88,0x07,0xc7,0x31,0xb1,0x12,0x10,0x59,0x27,0x80,0xec,0x5f,
0x60,0x51,0x7f,0xa9,0x19,0xb5,0x4a,0x0d,0x2d,0xe5,0x7a,0x9f,0x93,0xc9,0x9c,0xef,
0xa0,0xe0,0x3b,0x4d,0xae,0x2a,0xf5,0xb0,0xc8,0xeb,0xbb,0x3c,0x83,0x53,0x99,0x61,
0x17,0x2b,0x04,0x7e,0xba,0x77,0xd6,0x26,0xe1,0x69,0x14,0x63,0x55,0x21,0x0c,0x7d)
_RCON = (0x01, 0x02, 0x04, 0x08, 0x10, 0x20, 0x40, 0x80, 0x1b, 0x36, 0x6c, 0xd8, 0xab, 0x4d, 0x9a)


def _xtime(b):
    return ((b << 1) ^ (0x1b if b & 0x80 else 0)) & 0xff


def _gmul(a, b):
    r = 0
    for _ in range(8):
        if b & 1:
            r ^= a
        b >>= 1
        a = _xtime(a)
    return r & 0xff


def _expand_key(key):
    """AES-256 密钥扩展, 输出 60 个 4 字节 word (FIPS-197)"""
    w = [list(key[i * 4:(i + 1) * 4]) for i in range(8)]
    for i in range(8, 60):
        temp = list(w[i - 1])
        if i % 8 == 0:
            temp = temp[1:] + temp[:1]
            temp = [_SBOX[b] for b in temp]
            temp[0] ^= _RCON[i // 8 - 1]
        elif i % 8 == 4:
            temp = [_SBOX[b] for b in temp]
        w.append([w[i - 8][j] ^ temp[j] for j in range(4)])
    return w


def _add_rk(state, w):
    for c in range(4):
        state[4 * c] ^= w[c][0]
        state[4 * c + 1] ^= w[c][1]
        state[4 * c + 2] ^= w[c][2]
        state[4 * c + 3] ^= w[c][3]


def _sub(state):
    for i in range(16):
        state[i] = _SBOX[state[i]]


def _inv_sub(state):
    for i in range(16):
        state[i] = _INV_SBOX[state[i]]


def _shift(state):
    """ShiftRows: 第 r 行循环左移 r 字节 (state 为 16 字节平面数组, 列主序 s[r][c]=b[r+4c])"""
    t = list(state)
    # row1 = {1,5,9,13} 左移1 -> {5,9,13,1}
    state[1], state[5], state[9], state[13] = t[5], t[9], t[13], t[1]
    # row2 = {2,6,10,14} 左移2 -> {10,14,2,6}
    state[2], state[6], state[10], state[14] = t[10], t[14], t[2], t[6]
    # row3 = {3,7,11,15} 左移3 -> {15,3,7,11}
    state[3], state[7], state[11], state[15] = t[15], t[3], t[7], t[11]


def _inv_shift(state):
    """InvShiftRows: 第 r 行循环右移 r 字节 (列主序)"""
    t = list(state)
    # row1 右移1: {13,1,5,9}
    state[1], state[5], state[9], state[13] = t[13], t[1], t[5], t[9]
    # row2 右移2(与左移2等效): {6,10,14,2}
    state[2], state[6], state[10], state[14] = t[10], t[14], t[2], t[6]
    # row3 右移3: {3,7,11,15} -> {7,11,15,3}
    state[3], state[7], state[11], state[15] = t[7], t[11], t[15], t[3]


def _mix(state):
    """MixColumns"""
    for c in range(4):
        b0, b1, b2, b3 = state[4 * c], state[4 * c + 1], state[4 * c + 2], state[4 * c + 3]
        state[4 * c] = _xtime(b0) ^ (_xtime(b1) ^ b1) ^ b2 ^ b3
        state[4 * c + 1] = b0 ^ _xtime(b1) ^ (_xtime(b2) ^ b2) ^ b3
        state[4 * c + 2] = b0 ^ b1 ^ _xtime(b2) ^ (_xtime(b3) ^ b3)
        state[4 * c + 3] = (_xtime(b0) ^ b0) ^ b1 ^ b2 ^ _xtime(b3)


def _inv_mix(state):
    """InvMixColumns"""
    for c in range(4):
        b0, b1, b2, b3 = state[4 * c], state[4 * c + 1], state[4 * c + 2], state[4 * c + 3]
        state[4 * c] = _gmul(b0, 14) ^ _gmul(b1, 11) ^ _gmul(b2, 13) ^ _gmul(b3, 9)
        state[4 * c + 1] = _gmul(b0, 9) ^ _gmul(b1, 14) ^ _gmul(b2, 11) ^ _gmul(b3, 13)
        state[4 * c + 2] = _gmul(b0, 13) ^ _gmul(b1, 9) ^ _gmul(b2, 14) ^ _gmul(b3, 11)
        state[4 * c + 3] = _gmul(b0, 11) ^ _gmul(b1, 13) ^ _gmul(b2, 9) ^ _gmul(b3, 14)


def _aes256_encrypt_block(block, w):
    state = list(block)
    _add_rk(state, w[0:4])
    for rnd in range(1, 14):
        _sub(state)
        _shift(state)
        _mix(state)
        _add_rk(state, w[4 * rnd:4 * rnd + 4])
    _sub(state)
    _shift(state)
    _add_rk(state, w[56:60])
    return bytes(state)


def _aes256_decrypt_block(block, w):
    state = list(block)
    _add_rk(state, w[56:60])
    for rnd in range(13, 0, -1):
        _inv_shift(state)
        _inv_sub(state)
        _add_rk(state, w[4 * rnd:4 * rnd + 4])
        _inv_mix(state)
    _inv_shift(state)
    _inv_sub(state)
    _add_rk(state, w[0:4])
    return bytes(state)


def _aes256_cbc_encrypt(plain, key, iv):
    w = _expand_key(key)
    prev = list(iv)
    out = bytearray()
    for i in range(0, len(plain), 16):
        blk = bytes(plain[i + j] ^ prev[j] for j in range(16))
        enc = _aes256_encrypt_block(blk, w)
        out += enc
        prev = list(enc)
    return bytes(out)


def _aes256_cbc_decrypt(cipher, key, iv):
    w = _expand_key(key)
    prev = list(iv)
    out = bytearray()
    for i in range(0, len(cipher), 16):
        blk = cipher[i:i + 16]
        dec = _aes256_decrypt_block(blk, w)
        out += bytes(dec[j] ^ prev[j] for j in range(16))
        prev = list(blk)
    return bytes(out)


def _pkcs7_pad(data):
    n = 16 - (len(data) % 16)
    return data + bytes([n]) * n


def _pkcs7_unpad(data):
    if not data:
        return data
    return data[:-data[-1]]


# ---------- 纯 Python AES-256-GCM 降级实现(图片信封解密用, 无 pycryptodome 时) ----------
_GCM_R = 0xE1000000000000000000000000000000


def _inc32(block):
    """GCM 计数器递增(仅低 32 位, mod 2^32)"""
    n = (int.from_bytes(block[12:16], "big") + 1) & 0xFFFFFFFF
    return block[:12] + n.to_bytes(4, "big")


def _gf128_mul(x, y):
    """GCM 所用 GF(2^128) 乘法(R = 0xE100...)"""
    z = 0
    v = y
    for i in range(128):
        if (x >> (127 - i)) & 1:
            z ^= v
        if v & 1:
            v = (v >> 1) ^ _GCM_R
        else:
            v >>= 1
    return z


def _pad16(b):
    r = len(b) % 16
    return b + b"\x00" * (16 - r) if r else b


def _ghash(h_int, aad, cipher):
    """GHASH: pad16(A) || pad16(C) || [len(A)]64 || [len(C)]64"""
    data = (_pad16(aad) + _pad16(cipher)
            + (len(aad) * 8).to_bytes(8, "big")
            + (len(cipher) * 8).to_bytes(8, "big"))
    y = 0
    for i in range(0, len(data), 16):
        y = _gf128_mul(y ^ int.from_bytes(data[i:i + 16], "big"), h_int)
    return y


def _j0_from_nonce(h_int, nonce):
    """派生 J0: 12 字节 nonce 走快路径, 其余按 GHASH 规范"""
    if len(nonce) == 12:
        return nonce + b"\x00\x00\x00\x01"
    l = len(nonce)
    block = _pad16(nonce) + b"\x00" * 8 + (l * 8).to_bytes(8, "big")
    y = 0
    for i in range(0, len(block), 16):
        y = _gf128_mul(y ^ int.from_bytes(block[i:i + 16], "big"), h_int)
    return y.to_bytes(16, "big")


def _aes256_gcm_decrypt(key, nonce, aad, data):
    """AES-256-GCM 解密 + 认证校验; data = ciphertext||tag(16); 认证失败抛 ValueError"""
    if len(data) < 16:
        raise ValueError("gcm data too short")
    cipher, tag = data[:-16], data[-16:]
    w = _expand_key(key)
    enc = lambda b: bytes(_aes256_encrypt_block(b, w))
    h_int = int.from_bytes(enc(bytes(16)), "big")
    j0 = _j0_from_nonce(h_int, nonce)
    s_int = _ghash(h_int, aad, cipher)
    expect = bytes(a ^ b for a, b in zip(s_int.to_bytes(16, "big"), enc(j0)))
    if expect != tag:
        raise ValueError("gcm tag mismatch")
    out = bytearray()
    cb = _inc32(j0)
    for i in range(0, len(cipher), 16):
        ks = enc(cb)
        out += bytes(a ^ b for a, b in zip(cipher[i:i + 16], ks))
        cb = _inc32(cb)
    return bytes(out)


def _sniff_image_mime(b):
    """按魔数识别常见图片类型, 非图片返回 None"""
    if b[:2] == b"\xff\xd8":
        return "image/jpeg"
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    return None


# ---------- Spider 类 ----------
try:
    from base.spider import Spider as _BaseSpider
except ImportError:
    class _BaseSpider(object):
        def __init__(self):
            pass

        def getDependence(self):
            return []

        def init(self, extend=""):
            pass

        def destroy(self):
            return None

        def action(self, action):
            return {}

        def isVideoFormat(self, url):
            return False

        def manualVideoCheck(self):
            return False


class Spider(_BaseSpider):
    """苹果影视 aptv.tv py 源"""

    config = {
        "player": {},
        "filter": {}
    }

    # ========== 基础 ==========
    def getName(self):
        return "苹果影视"

    def init(self, extend=""):
        self.api_base = API_BASE
        self.api_call = API_CALL
        self.api_register = API_REGISTER
        self.ua = UA
        # extend 可能为 JSON 字符串(可覆盖域名等)
        self.ext = {}
        if isinstance(extend, str) and extend.strip().startswith("{"):
            try:
                self.ext = json.loads(extend)
            except Exception:
                self.ext = {}
        override = (self.ext or {}).get("api_base")
        if isinstance(override, str) and override.strip():
            self.api_base = override.rstrip("/")
            self.api_call = self.api_base + "/web/v1/tv/call"
            self.api_register = self.api_base + "/web/user/register/temp"
        self._token = None
        self._token_expire = 0
        self._cat_cursors = {}   # tid -> [None, cursor1, ...]
        self._main_coll = {}     # tid -> collection_id
        self._tab_cache = {}     # tid -> (ts, modules)
        self._tab_ttl = 300

    def getDependence(self):
        return []

    def destroy(self):
        return None

    # ========== 加密 / 请求 ==========
    def _encrypt(self, obj):
        text = json.dumps(obj, separators=(",", ":")).encode("utf-8")
        text = _pkcs7_pad(text)
        if HAS_CRYPTO:
            ct = _CAES.new(AES_KEY, _CAES.MODE_CBC, AES_IV).encrypt(text)
        else:
            ct = _aes256_cbc_encrypt(text, AES_KEY, AES_IV)
        return base64.b64encode(ct).decode()

    def _decrypt(self, b64text):
        ct = base64.b64decode(b64text)
        if HAS_CRYPTO:
            pt = _CAES.new(AES_KEY, _CAES.MODE_CBC, AES_IV).decrypt(ct)
        else:
            pt = _aes256_cbc_decrypt(ct, AES_KEY, AES_IV)
        pt = _pkcs7_unpad(pt)
        return json.loads(pt.decode("utf-8", errors="replace"))

    def _sign(self, b64text):
        return base64.b64encode(hmac.new(HMAC_KEY, b64text.encode(), hashlib.sha256).digest()).decode()

    def _http_post(self, url, data, headers, timeout=20):
        if HAS_REQUESTS:
            r = _requests.post(url, data=data, headers=headers, timeout=timeout)
            return r.status_code, r.text
        import urllib.request
        req = urllib.request.Request(url, data=data.encode(), headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.status, resp.read().decode("utf-8", errors="replace")
        except Exception:
            return 0, ""

    def _post(self, operation, params=None, query=None, payload=None, token=None,
              path=None, idempotency_key=None):
        params = params if params is not None else {}
        query = query if query is not None else {}
        body = {
            "device_id": str(uuid.uuid4()),
            "brand": "BROWSER",
            "model": "UNKNOWN",
            "appVer": "-1",
            "osVer": "1",
            "timestamp": int(time.time()),
            "operation": operation,
            "params": params,
            "query": query,
        }
        if payload is not None:
            body["payload"] = payload
        if idempotency_key:
            body["idempotency_key"] = idempotency_key
        b64 = self._encrypt(body)
        headers = {
            "Content-Type": "text/plain;charset=UTF-8",
            "X-Post-Signature": self._sign(b64),
            "X-Device-Id": body["device_id"],
            "User-Agent": self.ua,
        }
        if token:
            headers["Authorization"] = "Bearer " + token
        url = path or self.api_call
        st, raw = self._http_post(url, b64, headers)
        if st != 200 or not raw:
            return {}
        try:
            return self._decrypt(raw)
        except Exception:
            return {}

    # ========== 游客登录 ==========
    def _register_guest(self):
        rsp = self._post("", path=self.api_register)
        tokens = ((rsp or {}).get("data") or {}).get("tokens") or {}
        token = tokens.get("accessToken") or ""
        refresh = tokens.get("refreshToken") or ""
        if not token:
            self._token = None
            self._token_expire = 0
            return
        self._token = token
        self._token_expire = self._jwt_exp(token)
        if not self._token_expire:
            # 拿不到 exp 保守按 12 小时失效
            self._token_expire = int(time.time()) + 12 * 3600

    def _jwt_exp(self, token):
        try:
            part = token.split(".")[1]
            part += "=" * (-len(part) % 4)
            exp = json.loads(base64.urlsafe_b64decode(part)).get("exp", 0)
            return int(exp or 0)
        except Exception:
            return 0

    def _ensure_token(self):
        if not self._token or self._token_expire - int(time.time()) < 300:
            self._register_guest()
        return self._token

    # ========== API 业务 ==========
    def _pages_list(self):
        rsp = self._post("pages.list")
        data = rsp.get("data") or {}
        pages = data.get("pages") or []
        return pages

    def _pages_tab(self, tid):
        now = time.time()
        cached = self._tab_cache.get(tid)
        if cached and now - cached[0] < self._tab_ttl:
            return cached[1]
        rsp = self._post("pages.tab", params={"id": tid},
                         query={"platform": "web", "app_version": "1.0.0"})
        data = rsp.get("data") or {}
        modules = data.get("modules") or []
        try:
            modules = sorted(modules, key=lambda m: int(m.get("sort_order") or 0))
        except Exception:
            pass
        self._tab_cache[tid] = (now, modules)
        # 记录主 collection(第一个 content_grid)
        for m in modules:
            if m.get("kind") == "content_grid" and m.get("collection_id"):
                self._main_coll[tid] = m.get("collection_id")
                break
        return modules

    def _collection_items(self, coll_id, limit=50, cursor=None):
        query = {"limit": str(int(limit))}
        if cursor:
            query["cursor"] = cursor
        rsp = self._post("catalog.collection_items", params={"id": coll_id}, query=query)
        if not rsp or rsp.get("code") != 200:
            return {}
        return rsp.get("data") or {}

    def _work_detail(self, work_id):
        rsp = self._post("catalog.work", params={"id": work_id})
        if not rsp or rsp.get("code") != 200:
            return {}
        return rsp.get("data") or {}

    def _episodes(self, work_id):
        eps = []
        cursor = None
        for _ in range(100):
            query = {"limit": "100"}
            if cursor:
                query["cursor"] = cursor
            rsp = self._post("catalog.episodes", params={"id": work_id}, query=query)
            data = rsp.get("data") if isinstance(rsp, dict) else None
            if not data:
                break
            items = data.get("items") or []
            eps.extend(items)
            has_more = data.get("has_more")
            nc = data.get("next_cursor") or ""
            if not has_more or not nc or nc == cursor:
                break
            cursor = nc
        return eps

    def _search(self, key, limit=50):
        try:
            n = max(1, min(int(limit), 50))
        except Exception:
            n = 50
        rsp = self._post("search.works", query={"q": key.strip(), "limit": str(n)})
        if not rsp or rsp.get("code") != 200:
            return []
        data = rsp.get("data") or {}
        return data.get("items") or []

    def _playback(self, episode_id):
        token = self._ensure_token()
        payload = {"episode_id": episode_id, "platform": "web", "app_version": "1.0.0"}
        rsp = self._post("playback.session", params={}, query={}, payload=payload,
                         token=token, idempotency_key=str(uuid.uuid4()))
        if not rsp or rsp.get("code") != 200:
            return {}
        data = rsp.get("data") or {}
        return data

    # ========== 图片 TVIMAGE1 ==========
    def _b64u(self, s):
        s = s.replace("-", "+").replace("_", "/")
        s += "=" * (-len(s) % 4)
        return base64.b64decode(s)

    def _decrypt_tvi(self, data):
        """解密 TVIMAGE1 v3 信封 -> (bytes, mime)，失败返回 None"""
        if data[:8] != TVIMAGE_MAGIC:
            return None
        try:
            meta_len = int.from_bytes(data[8:12], "big")
            meta = json.loads(data[12:12 + meta_len].decode("utf-8"))
            if meta.get("schema") != "tv.image-envelope" or int(meta.get("version") or 0) != 3:
                return None
            if meta.get("algorithm") != "AES-256-GCM":
                return None
            aad = data[12:12 + meta_len]
            cipher = data[12 + meta_len:]
            nonce = self._b64u(meta.get("nonce") or "")
            wk = self._b64u(meta.get("wrapped_key") or "")
            digest = bytes.fromhex(meta.get("plaintext_sha256") or "")
            if not nonce or not wk or not digest:
                return None
            info = (TVIMAGE_INFO + nonce + digest + b"\x00"
                    + (meta.get("asset_id") or "").encode("utf-8") + b"\x00"
                    + (meta.get("role") or "").encode("utf-8"))
            d = hashlib.sha256(info).digest()
            key = bytes(x ^ y for x, y in zip(wk, d))
            plaintext = self._gcm_decrypt(key, nonce, aad, cipher)
            mime = meta.get("mime_type") or "image/jpeg"
            return plaintext, mime
        except Exception:
            return None

    def _gcm_decrypt(self, key, nonce, aad, data):
        """AES-GCM 解密: 有 pycryptodome 走它, 否则纯 Python 降级(data=ciphertext||tag)"""
        if HAS_CRYPTO:
            c = _CAES.new(key, _CAES.MODE_GCM, nonce=nonce)
            c.update(aad)
            return c.decrypt_and_verify(data[:-16], data[-16:])
        return _aes256_gcm_decrypt(key, nonce, aad, data)

    def _http_get_bytes(self, url, timeout=15):
        if HAS_REQUESTS:
            r = _requests.get(url, headers={"User-Agent": self.ua}, timeout=timeout)
            if r.status_code == 200:
                return r.content
            return None
        import urllib.request
        try:
            req = urllib.request.Request(url, headers={"User-Agent": self.ua})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except Exception:
            return None

    def _fetch_image(self, url):
        """拉取并返回壳可直接显示的 (bytes, mime); TVIMAGE1 信封自动解密, 普通图直通"""
        raw = self._http_get_bytes(url)
        if not raw:
            return None, None
        if raw[:8] == TVIMAGE_MAGIC:
            got = self._decrypt_tvi(raw)
            if got and got[0]:
                return got
            return None, None
        mime = _sniff_image_mime(raw)
        if mime:
            return raw, mime
        return None, None

    def _pic(self, cover):
        """cover 可为 dict {ciphertext_url,...} 或字符串 URL -> 壳可显示的图片地址"""
        if isinstance(cover, dict):
            url = cover.get("ciphertext_url") or ""
        elif isinstance(cover, str):
            url = cover
        else:
            url = ""
        if not url:
            return ""
        # 一律走本地代理: 加密信封由 localProxy 解密后再交付壳, 壳永远拿不到密文
        return PROXY_PREFIX + quote(url, safe="")

    # ========== 数据组装 ==========
    def _work_to_vod(self, w):
        if not isinstance(w, dict):
            return None
        vid = str(w.get("id") or "")
        if not vid:
            return None
        vod = {
            "vod_id": vid,
            "vod_name": w.get("title") or "",
            "vod_pic": self._pic(w.get("cover")),
            "vod_remarks": self._remarks(w),
            "vod_year": str(w.get("release_year") or ""),
            "vod_area": w.get("area") or "",
            "vod_score": str(w.get("score") or ""),
            "vod_actor": "",
            "vod_director": "",
            "vod_content": w.get("description") or "",
        }
        people = w.get("people") or []
        actors = [p.get("name", "") for p in people if p.get("credit_type") == "actor" and p.get("name")]
        directors = [p.get("name", "") for p in people if p.get("credit_type") == "director" and p.get("name")]
        if actors:
            vod["vod_actor"] = " / ".join(actors)
        if directors:
            vod["vod_director"] = " / ".join(directors)
        if w.get("tags"):
            vod["vod_type"] = " / ".join(str(t) for t in w["tags"] if t)
        return vod

    def _remarks(self, w):
        epc = w.get("available_episode_count")
        if epc:
            try:
                n = int(epc)
                if n > 0:
                    return "全%d集" % n
            except Exception:
                pass
        score = w.get("score")
        if score:
            try:
                s = float(score)
                if s > 0:
                    return "评分%s" % (str(score))
            except Exception:
                pass
        return ""

    def _items_from_modules(self, modules):
        """聚合 content_grid 的 work 列表(去重)"""
        seen = set()
        out = []
        for m in modules:
            if m.get("kind") != "content_grid":
                continue
            for it in (m.get("items") or []):
                w = it.get("work") if isinstance(it, dict) else None
                if not isinstance(w, dict):
                    continue
                vid = str(w.get("id") or "")
                if not vid or vid in seen:
                    continue
                seen.add(vid)
                out.append(self._work_to_vod(w))
        return out

    # ========== TVBox 十二接口 ==========
    def homeContent(self, filter):
        result = {"class": [], "filters": {}}
        try:
            pages = self._pages_list()
            for page in pages:
                for tab in (page.get("tabs") or []):
                    if tab.get("content_type") != "category":
                        continue
                    tid = str(tab.get("id") or "")
                    title = tab.get("title") or ""
                    if tid and title:
                        result["class"].append({"type_id": tid, "type_name": title})
            if filter:
                result["filters"] = self.config.get("filter", {})
        except Exception as e:
            print("[aptv] homeContent error:", e)
        return result

    def homeVideoContent(self):
        result = {"list": []}
        try:
            pages = self._pages_list()
            hot_tid = None
            for page in pages:
                if page.get("key") == "home":
                    hot_tid = page.get("default_tab_id") or (
                        next((t.get("id") for t in (page.get("tabs") or [])
                              if t.get("content_type") == "category"), None))
                    break
            if not hot_tid:
                # 兜底取第一个 category tab
                for page in pages:
                    for t in (page.get("tabs") or []):
                        if t.get("content_type") == "category":
                            hot_tid = t.get("id")
                            break
                    if hot_tid:
                        break
            if hot_tid:
                modules = self._pages_tab(hot_tid)
                result["list"] = self._items_from_modules(modules)[:80]
        except Exception as e:
            print("[aptv] homeVideoContent error:", e)
        return result

    def categoryContent(self, tid, pg, filter, extend):
        try:
            pg = max(1, int(pg or 1))
        except Exception:
            pg = 1
        result = {"list": [], "page": pg, "pagecount": 9999, "limit": 50, "total": 999999}
        try:
            modules = self._pages_tab(tid)
            main_coll = self._main_coll.get(tid)
            if pg == 1:
                videos = self._items_from_modules(modules)
                if not videos and main_coll:
                    data = self._collection_items(main_coll, 50)
                    videos = [v for v in (self._work_to_vod((it or {}).get("work"))
                                          for it in (data.get("items") or [])) if v]
                self._cat_cursors[tid] = [None]
                result["list"] = videos
                result["limit"] = max(1, len(videos))
                return result
            # 第 2 页起: 主 collection 游标分页
            if not main_coll:
                return result
            cursors = self._cat_cursors.setdefault(tid, [None])
            while len(cursors) < pg:
                i = len(cursors)
                prev = cursors[i - 1]
                data = self._collection_items(main_coll, 50, prev)
                cursors.append((data.get("next_cursor") or None) if data else None)
            cursor_in = cursors[pg - 1]
            data = self._collection_items(main_coll, 50, cursor_in)
            items = data.get("items") or []
            videos = []
            for it in items:
                w = it.get("work") if isinstance(it, dict) else None
                v = self._work_to_vod(w) if isinstance(w, dict) else None
                if v:
                    videos.append(v)
            result["list"] = videos
        except Exception as e:
            print("[aptv] categoryContent error:", e)
        return result

    def detailContent(self, ids):
        result = {}
        if isinstance(ids, (list, tuple)):
            vid = str(ids[0]) if ids else ""
        else:
            vid = str(ids or "")
        if not vid:
            return result
        try:
            work = self._work_detail(vid)
            if not work:
                return result
            vod = self._work_to_vod(work) or {}
            episodes = self._episodes(vid)
            # 按季节分组
            season_map = {}
            for s in (work.get("seasons") or []):
                sid = str(s.get("id") or "")
                if not sid:
                    continue
                num = s.get("number")
                title = s.get("title") or ("" if num in (None, 1) else "第%d季" % num)
                if not title:
                    title = "正片"
                season_map[sid] = title
            groups = {}
            for ep in episodes:
                sid = str(ep.get("season_id") or "")
                groups.setdefault(sid, []).append(ep)
            play_from = []
            play_url = []
            used_sids = set()
            for s in (work.get("seasons") or []):
                sid = str(s.get("id") or "")
                if sid not in groups or sid in used_sids:
                    continue
                used_sids.add(sid)
                eps = sorted(groups[sid], key=lambda e: e.get("episode_number") or 0)
                title = season_map.get(sid, "正片")
                parts = []
                for ep in eps:
                    eid = str(ep.get("id") or "")
                    if not eid:
                        continue
                    num = ep.get("episode_number")
                    ename = ep.get("title") or ("第%d集" % num if num else eid)
                    parts.append("%s$m3u8:%s" % (ename, eid))
                if parts:
                    play_from.append(title)
                    play_url.append("#".join(parts))
            for sid, eps in groups.items():
                if sid in used_sids or not sid:
                    continue
                used_sids.add(sid)
                eps = sorted(eps, key=lambda e: e.get("episode_number") or 0)
                parts = []
                for ep in eps:
                    eid = str(ep.get("id") or "")
                    if not eid:
                        continue
                    num = ep.get("episode_number")
                    ename = ep.get("title") or ("第%d集" % num if num else eid)
                    parts.append("%s$m3u8:%s" % (ename, eid))
                if parts:
                    play_from.append("默认线路")
                    play_url.append("#".join(parts))
            if not play_from:
                play_from.append("正片")
                play_url.append("")
            vod["vod_play_from"] = "$$$".join(play_from)
            vod["vod_play_url"] = "$$$".join(play_url)
            result["list"] = [vod]
        except Exception as e:
            print("[aptv] detailContent error:", e)
            result["list"] = []
        return result

    def searchContent(self, key, quick, pg):
        result = {"list": [], "page": pg, "pagecount": 1, "limit": 50, "total": 0}
        if not key:
            return result
        try:
            items = self._search(key, 50)
            videos = []
            for w in items:
                v = self._work_to_vod(w)
                if v:
                    videos.append(v)
            result["list"] = videos
            result["total"] = len(videos)
        except Exception as e:
            print("[aptv] searchContent error:", e)
        return result

    def playerContent(self, flag, id, vipFlags):
        ep_id = id or ""
        if ep_id.startswith("m3u8:"):
            ep_id = ep_id[5:]
        elif ep_id.startswith("play:"):
            ep_id = ep_id[5:]
        if not ep_id:
            return {"parse": 0, "jx": 0, "playUrl": "", "url": "", "header": {}}
        try:
            data = self._playback(ep_id)
            murl = (data or {}).get("manifest_url") or ""
            if not murl:
                return {"parse": 0, "jx": 0, "playUrl": "", "url": "", "header": {}}
            return {
                "parse": 0,
                "jx": 0,
                "playUrl": "",
                "url": murl,
                "header": {"User-Agent": self.ua, "Referer": REFERER},
                "format": "application/x-mpegURL",
            }
        except Exception as e:
            print("[aptv] playerContent error:", e)
            return {"parse": 0, "jx": 0, "playUrl": "", "url": "", "header": {}}

    def _extract_proxy_url(self, param):
        """从壳传入的各种形态还原真实图片 URL
        兼容: dict / JSON 串 / 完整代理 URL / /proxy?url= 路径 / query 串 / 裸 URL"""
        from urllib.parse import parse_qs
        if isinstance(param, dict):
            s = str(param.get("url") or param.get("URL") or param.get("u") or "").strip()
        elif isinstance(param, str):
            s = param.strip()
        else:
            return ""
        if not s:
            return ""
        if s.startswith("{"):
            try:
                j = json.loads(s)
                if isinstance(j, dict):
                    s = str(j.get("url") or j.get("URL") or j.get("u") or "").strip()
            except Exception:
                return ""
        if not s:
            return ""
        # s 现在必为字符串, 统一按字符串形态继续解析(尤其 dict/JSON 值可能是完整代理 URL)
        if s.startswith("http"):
            for pfx in (PROXY_PREFIX, "http://127.0.0.1:9978/proxy?url="):
                if s.startswith(pfx):
                    return unquote(s[len(pfx):])
            if "/proxy?" in s:
                vals = parse_qs(s.split("/proxy?", 1)[1]).get("url")
                if vals:
                    return vals[0]
            return s
        if s.startswith("/proxy?"):
            vals = parse_qs(s[len("/proxy?"):]).get("url")
            return vals[0] if vals else ""
        if s.startswith("?") or s.startswith("url=") or s.startswith("do="):
            qs = s[1:] if s.startswith("?") else s
            vals = parse_qs(qs).get("url")
            return vals[0] if vals else ""
        return s

    def localProxy(self, param):
        """TVIMAGE1 图片解密代理: 返回 [状态码, MIME, body, header]"""
        url = self._extract_proxy_url(param)
        if not url:
            return [404, "text/plain", b"Not Found", {}]
        body, mime = self._fetch_image(url)
        if body is None:
            return [502, "text/plain", b"Image Decrypt Failed", {}]
        return [200, mime, body,
                {"Cache-Control": "max-age=86400", "Access-Control-Allow-Origin": "*"}]

    def isVideoFormat(self, url):
        video_exts = ['.m3u8', '.mp4', '.flv', '.mkv', '.avi', '.ts', '.mpg']
        return any(ext in (url or "").lower() for ext in video_exts)

    def manualVideoCheck(self):
        return False

    def action(self, action):
        return {}


# ========== 命令行自检 ==========
def _selftest():
    sp = Spider()
    sp.init("")
    print("名称:", sp.getName())
    print("依赖:", sp.getDependence())

    print("\n=== homeContent ===")
    home = sp.homeContent(False)
    print("分类数:", len(home.get("class", [])))
    for c in home.get("class", [])[:10]:
        print("  ", c.get("type_name"), c.get("type_id"))
    if not home.get("class"):
        print("[FAIL] 无分类")
        return False

    print("\n=== homeVideoContent ===")
    hv = sp.homeVideoContent()
    print("推荐数:", len(hv.get("list", [])))
    if hv.get("list"):
        print("  样例:", json.dumps(hv["list"][0], ensure_ascii=False)[:220])

    print("\n=== categoryContent pg1 ===")
    tid = home["class"][0]["type_id"]
    cat = sp.categoryContent(tid, "1", False, {})
    print("数量:", len(cat.get("list", [])))
    vod_sample = None
    if cat.get("list"):
        vod_sample = cat["list"][0]
        print("  样例:", json.dumps(vod_sample, ensure_ascii=False)[:220])

    print("\n=== categoryContent pg2 ===")
    if vod_sample:
        cat2 = sp.categoryContent(tid, "2", False, {})
        print("数量:", len(cat2.get("list", [])))
        if cat2.get("list"):
            print("  样例:", json.dumps(cat2["list"][0], ensure_ascii=False)[:180])

    print("\n=== searchContent ===")
    se = sp.searchContent("复仇者", False, "1")
    print("数量:", len(se.get("list", [])))
    if se.get("list"):
        print("  样例:", json.dumps(se["list"][0], ensure_ascii=False)[:180])
        vod_sample = se["list"][0]

    print("\n=== detailContent ===")
    if vod_sample:
        detail = sp.detailContent(vod_sample["vod_id"])
        lst = detail.get("list", [])
        print("详情数:", len(lst))
        if lst:
            v = lst[0]
            print("  标题:", v.get("vod_name"))
            print("  年份:", v.get("vod_year"), "地区:", v.get("vod_area"))
            print("  导演:", v.get("vod_director"))
            print("  演员:", (v.get("vod_actor") or "")[:80])
            print("  线路:", v.get("vod_play_from"))
            purl = v.get("vod_play_url") or ""
            print("  播放串前120:", purl[:120])
            ep0 = purl.split("$$$")[0].split("#")[0] if purl else ""
            if ep0 and "$" in ep0:
                play_id = ep0.split("$", 1)[1]
                print("\n=== playerContent ===")
                pc = sp.playerContent("", play_id, [])
                print(json.dumps(pc, ensure_ascii=False)[:220])
                murl = pc.get("url")
                if murl:
                    import requests as _req
                    r = _req.head(murl, timeout=15, headers={"User-Agent": UA})
                    print("m3u8 head status:", r.status_code)
                    if r.status_code == 200:
                        r2 = _req.get(murl, timeout=15, headers={"User-Agent": UA})
                        print("m3u8 get status:", r2.status_code, "len:", len(r2.text),
                              "is_m3u8:", r2.text.startswith("#EXTM3U"))
            else:
                print("[WARN] 无播放串(该作品可能不可播)")

    print("\n=== localProxy 图片解密 ===")
    print("HAS_CRYPTO:", HAS_CRYPTO, "(False 时走纯 Python AES-GCM 降级)")
    if vod_sample:
        pic = vod_sample.get("vod_pic") or ""
        if pic.startswith(PROXY_PREFIX):
            raw_url = unquote(pic[len(PROXY_PREFIX):])
            body, mime = sp._fetch_image(raw_url)
            if body:
                print("解密成功:", mime, len(body), "magic:", body[:4])
            else:
                print("[WARN] 图片解密失败")
            # 多种传入形态都应还原同一张图
            forms = {"dict": {"url": pic}, "full": pic,
                     "path": "/proxy?url=" + quote(raw_url, safe=""),
                     "query": "url=" + quote(raw_url, safe="")}
            for name, p in forms.items():
                r = sp.localProxy(p)
                ok = isinstance(r, list) and r[0] == 200 and r[2][:2] == b"\xff\xd8"
                print(f"  localProxy[{name}]:", "OK" if ok else f"FAIL {r[0]} {r[1] if isinstance(r,list) else r}")
    print("\n自检结束")


if __name__ == "__main__":
    _selftest()