#!/usr/bin/env python3
import os
import re
import io
import shutil
import json
import time
import hashlib
import urllib.parse
from datetime import datetime

import requests
from bs4 import BeautifulSoup
from flask import Flask, render_template, request, jsonify, Response, send_file
import zipfile
import py7zr
import socket as _socket
import subprocess

_orig_getaddrinfo = _socket.getaddrinfo
_DNS_PIN = {'coserslove.com': '104.21.7.113'}

# 爱丽丝书屋使用轮换子域名（als1~als6），单个域名可能指向 127.0.1.1（被污染）。
_ALICESW_HOSTS = [
    'xn--vcsx64d.als2.sbs',
    'xn--vcsx64d.als3.sbs',
    'xn--vcsx64d.als4.sbs',
    'xn--vcsx64d.als5.sbs',
    'xn--vcsx64d.als6.sbs',
    'xn--vcsx64d.als1.sbs',
]

def _pinned_getaddrinfo(host, port, *args, **kwargs):
    target = _DNS_PIN.get(host)
    if target:
        return _orig_getaddrinfo(target, port or 443, *args, **kwargs)
    return _orig_getaddrinfo(host, port, *args, **kwargs)

_socket.getaddrinfo = _pinned_getaddrinfo

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_NAME = 'Malice-Library'
APP_VERSION = '1.0.0'
COS_COOKIE_FILE = os.path.join(BASE_DIR, 'cos_cookie.json')
_COS_COOKIE_MEM = ''


def load_cos_cookie(force_file=False):
    global _COS_COOKIE_MEM
    if not force_file and _COS_COOKIE_MEM:
        return _COS_COOKIE_MEM
    try:
        with open(COS_COOKIE_FILE, 'r', encoding='utf-8') as f:
            d = json.load(f)
        _COS_COOKIE_MEM = (d.get('cf_clearance') or '').strip()
        return _COS_COOKIE_MEM
    except Exception:
        return ''


def save_cos_cookie(cf):
    global _COS_COOKIE_MEM
    cf = (cf or '').strip()
    _COS_COOKIE_MEM = cf
    try:
        with open(COS_COOKIE_FILE, 'w', encoding='utf-8') as f:
            json.dump({'cf_clearance': cf}, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def _cos_auto_solve():
    """用无头浏览器自动通过 Cloudflare 验证，获取 cf_clearance 并保存。"""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {'ok': False, 'message': '未安装 playwright（pip install playwright && playwright install chromium）'}
    try:
        pinned_ip = _DNS_PIN.get('coserslove.com')
        host_rules = f'MAP coserslove.com {pinned_ip}' if pinned_ip else None
        with sync_playwright() as p:
            launch_args = ['--no-sandbox', '--disable-blink-features=AutomationControlled']
            if host_rules:
                launch_args.append(f'--host-resolver-rules={host_rules}')
            browser = p.chromium.launch(headless=True, args=launch_args)
            ctx = browser.new_context(
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
                locale='zh-CN',
            )
            page = ctx.new_page()
            page.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
            page.goto(COS_BASE + '/', wait_until='domcontentloaded', timeout=60000)
            for _ in range(12):
                page.wait_for_timeout(3000)
                html = page.content()
                if 'Just a moment' not in html[:20000]:
                    break
            cookies = ctx.cookies()
            cf = ''
            for c in cookies:
                if c.get('name') == 'cf_clearance' and c.get('value'):
                    cf = c['value']
                    break
            ok = bool(cf)
            save_cos_cookie(cf)
            browser.close()
            return {'ok': ok, 'message': '已自动获取 cf_clearance' if ok else '未能通过验证，请重试', 'cf': cf[:20] + '...' if cf else ''}
    except Exception as e:
        return {'ok': False, 'message': f'自动获取失败: {e}'}


def cos_page_headers():
    h = dict(HEADERS)
    cf = load_cos_cookie()
    if cf:
        h['Cookie'] = 'cf_clearance=' + cf
    return h

ARCHIVE_EXTS = {'.zip', '.7z', '.rar'}
TEXT_EXTS = {'.txt', '.md', '.novel', '.log', '.json', '.srt', '.ass', '.text', '.ncode'}
IMAGE_EXTS = {'.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp', '.svg', '.ico', '.avif'}
VIDEO_EXTS = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.flv', '.webm', '.m4v', '.ts', '.mpg', '.mpeg', '.3gp', '.rmvb'}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Accept-Encoding': 'gzip, deflate',
    'Connection': 'keep-alive',
    'Referer': 'https://www.google.com/',
}

SITES = {
    'alicesw': {
        'name': '爱丽丝书屋',
        'base': 'https://xn--vcsx64d.als6.sbs',
        'hosts': list(_ALICESW_HOSTS),
        'encoding': 'utf-8',
    },
    'alicesw2': {
        'name': '爱丽丝书屋(简体)',
        'base': 'https://alicesw.com',
        'hosts': ['alicesw.com'],
        'encoding': 'utf-8',
    },
}

CATEGORIES = {
    'alicesw': [
        {'id': '71', 'name': '科幻'}, {'id': '79', 'name': '经典'},
        {'id': '75', 'name': '奇幻'}, {'id': '69', 'name': '系统'},
        {'id': '68', 'name': '武侠'}, {'id': '65', 'name': '乱伦'},
        {'id': '64', 'name': '都市'}, {'id': '63', 'name': '乡村'},
        {'id': '73', 'name': '同人'}, {'id': '62', 'name': '玄幻'},
        {'id': '61', 'name': '校园'}, {'id': '70', 'name': '穿越'},
        {'id': '22', 'name': '反差'}, {'id': '46', 'name': '凌辱'},
        {'id': '18', 'name': '堕落'}, {'id': '19', 'name': '纯爱'},
        {'id': '52', 'name': '伪娘'}, {'id': '48', 'name': '萝莉'},
        {'id': '56', 'name': '熟女'}, {'id': '50', 'name': '正太'},
        {'id': '72', 'name': '明星'}, {'id': '54', 'name': 'NTR'},
        {'id': '53', 'name': '媚黑'}, {'id': '58', 'name': '调教'},
        {'id': '59', 'name': '言情'}, {'id': '47', 'name': '百合'},
        {'id': '82', 'name': '耽美'}, {'id': '21', 'name': '重口'},
        {'id': '57', 'name': '其他'},
    ],
    'alicesw2': [
        {'id': '71', 'name': '科幻'}, {'id': '79', 'name': '经典'},
        {'id': '75', 'name': '奇幻'}, {'id': '69', 'name': '系统'},
        {'id': '68', 'name': '武侠'}, {'id': '65', 'name': '乱伦'},
        {'id': '64', 'name': '都市'}, {'id': '63', 'name': '乡村'},
        {'id': '73', 'name': '同人'}, {'id': '62', 'name': '玄幻'},
        {'id': '61', 'name': '校园'}, {'id': '70', 'name': '穿越'},
        {'id': '22', 'name': '反差'}, {'id': '46', 'name': '凌辱'},
        {'id': '18', 'name': '堕落'}, {'id': '19', 'name': '纯爱'},
        {'id': '52', 'name': '伪娘'}, {'id': '48', 'name': '萝莉'},
        {'id': '56', 'name': '熟女'}, {'id': '50', 'name': '正太'},
        {'id': '72', 'name': '明星'}, {'id': '54', 'name': 'NTR'},
        {'id': '53', 'name': '媚黑'}, {'id': '58', 'name': '调教'},
        {'id': '59', 'name': '言情'}, {'id': '47', 'name': '百合'},
        {'id': '82', 'name': '耽美'}, {'id': '21', 'name': '重口'},
        {'id': '57', 'name': '其他'},
    ],
}


_BASE_CACHE = {}

def _good_host(host, timeout=6):
    """快速探测某 host 是否可用（解析出非回环 IP 且能建立连接）。"""
    try:
        addrs = [x[4][0] for x in _orig_getaddrinfo(host, 443)]
    except Exception:
        return False
    addrs = [a for a in addrs if a and not a.startswith('127.')]
    if not addrs:
        return False
    try:
        import socket as sckt
        for a in addrs[:2]:
            try:
                tmp = sckt.create_connection((a, 443), timeout=timeout)
                tmp.close()
                return True
            except Exception:
                continue
    except Exception:
        pass
    return False


def site_base(site_key):
    """返回当前可用的站点 base；缓存，失败时可在下一请求重试探测。"""
    cached = _BASE_CACHE.get(site_key)
    if cached:
        host = cached.replace('https://', '').split('/')[0]
        if _good_host(host, timeout=4):
            return cached
    site = SITES.get(site_key)
    if not site:
        return
    for host in site.get('hosts', []):
        if _good_host(host):
            base = 'https://' + host
            _BASE_CACHE[site_key] = base
            return base
    _BASE_CACHE.pop(site_key, None)
    return site['base']


def get_session(site_key):
    site = SITES[site_key]
    s = requests.Session()
    s.headers.update(HEADERS)
    s.headers['Referer'] = site_base(site_key) + '/'
    return s


def clean_text(text):
    if not text:
        return ''
    text = re.sub(r'\s+', ' ', text).strip()
    text = text.replace('&ldquo;', '\u201c').replace('&rdquo;', '\u201d')
    text = text.replace('&mdash;', '\u2014').replace('&hellip;', '\u2026')
    text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
    text = text.replace('&nbsp;', ' ')
    return text.strip()


@app.route('/')
def index():
    resp = render_template('index.html')
    return Response(resp, headers={
        'Cache-Control': 'no-cache, no-store, must-revalidate',
        'Pragma': 'no-cache',
        'Expires': '0',
    })


@app.route('/api/version')
def api_version():
    """返回本地程序版本号（供前端顶栏与更新检查使用）。"""
    return jsonify({'name': APP_NAME, 'version': APP_VERSION,
                    'copyright': '2026', 'update_url': _update_repo_url()})


def _update_repo_url(remote='origin'):
    """从 git 远程仓库推导 owner/repo，用于查询 GitHub Releases。"""
    try:
        r = subprocess.run(['git', 'config', '--get', f'remote.{remote}.url'],
                           capture_output=True, text=True, timeout=5, cwd=BASE_DIR)
        url = (r.stdout or '').strip()
    except Exception:
        url = ''
    m = re.search(r'(?:github\.com[:/])([^/]+)/([^/.]+)', url)
    if m:
        return {'owner': m.group(1), 'repo': m.group(2)}
    return None


def _semver_key(s):
    """把 'v1.2.3' 转成可比较的 (major,minor,patch,prerelease)。非语义化时返回 None。"""
    m = re.match(r'^[vV]?(\d+)\.(\d+)\.(\d+)(?:[-+.](.+))?$', s.strip())
    if not m:
        return None
    pre = m.group(4) or ''
    return tuple([int(m.group(1)), int(m.group(2)), int(m.group(3))] + ([0, 0] if pre else [1, 0]))


@app.route('/api/update_check')
def api_update_check():
    """手动检查 GitHub 上最新 Release 版本。仅用户主动触发时才调用。"""
    repo = _update_repo_url()
    if not repo:
        return jsonify({'supported': False, 'message': '未识别到 git 远程仓库，无法检查更新',
                        'current': APP_VERSION})
    try:
        url = f"https://api.github.com/repos/{repo['owner']}/{repo['repo']}/releases/latest"
        resp = requests.get(url, timeout=10, headers={'Accept': 'application/vnd.github+json',
                                                      'User-Agent': APP_NAME})
        if resp.status_code != 200:
            return jsonify({'supported': True, 'has_update': False,
                            'message': f'GitHub 查询失败({resp.status_code})，仅显示本地版本',
                            'current': APP_VERSION})
        d = resp.json()
        tag = d.get('tag_name') or ''
        latest_version = tag.lstrip('vV')
        cur_key = _semver_key(APP_VERSION)
        new_key = _semver_key(tag) or _semver_key(latest_version)
        has_update = bool(cur_key and new_key and new_key > cur_key)
        assets = d.get('assets') or []
        assets_info = [{'name': a.get('name'), 'browser_download_url': a.get('browser_download_url'),
                        'size': a.get('size')} for a in assets]
        return jsonify({
            'supported': True,
            'current': APP_VERSION,
            'latest': latest_version,
            'has_update': has_update,
            'name': d.get('name') or '',
            'notes': (d.get('body') or '')[:2000],
            'published_at': d.get('published_at'),
            'url': d.get('html_url'),
            'zip_url': d.get('zipball_url'),
            'assets': assets_info,
        })
    except Exception as e:
        return jsonify({'supported': False, 'message': f'检查更新失败: {e}',
                        'current': APP_VERSION})


# 更新操作只允许处理以下源码白名单文件（绝对安全路径）。
_UPDATE_SRC = {
    os.path.join(BASE_DIR, 'app.py'),
    os.path.join(BASE_DIR, 'templates', 'index.html'),
    os.path.join(BASE_DIR, 'start.sh'),
    os.path.join(BASE_DIR, 'requirements.txt'),
    os.path.join(BASE_DIR, 'README.md'),
    os.path.join(BASE_DIR, 'build_release.py'),
}
# 白名单内的子目录（如 packaging/ 下的文件），逐项校验后再允许。
_UPDATE_SRC_DIRS = [
    os.path.join(BASE_DIR, 'templates'),
    os.path.join(BASE_DIR, 'packaging'),
]
# 任何情况都不可写/不可删（本地图库、cookie、元数据）
_UPDATE_FORBIDDEN = [
    os.path.join(BASE_DIR, 'archives'),
    os.path.join(BASE_DIR, COS_COOKIE_FILE) if COS_COOKIE_FILE else os.path.join(BASE_DIR, 'cos_cookie.json'),
]
_UPDATE_FORBIDDEN_FILES = {'archives', '.meta.json', 'cos_cookie.json', 'server.log'}


def _update_abs_path(name):
    """把更新包内相对路径（如 templates/index.html 或 files/templates/index.html）解析为仓库绝对路径。

    返回 (abs_path, is_allowed)；只允许落进白名单内，且严禁触碰 archives / cookie / meta。
    """
    normalized = os.path.normpath(name).replace('\\', '/')
    if normalized.startswith('files/'):
        normalized = normalized[len('files/'):]
    if normalized.startswith('../') or normalized.startswith('/') or ':' in normalized[:2]:
        return None, False
    if normalized in _UPDATE_FORBIDDEN_FILES or normalized.split('/')[0] in _UPDATE_FORBIDDEN_FILES:
        return None, False
    abs_path = os.path.abspath(os.path.join(BASE_DIR, normalized))
    if abs_path in _UPDATE_SRC or abs_path + '.md' in _UPDATE_SRC:
        return abs_path, True
    for d in _UPDATE_SRC_DIRS:
        if abs_path == d or abs_path.startswith(d + os.sep):
            return abs_path, True
    return None, False


@app.route('/api/update/apply', methods=['POST'])
def api_update_apply():
    """从更新包自动更新。仅替换白名单内的源码文件，先备份，校验失败自动回滚。

    本地图库 archives/、cos_cookie.json、.meta.json 绝不会被写入或删除。
    """
    import shutil as _sh
    zip_url = (request.json or {}).get('url')
    zip_path = (request.json or {}).get('path') or request.form.get('path')
    tmp_path = None
    if zip_url and not zip_path:
        try:
            tmp_dir = os.path.join(BASE_DIR, 'tmp')
            os.makedirs(tmp_dir, exist_ok=True)
            tmp_path = os.path.join(tmp_dir, 'update_%s.zip' % time.strftime('%Y%m%d_%H%M%S'))
            r = requests.get(zip_url, timeout=60, stream=True)
            r.raise_for_status()
            with open(tmp_path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=65536):
                    f.write(chunk)
            zip_path = tmp_path
        except Exception as e:
            if tmp_path and os.path.exists(tmp_path):
                os.remove(tmp_path)
            return jsonify({'ok': False, 'error': '更新包下载失败: %s' % e})
    if not zip_path or not os.path.exists(zip_path):
        return jsonify({'ok': False, 'error': '更新包不存在，请先下载'})
    good = False
    backup_dir = os.path.join(BASE_DIR, 'backup', time.strftime('%Y%m%d_%H%M%S'))
    os.makedirs(backup_dir, exist_ok=True)
    replaced, restored = [], []
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            names = [n for n in zf.namelist() if not n.endswith('/')]
            allowed = []
            for n in names:
                base = os.path.basename(n)
                if base in ('UPDATE.md', 'update.json', 'manifest.json', '.gitignore'):
                    continue
                abs_p, ok = _update_abs_path(n)
                if not ok:
                    return jsonify({'ok': False, 'error': f'更新包内含不允许的文件: {n}', 'replaced': replaced, 'rolled_back': restored})
                allowed.append((n, abs_p))
            for n, abs_p in allowed:
                data = zf.read(n)
                bak = os.path.join(backup_dir, os.path.basename(abs_p) + '.bak')
                if os.path.exists(abs_p):
                    _sh.copy2(abs_p, bak)
                with open(abs_p, 'wb') as f:
                    f.write(data)
                replaced.append(os.path.basename(abs_p))
        # 校验：app.py 必须可解析；templates/index.html 必须存在
        replaced_files = set(replaced)
        appfile = os.path.join(BASE_DIR, 'app.py')
        if 'app.py' in replaced_files or os.path.exists(appfile):
            try:
                compile(open(appfile, 'rb').read(), appfile, 'exec')
            except SyntaxError as e2:
                raise RuntimeError(f'更新后 app.py 语法错误: {e2}')
        if os.path.exists(os.path.join(BASE_DIR, 'templates', 'index.html')):
            with open(os.path.join(BASE_DIR, 'templates', 'index.html'), 'rb') as _f:
                if b'<html' not in _f.read(2000).lower():
                    raise RuntimeError('templates/index.html 内容异常')
        good = True
        return jsonify({'ok': True, 'replaced': replaced, 'backup': os.path.basename(backup_dir),
                        'restart': '请重启程序以生效'})
    except Exception as e:
        # 回滚备份
        for f in os.listdir(backup_dir) if os.path.isdir(backup_dir) else []:
            if f.endswith('.bak'):
                orig = os.path.join(BASE_DIR, f[:-4])
                if os.path.exists(orig):
                    _sh.copy2(os.path.join(backup_dir, f), orig)
                    restored.append(f[:-4])
        return jsonify({'ok': False, 'error': str(e), 'replaced': replaced, 'rolled_back': restored})
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


@app.route('/api/categories')
def api_categories():
    site = request.args.get('site', 'alicesw')
    cats = CATEGORIES.get(site, [])
    return jsonify({'categories': cats})


def _search_site(site_key, keyword, search_type, page, sort, serialize):
    """在单个站点执行搜索，返回 (results, total, pages)。"""
    site = SITES[site_key]
    s = get_session(site_key)
    params = {
        'q': keyword,
        'f': search_type,
        'sort': sort,
        'p': page,
        'serialize': serialize,
    }
    url = f"{site_base(site_key)}/search.html"
    resp = s.get(url, params=params, timeout=15)
    resp.encoding = site['encoding']
    soup = BeautifulSoup(resp.text, 'lxml')

    results = []
    for item in soup.select('.list-group-item'):
        title_tag = item.select_one('h5 a')
        if not title_tag:
            continue
        title = clean_text(title_tag.get_text())
        title = re.sub(r'^\d+\.\s*', '', title)
        novel_url = title_tag.get('href', '')

        author_tag = item.select_one('.text-muted a')
        author = clean_text(author_tag.get_text()) if author_tag else ''

        status_tag = item.select_one('h5 small')
        status = clean_text(status_tag.get_text()) if status_tag else ''
        status = status.strip('[]')

        desc_tag = item.select_one('.content-txt')
        desc = clean_text(desc_tag.get_text()) if desc_tag else ''

        tags = []
        tags_div = item.select('.text-muted a[href*="tag"]')
        for t in tags_div:
            tag_text = clean_text(t.get_text()).lstrip('#')
            if tag_text:
                tags.append(tag_text)

        time_tag = item.select_one('.timedesc')
        time_info = clean_text(time_tag.get_text()) if time_tag else ''

        meta_tag = item.select_one('.mb-1.text-muted')
        word_count = ''
        hits = ''
        if meta_tag:
            text = meta_tag.get_text()
            wm = re.search(r'字数[：:](.+?)　', text)
            if wm:
                word_count = clean_text(wm.group(1))
            hm = re.search(r'浏览[：:](\d+)', text)
            if hm:
                hits = hm.group(1)

        results.append({
            'title': title,
            'url': novel_url,
            'author': author,
            'status': status,
            'desc': desc,
            'tags': tags,
            'word_count': word_count,
            'hits': hits,
            'time_info': time_info,
            'site': site_key,
            'site_name': site['name'],
        })

    total = 0
    pages = 1
    errortips = soup.select_one('.errortips b')
    if errortips:
        try:
            total = int(errortips.get_text())
        except Exception:
            pass

    page_links = soup.select('.layui-laypage-default a[data-page]')
    if page_links:
        max_page = 1
        for pl in page_links:
            try:
                p_num = int(pl.get('data-page', '1'))
                if p_num > max_page:
                    max_page = p_num
            except Exception:
                pass
        pages = max_page
    return results, total, pages


@app.route('/api/search')
def api_search():
    site_key = request.args.get('site', 'alicesw')
    keyword = request.args.get('q', '').strip()
    search_type = request.args.get('f', '_all')
    page = request.args.get('p', '1')
    sort = request.args.get('sort', 'relevance')
    serialize = request.args.get('serialize', '')

    if not keyword:
        return jsonify({'error': '请输入搜索关键词', 'results': [], 'total': 0, 'page': 1, 'pages': 0})

    sites = list(SITES.keys()) if site_key == 'all' else [site_key]
    try:
        all_results = []
        totals = []
        pages_list = []
        for sk in sites:
            try:
                results, total, pages = _search_site(sk, keyword, search_type, page, sort, serialize)
                all_results.extend(results)
                totals.append(total)
                pages_list.append(pages)
            except Exception:
                continue
        return jsonify({
            'results': all_results,
            'total': sum(totals),
            'page': int(page),
            'pages': max(pages_list) if pages_list else 1,
        })
    except Exception as e:
        return jsonify({'error': str(e), 'results': [], 'total': 0, 'page': 1, 'pages': 0})


@app.route('/api/category')
def api_category():
    site_key = request.args.get('site', 'alicesw')
    cat_id = request.args.get('id', '')
    page = request.args.get('p', '1')

    if not cat_id:
        return jsonify({'error': '请选择分类', 'results': [], 'total': 0, 'page': 1, 'pages': 0})

    site = SITES[site_key]
    s = get_session(site_key)

    url = f"{site_base(site_key)}/lists/{cat_id}.html"
    if page != '1':
        url = f"{site_base(site_key)}/lists/{cat_id}/page/{page}.html"

    try:
        resp = s.get(url, timeout=15)
        resp.encoding = site['encoding']
        soup = BeautifulSoup(resp.text, 'lxml')

        results = []
        seen_urls = set()

        for a_tag in soup.select('a[href*="/novel/"]'):
            novel_url = a_tag.get('href', '')
            if not novel_url or novel_url in seen_urls:
                continue
            title = clean_text(a_tag.get_text())
            if not title:
                continue
            seen_urls.add(novel_url)

            li = a_tag.find_parent('li')
            author = ''
            if li:
                author_span = li.select_one('span.author')
                if author_span:
                    author = clean_text(author_span.get_text()).lstrip('/ ')
                text = li.get_text()
                am = re.search(r'(?:作者|/)\s*(.+)', text)
                if am and not author:
                    author = clean_text(am.group(1))

            results.append({
                'title': title,
                'url': novel_url,
                'author': author,
            })

        pages = 1
        last_link = soup.select_one('a.layui-laypage-last')
        if last_link:
            href = last_link.get('href', '')
            pm = re.search(r'[?&]p=(\d+)', href)
            if pm:
                pages = int(pm.group(1))
            else:
                pm = re.search(r'/page/(\d+)', href)
                if pm:
                    pages = int(pm.group(1))

        return jsonify({
            'results': results,
            'total': len(results),
            'page': int(page),
            'pages': pages,
        })
    except Exception as e:
        return jsonify({'error': str(e), 'results': [], 'total': 0, 'page': 1, 'pages': 0})


@app.route('/api/novel')
def api_novel():
    site_key = request.args.get('site', 'alicesw')
    novel_url = request.args.get('url', '')

    if not novel_url:
        return jsonify({'error': '缺少小说URL'})

    site = SITES[site_key]
    s = get_session(site_key)

    if novel_url.startswith('/'):
        novel_url = site_base(site_key) + novel_url

    try:
        resp = s.get(novel_url, timeout=15)
        resp.encoding = site['encoding']
        soup = BeautifulSoup(resp.text, 'lxml')

        title = ''
        title_tag = soup.select_one('.novel_title')
        if title_tag:
            title = clean_text(title_tag.get_text())
        if not title:
            og_title = soup.select_one('meta[property="og:title"]')
            if og_title:
                title = og_title.get('content', '').split('-')[0].strip()

        author = ''
        author_tag = soup.select_one('.novel_info a[href*="author"]')
        if author_tag:
            author = clean_text(author_tag.get_text())

        category = ''
        cat_tag = soup.select_one('.novel_info a[href*="/lists/"]')
        if cat_tag:
            category = clean_text(cat_tag.get_text())

        desc = ''
        desc_p = soup.select_one('.jianjie p')
        if desc_p:
            desc = clean_text(desc_p.get_text())

        tags = []
        for t in soup.select('.tags_list a[href*="tag"]'):
            tag_text = clean_text(t.get_text()).lstrip('#')
            if tag_text:
                tags.append(tag_text)

        info_text = ''
        info_div = soup.select_one('.novel_info')
        if info_div:
            info_text = clean_text(info_div.get_text())

        stats = {}
        info_text_norm = re.sub(r'\s+', ' ' * 0, info_text).replace(' ', '')
        stats_m = re.search(r'热度[：:](\d+)', info_text_norm)
        if stats_m:
            stats['hits'] = stats_m.group(1)
        stats_m = re.search(r'收藏[：:](\d+)', info_text_norm)
        if stats_m:
            stats['favorites'] = stats_m.group(1)
        stats_m = re.search(r'字数[：:](.+?万)?', info_text_norm)
        if stats_m and stats_m.group(1):
            stats['words'] = clean_text(stats_m.group(1))
        stats_m = re.search(r'章节[：:](\d+)', info_text_norm)
        if stats_m:
            stats['chapters'] = stats_m.group(1)
        stats_m = re.search(r'状态[：:](连载中|已完结|全本|完本)', info_text_norm)
        if stats_m:
            stats['status'] = stats_m.group(1)

        chapter_list_url = None
        chapter_link = soup.select_one('a[href*="/other/chapters/"]')
        if chapter_link:
            chapter_list_url = chapter_link.get('href', '')

        chapters = []
        if chapter_list_url:
            if chapter_list_url.startswith('/'):
                chapter_list_url = site_base(site_key) + chapter_list_url
            try:
                c_resp = s.get(chapter_list_url, timeout=15)
                c_resp.encoding = site['encoding']
                c_soup = BeautifulSoup(c_resp.text, 'lxml')
                for li in c_soup.select('.mulu_list li'):
                    a_tag = li.select_one('a')
                    if a_tag:
                        ch_title = clean_text(a_tag.get_text())
                        ch_url = a_tag.get('href', '')
                        chapters.append({
                            'title': ch_title,
                            'url': ch_url,
                        })
            except:
                pass

        if not chapters:
            for li in soup.select('.book_newchap li'):
                a_tag = li.select_one('a')
                if a_tag:
                    ch_title = clean_text(a_tag.get_text())
                    ch_url = a_tag.get('href', '')
                    chapters.append({
                        'title': ch_title,
                        'url': ch_url,
                    })

        return jsonify({
            'title': title,
            'author': author,
            'category': category,
            'desc': desc,
            'tags': tags,
            'stats': stats,
            'chapters': chapters,
            'url': novel_url,
        })
    except Exception as e:
        return jsonify({'error': str(e)})


@app.route('/api/chapter')
def api_chapter():
    site_key = request.args.get('site', 'alicesw')
    chapter_url = request.args.get('url', '')

    if not chapter_url:
        return jsonify({'error': '缺少章节URL'})

    site = SITES[site_key]
    s = get_session(site_key)

    if chapter_url.startswith('/'):
        chapter_url = site_base(site_key) + chapter_url

    try:
        resp = s.get(chapter_url, timeout=15)
        resp.encoding = site['encoding']
        soup = BeautifulSoup(resp.text, 'lxml')

        title = ''
        title_tag = soup.select_one('.j_chapterName, h3.j_chapterName')
        if title_tag:
            title = clean_text(title_tag.get_text())

        content = ''
        content_div = soup.select_one('.read-content, .j_readContent')
        if content_div:
            for p in content_div.find_all(['p', 'br']):
                if p.name == 'br':
                    content += '\n'
                else:
                    text = clean_text(p.get_text())
                    if text:
                        content += text + '\n'
        content = content.strip()

        prev_url = ''
        prev_tag = soup.select_one('#j_chapterPrev')
        if prev_tag and prev_tag.get('href'):
            prev_url = prev_tag['href']

        next_url = ''
        next_tag = soup.select_one('#j_chapterNext')
        if next_tag and next_tag.get('href'):
            next_url = next_tag['href']

        novel_info = {}
        novel_link = soup.select_one('.text-info a[href*="/novel/"]')
        if novel_link:
            novel_info['url'] = novel_link.get('href', '')
            novel_info['title'] = clean_text(novel_link.get_text())

        return jsonify({
            'title': title,
            'content': content,
            'prev_url': prev_url,
            'next_url': next_url,
            'novel_info': novel_info,
        })
    except Exception as e:
        return jsonify({'error': str(e)})


@app.route('/api/proxy_img')
def api_proxy_img():
    img_url = request.args.get('url', '')
    if not img_url:
        return '', 404
    try:
        resp = requests.get(img_url, headers=HEADERS, timeout=10)
        content_type = resp.headers.get('Content-Type', 'image/jpeg')
        return Response(resp.content, content_type=content_type)
    except:
        return '', 404


# ---------------------------------------------------------------------------
# CosersLove 图集（首页画廊，非绕过防护）
# ---------------------------------------------------------------------------
COS_BASE = 'https://coserslove.com'
IMG_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
    'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
    'Sec-Fetch-Dest': 'empty',
    'Sec-Fetch-Mode': 'cors',
    'Sec-Fetch-Site': 'same-origin',
    'Origin': COS_BASE + '/',
    'Referer': COS_BASE + '/',
}


def _cos_title_parts(title):
    """从 "[Coser] 作品名[30P - 100MB]" 提取 coser 与页数。"""
    coser = ''
    p_count = ''
    size = ''
    m = re.match(r'^\s*\[([^\]]+)\]\s*(.*)$', title)
    raw = title
    if m:
        coser = m.group(1).strip()
        raw = m.group(2).strip()
    m2 = re.search(r'(\d+)\s*P(?:\s*-?\s*([\d.]+(?:MB|GB|KB)))?\s*\]?\s*$', raw)
    if m2:
        p_count = m2.group(1)
        if m2.group(2):
            size = m2.group(2).strip()
    return coser, p_count, size


@app.route('/api/cos/latest')
def api_cos_latest():
    """抓取 coserslove 首页画廊（公开可访问的首页，非绕过防护）。"""
    try:
        resp = requests.get(COS_BASE + '/', headers=cos_page_headers(), timeout=15)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'lxml')
        albums = []
        for card in soup.select('article.masonry-item'):
            link = card.select_one('a[href*="/album/"]')
            img = card.select_one('img')
            h3 = card.select_one('h3')
            btn = card.select_one('button[onclick*="/coser/"]')
            if not link or not h3:
                continue
            href = link.get('href', '')
            cover = img.get('src') or img.get('data-src') or ''
            title = clean_text(h3.get_text())
            coser_id = ''
            coser_btn = ''
            if btn:
                m = re.search(r"/coser/([a-f0-9-]+)", btn.get('onclick', ''))
                if m:
                    coser_id = m.group(1)
                coser_btn = clean_text(btn.get_text()).lstrip('@')
            coser, p_count, size = _cos_title_parts(title)
            if not coser and coser_btn:
                coser = coser_btn
            if not coser:
                m = re.match(r'^\[([^\]]+)\]', title)
                if m:
                    coser = m.group(1).strip()
            albums.append({
                'title': title,
                'url': href,
                'id': href.rstrip('/').split('/')[-1],
                'coser': coser,
                'coser_id': coser_id,
                'cover': cover,
                'pages': p_count,
                'size': size,
            })
        return jsonify({'albums': albums})
    except Exception as e:
        return jsonify({'error': str(e), 'albums': []})


@app.route('/api/cos/check')
def api_cos_check():
    """检查 cf_clearance cookie 是否有效（能否通过防护访问专辑页）。"""
    cf = load_cos_cookie()
    if not cf:
        return jsonify({'ok': False, 'message': '未配置 cf_clearance。请在 cos_cookie.json 中填入浏览器获取的 cookie。'})
    try:
        resp = requests.get(COS_BASE + '/', headers=cos_page_headers(), timeout=15)
        return jsonify({
            'ok': resp.status_code == 200 and 'Just a moment' not in resp.text[:5000],
            'status': resp.status_code,
            'message': 'cookie 有效，可访问专辑页' if resp.status_code == 200 else f'返回 {resp.status_code}，cookie 可能已失效',
        })
    except Exception as e:
        return jsonify({'ok': False, 'message': str(e)})


@app.route('/api/cos/refresh_cookie')
def api_cos_refresh_cookie():
    """自动用无头浏览器通过 Cloudflare 验证并刷新 cf_clearance cookie。"""
    res = _cos_auto_solve()
    if not res['ok']:
        return jsonify({'ok': False, 'message': res.get('message', '自动验证失败')})
    cf = load_cos_cookie(force_file=True)
    valid = False
    try:
        resp = requests.get(COS_BASE + '/', headers=cos_page_headers(), timeout=15)
        valid = resp.status_code == 200 and 'Just a moment' not in resp.text[:5000]
    except Exception:
        valid = False
    return jsonify({'ok': valid, 'message': '验证通过，cookie 已生效' if valid else 'cookie 已获取但未能访问，可能被限流', 'cf_prefix': (cf or '')[:20]})


COS_SESSION = requests.Session()
COS_SESSION.headers.update(HEADERS)


def _cos_get(url, auto_solve=True, max_solve=1):
    """带 cookie 访问 coserslove 页面；403/challenge 时先自动验证再重试一次。"""
    cf = load_cos_cookie()
    if not cf and auto_solve and max_solve > 0:
        res = _cos_auto_solve()
        if res.get('ok'):
            cf = load_cos_cookie(force_file=True)
    h = cos_page_headers()
    COS_SESSION.cookies.set('cf_clearance', cf, domain='coserslove.com', path='/')
    try:
        resp = COS_SESSION.get(url, headers=h, timeout=20)
    except Exception as e:
        if auto_solve and max_solve > 0:
            res = _cos_auto_solve()
            if res.get('ok'):
                return _cos_get(url, auto_solve=False, max_solve=0)
        raise e
    if resp.status_code == 403 or 'Just a moment' in resp.text[:5000]:
        if auto_solve and max_solve > 0:
            res = _cos_auto_solve()
            if res.get('ok'):
                return _cos_get(url, auto_solve=False, max_solve=0)
        raise RuntimeError('Cloudflare 拦截（403 / JS challenge）：自动验证失败，请重试')
    return resp


@app.route('/api/cos/album')
def api_cos_album():
    """解析专辑全部图片列表（需有效 cf_clearance cookie）。翻页收集全尺寸 xl 图片。"""
    album_id = (request.args.get('id') or request.args.get('url') or '').strip()
    if not album_id:
        return jsonify({'error': '缺少 id 参数'})
    album_id = album_id.rstrip('/').split('/')[-1]
    try:
        resp = _cos_get(f'{COS_BASE}/album/{album_id}')
        resp.encoding = 'utf-8'
        html = resp.text
        soup = BeautifulSoup(html, 'lxml')

        title = ''
        h1 = soup.select_one('h1') or soup.select_one('.album-title') or soup.select_one('title')
        if h1:
            title = clean_text(h1.get_text())

        coser = ''
        coser_id = ''
        for a in soup.select('a[href*="/coser/"]'):
            t = clean_text(a.get_text()).lstrip('@').strip()
            if t:
                coser = t
                m = re.search(r'/coser/([a-f0-9-]+)', a.get('href', ''))
                if m:
                    coser_id = m.group(1)
                break

        # 专辑相关标签（/tag/xxx 链接，排除常见系统链接）
        tags = []
        for a in soup.select('a[href*="/tag/"]'):
            href = a.get('href', '')
            m = re.match(r'^/tag/([a-z0-9-]+)/?$', href)
            if not m:
                continue
            slug = m.group(1)
            name = clean_text(a.get_text()).lstrip('#').strip()
            if not name or name == slug:
                name = slug
            if {'slug': slug, 'name': name} not in tags:
                tags.append({'slug': slug, 'name': name})

        # 收集所有翻页 URL（/album/{id}/2 ... /N）
        pages = ['/album/' + album_id]
        for m in re.finditer(r'href="(/album/' + re.escape(album_id) + r'/(\d+))"', html):
            pages.append(m.group(1))
        pages = list(dict.fromkeys(pages))
        try:
            pages.sort(key=lambda u: int(u.rstrip('/').split('/')[-1]))
        except Exception:
            pass

        images = []
        seen = set()
        for page_url in pages:
            if page_url != pages[0]:
                r2 = _cos_get(COS_BASE + page_url)
                r2.encoding = 'utf-8'
                page_html = r2.text
            else:
                page_html = html
            # 全尺寸 xl 图（下载按钮 data-image-url）为权威来源
            for u in re.findall(r'data-image-url="([^"]+)"', page_html):
                if u not in seen:
                    seen.add(u)
                    images.append({'url': u, 'name': os.path.basename(u)})
            if not images:
                for u in re.findall(r'["\'](https?://[^"\']*?/pic/[^"\']*?\.(?:avif|webp|jpg|png))["\']', page_html):
                    if u not in seen:
                        seen.add(u)
                        images.append({'url': u, 'name': os.path.basename(u)})

        # 统一提供全尺寸 xl 与预览 md
        for rcd in images:
            u = rcd['url']
            if '/xl.' in u:
                rcd['thumb'] = u.replace('/xl.', '/md.')
            else:
                rcd['thumb'] = u

        return jsonify({
            'id': album_id,
            'title': title,
            'coser': coser,
            'coser_id': coser_id,
            'tags': tags,
            'images': images,
            'count': len(images),
        })
    except RuntimeError as e:
        return jsonify({'error': str(e), 'images': []})
    except Exception as e:
        return jsonify({'error': f'解析失败: {e}', 'images': []})


def _parse_cos_card(card, base=COS_BASE):
    """解析 coserslove 首页/搜索/标签页的单个卡片（article.masonry-item 或 JSON 条目）。"""
    link = card.select_one('a[href*="/album/"]')
    img = card.select_one('img')
    h3 = card.select_one('h3')
    btn = card.select_one('button[onclick*="/coser/"]')
    if not link or not h3:
        return None
    href = link.get('href', '')
    if not href.startswith('http'):
        href = base + href
    id_ = href.rstrip('/').split('/')[-1]
    cover = img.get('src') or img.get('data-src') or ''
    title = clean_text(h3.get_text())
    coser_id = ''
    coser_btn = ''
    if btn:
        m = re.search(r"/coser/([a-f0-9-]+)", btn.get('onclick', ''))
        if m:
            coser_id = m.group(1)
        coser_btn = clean_text(btn.get_text()).lstrip('@')
    coser, p_count, size = _cos_title_parts(title)
    if not coser and coser_btn:
        coser = coser_btn
    if not coser:
        m = re.match(r'^\[([^\]]+)\]', title)
        if m:
            coser = m.group(1).strip()
    return {
        'title': title,
        'url': href,
        'id': id_,
        'coser': coser,
        'coser_id': coser_id,
        'cover': cover,
        'pages': p_count,
        'size': size,
    }


COS_TAGS_CACHE = {'ts': 0.0, 'items': [], 'ttl': 1800}


def _cos_fetch_tags_cached():
    """抓取 /tags 标签列表（带 30 分钟缓存），返回 [{slug,name,count}]。"""
    now = time.time()
    if COS_TAGS_CACHE['items'] and now - COS_TAGS_CACHE['ts'] < COS_TAGS_CACHE['ttl']:
        return COS_TAGS_CACHE['items']
    resp = _cos_get(COS_BASE + '/tags')
    resp.encoding = 'utf-8'
    soup = BeautifulSoup(resp.text, 'lxml')
    tags = []
    for a in soup.select('a[href*="/tag/"]'):
        href = a.get('href', '')
        if href.count('/') != 2:
            continue
        slug = href.rstrip('/').split('/')[-1]
        if not re.match(r'^[a-z0-9-]+$', slug):
            continue
        text = clean_text(a.get_text())
        m = re.search(r'([^#\d]*)[#]?([\d]+)', text)
        name = m.group(1).strip() or slug
        count = int(m.group(2)) if m and m.group(2) else 0
        if not name or name == slug:
            name = slug
        tags.append({'slug': slug, 'name': name, 'count': count})
    seen = {}
    for t in tags:
        if t['slug'] not in seen:
            seen[t['slug']] = t
    COS_TAGS_CACHE['items'] = list(seen.values())
    COS_TAGS_CACHE['ts'] = now
    return COS_TAGS_CACHE['items']


def _cos_match_tag(q):
    """搜索词精确匹配已有标签（slug / 显示名，忽略大小写与 # 前缀）时返回该标签。"""
    nq = re.sub(r'\s+', ' ', q.strip().lstrip('#')).casefold()
    if not nq:
        return None
    variants = {nq, nq.replace(' ', '-')}
    for t in _cos_fetch_tags_cached():
        if t['slug'].casefold() in variants or (t['name'] or '').casefold() in variants:
            return t
    return None


def _cos_fetch_tag_page(slug, page):
    """抓取标签页第 page 页，返回 (albums, total_pages, total_count_hint)。"""
    url = f'{COS_BASE}/tag/{slug}' if page <= 1 else f'{COS_BASE}/tag/{slug}/{page}'
    resp = _cos_get(url)
    resp.encoding = 'utf-8'
    soup = BeautifulSoup(resp.text, 'lxml')
    albums = []
    for card in soup.select('article.masonry-item'):
        rcd = _parse_cos_card(card)
        if rcd:
            albums.append(rcd)
    max_linked = 1
    for m in re.finditer(rf'href="/tag/{re.escape(slug)}/(\d+)"', resp.text):
        max_linked = max(max_linked, int(m.group(1)))
    # 分页链接的最大编号即末页号；无链接时仅 1 页
    linked_pages = [int(x) for x in re.findall(rf'/tag/{re.escape(slug)}/(\d+)', resp.text)]
    total_pages = max(linked_pages) if linked_pages else 1
    return albums, total_pages, max_linked


@app.route('/api/cos/search')
def api_cos_search():
    """搜索 coserslove 原站。搜索词精确命中标签时改走标签页（全量结果）。"""
    q = (request.args.get('q') or '').strip()
    page = int(request.args.get('page') or 1)
    if not q:
        return jsonify({'error': '缺少 q 参数', 'albums': []})
    try:
        # 标签精确命中 -> 标签页全量（与标签显示计数一致），否则走关键词搜索API
        matched = _cos_match_tag(q)
        if matched:
            albums, total_pages, _ = _cos_fetch_tag_page(matched['slug'], page)
            return jsonify({
                'albums': albums,
                'total': matched['count'],
                'page': page,
                'total_pages': total_pages,
                'tags': [],
                'cosers': [],
                'matched_tag': {'slug': matched['slug'], 'name': matched['name'], 'count': matched['count']},
            })
        url = f"{COS_BASE}/api/search.json?q={urllib.parse.quote(q)}&page={page}&perPage=24&lang=zh-CN"
        resp = _cos_get(url)
        data = resp.json()
        albums = []
        for item in data.get('data') or []:
            album_id = item.get('id') or ''
            title = item.get('title') or ''
            cover = item.get('cover_cf_image_id') or ''
            coser = ''
            c = item.get('coser') or {}
            if isinstance(c, dict):
                coser = c.get('name') or ''
            albums.append({
                'title': title,
                'url': f'/album/{album_id}',
                'id': album_id,
                'coser': coser,
                'coser_id': (c.get('id') if isinstance(c, dict) else ''),
                'cover': cover,
                'pages': str(item.get('image_count') or ''),
                'size': '',
            })
        return jsonify({
            'albums': albums,
            'total': (data.get('pagination') or {}).get('total', 0),
            'page': page,
            'total_pages': (data.get('pagination') or {}).get('total_pages', 1),
            'tags': data.get('tags') or [],
            'cosers': data.get('cosers') or [],
        })
    except RuntimeError as e:
        return jsonify({'error': str(e), 'albums': []})
    except Exception as e:
        return jsonify({'error': f'搜索失败: {e}', 'albums': []})


@app.route('/api/cos/tags')
def api_cos_tags():
    """解析原站标签列表页面 /tags（带缓存）。"""
    try:
        return jsonify({'tags': _cos_fetch_tags_cached()})
    except RuntimeError as e:
        return jsonify({'error': str(e), 'tags': []})
    except Exception as e:
        return jsonify({'error': f'获取标签失败: {e}', 'tags': []})


@app.route('/api/cos/categories')
def api_cos_categories():
    """解析原站分类列表页面 /categories。"""
    try:
        resp = _cos_get(COS_BASE + '/categories')
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'lxml')
        cats = []
        for a in soup.select('a[href*="/category/"]'):
            href = a.get('href', '')
            if href.count('/') != 2:
                continue
            slug = href.rstrip('/').split('/')[-1]
            if not re.match(r'^[a-z0-9-]+$', slug):
                continue
            h3 = a.select_one('h3')
            name = clean_text(h3.get_text()) if h3 else ''
            if not name:
                m = re.search(r'^(.*?)#?([\d]+)?\s*图集查看', clean_text(a.get_text()))
                name = (m.group(1) if m else '').strip() or slug
            # 数量来自独立 span（如 737）
            count = 0
            tm = re.search(r'(\d+)\s*图集', clean_text(a.get_text()))
            if tm:
                count = int(tm.group(1))
            cats.append({'slug': slug, 'name': name, 'count': count})
        seen = {}
        for c in cats:
            if c['slug'] not in seen:
                seen[c['slug']] = c
        return jsonify({'categories': list(seen.values())})
    except RuntimeError as e:
        return jsonify({'error': str(e), 'categories': []})
    except Exception as e:
        return jsonify({'error': f'获取分类失败: {e}', 'categories': []})


@app.route('/api/cos/cosers')
def api_cos_cosers():
    """解析原站热门 Coser 列表页面 /cosers。"""
    try:
        resp = _cos_get(COS_BASE + '/cosers')
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'lxml')
        cosers = []
        for a in soup.select('a[href*="/coser/"]'):
            href = a.get('href', '')
            m = re.search(r'/coser/([a-f0-9-]+)', href)
            if not m:
                continue
            cid = m.group(1)
            text = clean_text(a.get_text())
            n = re.search(r'作品[:：]\s*(\d+)', text)
            name = re.split(r'作品[:：]', text)[0].strip()
            if not name:
                name = cid
            cosers.append({'id': cid, 'name': name, 'count': int(n.group(1)) if n else 0})
        return jsonify({'cosers': cosers})
    except RuntimeError as e:
        return jsonify({'error': str(e), 'cosers': []})
    except Exception as e:
        return jsonify({'error': f'获取Coser失败: {e}', 'cosers': []})


@app.route('/api/cos/browse')
def api_cos_browse():
    """浏览标签或分类下的图集。type=tag|category&slug=..."""
    kind = request.args.get('type', 'tag')
    slug = (request.args.get('slug') or '').strip()
    page = int(request.args.get('page') or 1)
    if not slug:
        return jsonify({'error': '缺少 slug 参数', 'albums': []})
    try:
        if page <= 1:
            url = f'{COS_BASE}/{kind}/{slug}'
        else:
            url = f'{COS_BASE}/{kind}/{slug}/{page}'
        resp = _cos_get(url)
        resp.encoding = 'utf-8'
        soup = BeautifulSoup(resp.text, 'lxml')
        albums = []
        for card in soup.select('article.masonry-item'):
            rcd = _parse_cos_card(card)
            if rcd:
                albums.append(rcd)
        # 解析总页数
        total_pages = 0
        if kind == 'coser':
            for m in re.finditer(r'href="(?:/coser/)' + re.escape(slug) + r'/(\d+)"', resp.text):
                total_pages = max(total_pages, int(m.group(1)))
        else:
            for m in re.finditer(r'href="(?:/tag/|/category/)' + re.escape(slug) + r'/(\d+)"', resp.text):
                total_pages = max(total_pages, int(m.group(1)))
        return jsonify({'albums': albums, 'page': page, 'total_pages': total_pages + 1})
    except RuntimeError as e:
        return jsonify({'error': str(e), 'albums': []})
    except Exception as e:
        return jsonify({'error': f'浏览失败: {e}', 'albums': []})


@app.route('/api/cos/save')
def api_cos_save():
    """将专辑全部图片打包下载到本地 archives/ 并按作者归档。需有效 cookie。"""
    album_id = (request.args.get('id') or '').strip()
    if not album_id:
        return jsonify({'error': '缺少 id 参数'})
    try:
        # 复用专辑解析，获取全部翻页后的图片（xl 全尺寸）
        album_data = None
        with app.test_request_context(f'/api/cos/album?id={album_id}'):
            resp = api_cos_album()
            album_data = resp.get_json()
        if album_data.get('error'):
            return jsonify({'ok': False, 'error': album_data['error']})
        title = album_data.get('title') or ''
        coser = album_data.get('coser') or ''
        images = album_data.get('images') or []
        if not images:
            images = [{'url': rcd.get('url')} for rcd in images]
        if not coser:
            coser = _cos_title_parts(title)[0]

        if not images:
            return jsonify({'error': '未解析到图片', 'ok': False})

        base_name = title.strip() or album_id
        base_name = re.sub(r'[\r\n]+', ' ', base_name)
        base_name = re.sub(r'\s+', ' ', base_name).strip()
        fname = re.sub(r'[\\/:*?"<>|\r\n]', '_', f'{base_name}.zip')
        dest = os.path.join(ARCHIVE_DIR, fname)
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        if os.path.exists(dest):
            os.remove(dest)

        ok = 0
        failed = 0
        with zipfile.ZipFile(dest, 'w', zipfile.ZIP_STORED) as zf:
            written = set()
            for i, rcd in enumerate(images):
                url = rcd.get('url') if isinstance(rcd, dict) else rcd
                try:
                    ir = requests.get(url, headers=IMG_HEADERS, timeout=20)
                    if ir.status_code != 200:
                        failed += 1
                        continue
                    ext = os.path.splitext(url.split('?')[0])[1].lower() or '.avif'
                    iname = f'{i+1:03d}{ext}'
                    while iname in written:
                        iname = f'{i+1:03d}_{ok+1}{ext}'
                    zf.writestr(f'{fname[:-4]}/{iname}', ir.content)
                    written.add(iname)
                    ok += 1
                except Exception:
                    failed += 1

        if ok == 0:
            if os.path.exists(dest):
                os.remove(dest)
            return jsonify({'ok': False, 'error': '下载全部失败', 'downloaded': 0, 'failed': failed})

        rel = os.path.relpath(dest, ARCHIVE_DIR).replace('\\', '/')
        meta = load_archive_meta()
        entry = meta.get(rel, {})
        entry['author'] = coser or entry.get('author', '')
        meta[rel] = entry
        save_archive_meta(meta)

        return jsonify({
            'ok': True,
            'file': fname,
            'path': rel,
            'coser': coser,
            'title': base_name,
            'downloaded': ok,
            'failed': failed,
        })
    except RuntimeError as e:
        return jsonify({'ok': False, 'error': str(e)})
    except Exception as e:
        return jsonify({'ok': False, 'error': f'保存失败: {e}'})


@app.route('/api/cos/cover')
def api_cos_cover():
    """代理 coserslove 封面图。加 download=1 时作为附件下载。"""
    img_url = request.args.get('url', '')
    if not img_url:
        return '', 404
    try:
        resp = requests.get(img_url, headers=IMG_HEADERS, timeout=15)
        if resp.status_code != 200:
            return '', resp.status_code
        if request.args.get('download') == '1':
            filename = request.args.get('name', '') or 'coserslove.jpg'
            filename = re.sub(r'[\\/:*?"<>|]', '_', filename)
            return Response(resp.content, headers={
                'Content-Type': 'application/octet-stream',
                'Content-Disposition': f"attachment; filename*=UTF-8''{urllib.parse.quote(filename)}",
            })
        return Response(resp.content, content_type=resp.headers.get('Content-Type', 'image/jpeg'))
    except Exception:
        return '', 404


# ---------------------------------------------------------------------------
# 爱妹子 LoveCutes 图集（公开站点，无需 cookie）
# ---------------------------------------------------------------------------
LC_BASE = 'https://www.lovecutes.com'
LC_HEADERS = {
    'User-Agent': HEADERS['User-Agent'],
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Referer': LC_BASE + '/',
}
LC_SESSION = requests.Session()
LC_SESSION.headers.update(LC_HEADERS)

LC_IMG_HEADERS = {
    'User-Agent': LC_HEADERS['User-Agent'],
    'Accept': 'image/avif,image/webp,image/apng,image/*,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Referer': LC_BASE + '/',
}

LC_TYPES = [
    {'id': '1', 'name': '性感美女'}, {'id': '2', 'name': '清纯美女'},
    {'id': '3', 'name': '丝袜美女'}, {'id': '4', 'name': '美腿美女'},
    {'id': '5', 'name': '美胸美女'}, {'id': '6', 'name': 'Cosplay'},
    {'id': '7', 'name': '制服诱惑'}, {'id': '8', 'name': '网络美女'},
    {'id': '9', 'name': '大尺度美女'}, {'id': '10', 'name': 'AI美女'},
]


def _lc_get(url):
    resp = LC_SESSION.get(LC_BASE + url, timeout=20)
    resp.raise_for_status()
    resp.encoding = 'utf-8'
    return resp


def _lc_title_parts(title):
    """从 "[私房] 莎弥拉 - 白虎订阅：居家JK白虎 69P2V" 提取 coser 与页数。"""
    coser = ''
    p_count = ''
    size = ''
    raw = title
    m = re.match(r'^\s*\[([^\]]+)\]\s*', raw)
    if m:
        raw = raw[m.end():]
    cm = re.search(r'(\d+)\s*P(?:\s*(\d+)\s*[VG])?\s*$', raw)
    if cm:
        p_count = cm.group(1)
    seg = re.split(r'\s+-\s+', raw, 1)
    coser_raw = re.sub(r'(\d+)\s*[PVG]+\s*$', '', seg[0]).strip(' -:：')
    if coser_raw and len(coser_raw) <= 30:
        coser = coser_raw
    return coser, p_count, size


def _lc_total_pages(html):
    m = re.search(r'"total_pages":\s*(\d+)', html)
    if m:
        return int(m.group(1))
    m = re.search(r'共\s*(\d+)\s*[頁页]', html)
    if m:
        return int(m.group(1))
    pages = [int(x) for x in re.findall(r'data-page="(\d+)"', html)]
    return max(pages) if pages else 1


def _parse_lc_excerpt(card):
    link = card.select_one('a[href*="/article/"]')
    if not link:
        return None
    href = link.get('href', '')
    if not href.startswith('http'):
        href = LC_BASE + href
    id_ = href.rstrip('/').split('/')[-1]
    title = link.get('title') or ''
    if not title:
        h = card.select_one('h2 a')
        title = clean_text(h.get_text()) if h else ''
    img = card.select_one('img.imgbox-img') or card.select_one('img')
    cover = (img.get('data-original-src') or img.get('src') or '')
    if cover.startswith('/'):
        cover = LC_BASE + cover
    views = ''
    d = ''
    hot = card.select_one('hot')
    time_tag = card.select_one('time')
    if hot:
        views = clean_text(hot.get_text())
    if time_tag:
        d = time_tag.get('datetime') or clean_text(time_tag.get_text()) or ''
    coser, p_count, size = _lc_title_parts(title)
    return {
        'title': title,
        'url': href,
        'id': id_,
        'coser': coser,
        'coser_id': '',
        'cover': cover,
        'pages': p_count,
        'size': size,
        'views': views,
        'date': d,
    }


def _parse_lc_homecard(card):
    link = card.select_one('a[href*="/article/"]')
    if not link:
        return None
    href = link.get('href', '')
    if not href.startswith('http'):
        href = LC_BASE + href
    id_ = href.rstrip('/').split('/')[-1]
    h = card.select_one('h3 a')
    title = clean_text(h.get_text()) if h else (link.get('title') or '')
    img = card.select_one('img')
    cover = (img.get('src') or img.get('data-src') or '')
    if cover.startswith('/'):
        cover = LC_BASE + cover
    views = ''
    d = ''
    vt = card.select_one('.homepage-content-card__views')
    time_tag = card.select_one('time')
    if vt:
        views = clean_text(vt.get_text())
    if time_tag:
        d = time_tag.get('datetime') or clean_text(time_tag.get_text()) or ''
    coser, p_count, size = _lc_title_parts(title)
    return {
        'title': title,
        'url': href,
        'id': id_,
        'coser': coser,
        'coser_id': '',
        'cover': cover,
        'pages': p_count,
        'size': size,
        'views': views,
        'date': d,
    }


@app.route('/api/lc/latest')
def api_lc_latest():
    """爱妹子首页/最新图集。"""
    try:
        resp = _lc_get('/sort/new/')
        soup = BeautifulSoup(resp.text, 'lxml')
        albums = []
        for card in soup.select('article.excerpt'):
            rcd = _parse_lc_excerpt(card)
            if rcd:
                albums.append(rcd)
        return jsonify({'albums': albums, 'total_pages': _lc_total_pages(resp.text)})
    except Exception as e:
        return jsonify({'error': str(e), 'albums': []})


@app.route('/api/lc/search')
def api_lc_search():
    q = (request.args.get('q') or '').strip()
    page = int(request.args.get('page') or 1)
    if not q:
        return jsonify({'error': '缺少 q 参数', 'albums': []})
    try:
        if page <= 1:
            url = f'/search/?s={urllib.parse.quote(q)}'
        else:
            url = f'/search/page/{page}/?s={urllib.parse.quote(q)}'
        resp = _lc_get(url)
        soup = BeautifulSoup(resp.text, 'lxml')
        albums = []
        for card in soup.select('article.excerpt'):
            rcd = _parse_lc_excerpt(card)
            if rcd:
                albums.append(rcd)
        return jsonify({
            'albums': albums,
            'total': len(albums),
            'page': page,
            'total_pages': _lc_total_pages(resp.text),
            'tags': [],
            'cosers': [],
        })
    except Exception as e:
        return jsonify({'error': f'搜索失败: {e}', 'albums': []})


@app.route('/api/lc/tags')
def api_lc_tags():
    try:
        resp = _lc_get('/tag/')
        soup = BeautifulSoup(resp.text, 'lxml')
        tags = []
        seen = set()
        for li in soup.select('li'):
            a = li.select_one('a.name')
            small = li.select_one('small')
            if not a:
                continue
            href = a.get('href', '')
            m = re.search(r'/tag/(\d+)/?$', href)
            if not m:
                continue
            tid = m.group(1)
            if tid in seen:
                continue
            seen.add(tid)
            name = clean_text(a.get('title') or a.get_text()).strip()
            count = 0
            if small:
                cm = re.search(r'(\d+)', small.get_text())
                if cm:
                    count = int(cm.group(1))
            tags.append({'slug': tid, 'name': name or tid, 'count': count})
        return jsonify({'tags': tags})
    except Exception as e:
        return jsonify({'error': str(e), 'tags': []})


@app.route('/api/lc/categories')
def api_lc_categories():
    return jsonify({'categories': [{'slug': t['id'], 'name': t['name'], 'count': 0} for t in LC_TYPES]})


@app.route('/api/lc/cosers')
def api_lc_cosers():
    """爱妹子无独立 coser 目录，返回空以保持前端 Promise.all 兼容。"""
    return jsonify({'cosers': []})


@app.route('/api/lc/browse')
def api_lc_browse():
    """浏览标签或分类。type=tag|category&slug=..."""
    kind = request.args.get('type', 'tag')
    slug = (request.args.get('slug') or '').strip()
    page = int(request.args.get('page') or 1)
    if not slug:
        return jsonify({'error': '缺少 slug 参数', 'albums': []})
    try:
        prefix = '/type/' if kind == 'category' else '/tag/'
        if page <= 1:
            url = f'{prefix}{slug}'
        else:
            url = f'{prefix}{slug}/page/{page}/'
        resp = _lc_get(url)
        soup = BeautifulSoup(resp.text, 'lxml')
        albums = []
        for card in soup.select('article.excerpt'):
            rcd = _parse_lc_excerpt(card)
            if rcd:
                albums.append(rcd)
        return jsonify({'albums': albums, 'page': page, 'total_pages': _lc_total_pages(resp.text)})
    except Exception as e:
        return jsonify({'error': f'浏览失败: {e}', 'albums': []})


@app.route('/api/lc/album')
def api_lc_album():
    """解析专辑全部图片（翻页收集全部页）。"""
    album_id = (request.args.get('id') or request.args.get('url') or '').strip()
    if not album_id:
        return jsonify({'error': '缺少 id 参数'})
    album_id = album_id.rstrip('/').split('/')[-1]
    try:
        first = _lc_get(f'/article/{album_id}')
        html = first.text
        soup = BeautifulSoup(html, 'lxml')

        h1 = soup.select_one('h1.focusbox-title') or soup.select_one('h1') or soup.select_one('title')
        title = clean_text(h1.get_text()) if h1 else ''

        category = ''
        bc = soup.select_one('.seo-breadcrumbs')
        if bc:
            ca = bc.select_one('a[href*="/type/"]')
            if ca:
                category = clean_text(ca.get_text())

        tags = []
        for a in soup.select('.article-tags a[href*="/tag/"]'):
            m = re.search(r'/tag/(\d+)/?$', a.get('href', ''))
            name = clean_text(a.get_text()).lstrip('#').strip()
            if m and name:
                tags.append({'slug': m.group(1), 'name': name})

        coser, p_count, size = _lc_title_parts(title)

        total_pages = _lc_total_pages(html)
        images = []
        seen = set()
        for page in range(1, total_pages + 1):
            if page == 1:
                page_html = html
            else:
                r = _lc_get(f'/article/{album_id}/page/{page}/')
                page_html = r.text
            for im in re.finditer(r'<img class="item-image__img[^"]*"[^>]*src="([^"]+)"', page_html):
                u = im.group(1)
                if not u.startswith('http'):
                    u = LC_BASE + u
                if u.split('?')[0] not in seen:
                    seen.add(u.split('?')[0])
                    images.append({'url': u, 'thumb': u, 'name': os.path.basename(u.split('?')[0])})

        return jsonify({
            'id': album_id,
            'title': title,
            'coser': coser,
            'coser_id': '',
            'category': category,
            'tags': tags,
            'images': images,
            'count': len(images),
        })
    except Exception as e:
        return jsonify({'error': f'解析失败: {e}', 'images': []})


@app.route('/api/lc/cover')
def api_lc_cover():
    """代理爱妹子图片。加 download=1 时作为附件下载。"""
    img_url = request.args.get('url', '')
    if not img_url:
        return '', 404
    if not img_url.startswith('http'):
        img_url = LC_BASE + img_url
    try:
        resp = requests.get(img_url, headers=LC_IMG_HEADERS, timeout=15)
        if resp.status_code != 200:
            return '', resp.status_code
        if request.args.get('download') == '1':
            filename = request.args.get('name', '') or 'lovecutes.jpg'
            filename = re.sub(r'[\\/:*?"<>|]', '_', filename)
            return Response(resp.content, headers={
                'Content-Type': 'application/octet-stream',
                'Content-Disposition': f"attachment; filename*=UTF-8''{urllib.parse.quote(filename)}",
            })
        return Response(resp.content, content_type=resp.headers.get('Content-Type', 'image/jpeg'))
    except Exception:
        return '', 404


@app.route('/api/lc/save')
def api_lc_save():
    """将爱妹子专辑全部图片打包下载到本地 archives/ 并按作者归档。"""
    album_id = (request.args.get('id') or '').strip()
    if not album_id:
        return jsonify({'error': '缺少 id 参数'})
    try:
        album_data = None
        with app.test_request_context(f'/api/lc/album?id={album_id}'):
            album_data = api_lc_album().get_json()
        if album_data.get('error'):
            return jsonify({'ok': False, 'error': album_data['error']})
        title = album_data.get('title') or ''
        coser = album_data.get('coser') or ''
        images = album_data.get('images') or []
        if not coser:
            coser = _lc_title_parts(title)[0]

        if not images:
            return jsonify({'error': '未解析到图片', 'ok': False})

        base_name = re.sub(r'\s+', ' ', re.sub(r'[\r\n]+', ' ', title.strip())) or album_id
        fname = re.sub(r'[\\/:*?"<>|\r\n]', '_', f'{base_name}.zip')
        dest = os.path.join(ARCHIVE_DIR, fname)
        os.makedirs(ARCHIVE_DIR, exist_ok=True)
        if os.path.exists(dest):
            os.remove(dest)

        ok = 0
        failed = 0
        with zipfile.ZipFile(dest, 'w', zipfile.ZIP_STORED) as zf:
            written = set()
            for i, rcd in enumerate(images):
                url = rcd.get('url') if isinstance(rcd, dict) else rcd
                try:
                    ir = requests.get(url, headers=LC_IMG_HEADERS, timeout=20)
                    if ir.status_code != 200:
                        failed += 1
                        continue
                    ext = os.path.splitext(url.split('?')[0])[1].lower() or '.jpg'
                    iname = f'{i+1:03d}{ext}'
                    while iname in written:
                        iname = f'{i+1:03d}_{ok+1}{ext}'
                    zf.writestr(f'{fname[:-4]}/{iname}', ir.content)
                    written.add(iname)
                    ok += 1
                except Exception:
                    failed += 1

        if ok == 0:
            if os.path.exists(dest):
                os.remove(dest)
            return jsonify({'ok': False, 'error': '下载全部失败', 'downloaded': 0, 'failed': failed})

        rel = os.path.relpath(dest, ARCHIVE_DIR).replace('\\', '/')
        meta = load_archive_meta()
        entry = meta.get(rel, {})
        entry['author'] = coser or entry.get('author', '')
        meta[rel] = entry
        save_archive_meta(meta)

        return jsonify({
            'ok': True,
            'file': fname,
            'path': rel,
            'coser': coser,
            'title': title,
            'downloaded': ok,
            'failed': failed,
        })
    except Exception as e:
        return jsonify({'ok': False, 'error': f'保存失败: {e}'})


@app.route('/api/home')
def api_home():
    site_key = request.args.get('site', 'alicesw')
    site = SITES[site_key]
    s = get_session(site_key)

    try:
        resp = s.get(site_base(site_key) + '/', timeout=15)
        resp.encoding = site['encoding']
        soup = BeautifulSoup(resp.text, 'lxml')

        sections = []
        seen_titles = set()

        for box in soup.select('.hot-box'):
            h = box.select_one('.hot-title h2')
            if not h:
                continue
            raw_title = clean_text(h.get_text())
            title = re.sub(r'排行榜$', '', raw_title)
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)

            more_link = box.select_one('.hot-title a')
            more = more_link.get('href', '') if more_link else ''

            featured = []
            class_imgs = box.select('.class-img')
            hot_imgs = box.select('.hot-img')
            for ci in class_imgs + hot_imgs:
                a_tag = ci.select_one('h5 a') or ci.select_one('a')
                if not a_tag:
                    continue
                n_title = clean_text(a_tag.get_text())
                n_url = a_tag.get('href', '')
                if not n_title or not n_url:
                    continue
                img = ci.select_one('img')
                cover = img.get('data-src') or img.get('src', '') if img else ''
                txt = ci.get_text()
                am = re.search(r'作者[：:]\s*([^\n]+)', txt)
                author = clean_text(am.group(1)) if am else ''
                p = ci.select_one('p a')
                desc = clean_text(p.get_text()) if p else ''
                featured.append({
                    'title': n_title,
                    'url': n_url,
                    'cover': cover,
                    'author': author,
                    'desc': desc[:120] + '...' if len(desc) > 120 else desc,
                })

            ranking = []
            for li in box.select('li'):
                a_tag = li.select_one('a[href*="/novel/"]')
                if not a_tag:
                    continue
                n_title = clean_text(a_tag.get_text())
                n_url = a_tag.get('href', '')
                if not n_title:
                    continue
                author_span = li.select_one('span.author')
                author = clean_text(author_span.get_text()).lstrip('/ ') if author_span else ''
                ranking.append({
                    'title': n_title,
                    'url': n_url,
                    'author': author,
                })

            sections.append({
                'title': title,
                'more': more,
                'featured': featured,
                'ranking': ranking,
            })

        hot_items = []
        for li in soup.select('#sidebar .item-list li'):
            a_tag = li.select_one('a.titles')
            if not a_tag:
                continue
            cat_tag = li.select_one('a.ctitle')
            author_span = li.select_one('span.author')
            hot_items.append({
                'title': clean_text(a_tag.get_text()),
                'url': a_tag.get('href', ''),
                'category': clean_text(cat_tag.get_text()) if cat_tag else '',
                'author': clean_text(author_span.get_text()) if author_span else '',
            })

        return jsonify({'sections': sections, 'hot_items': hot_items})
    except Exception as e:
        return jsonify({'error': str(e)})


@app.route('/api/latest')
def api_latest():
    site_key = request.args.get('site', 'alicesw')
    page = request.args.get('p', '1')

    site = SITES[site_key]
    s = get_session(site_key)

    url = f"{site_base(site_key)}/all/order/update_time+desc.html"
    if page != '1':
        url += f"?page={page}"

    try:
        resp = s.get(url, timeout=15)
        resp.encoding = site['encoding']
        soup = BeautifulSoup(resp.text, 'lxml')

        results = []
        rows = soup.select('.rec_rullist > ul')
        for ul in rows:
            a_tag = ul.select_one('li.two a[href*="/novel/"]')
            if not a_tag:
                continue
            title = clean_text(a_tag.get_text())
            novel_url = a_tag.get('href', '')
            cat_li = ul.select_one('li.sev')
            cat_tag = cat_li.select_one('a[href*="/lists/"]') if cat_li else None
            author_li = ul.select_one('li.four')
            if not title or not novel_url:
                continue
            results.append({
                'title': title,
                'url': novel_url,
                'category': clean_text(cat_tag.get_text()) if cat_tag else '',
                'author': clean_text(author_li.get_text()) if author_li else '',
            })
        if not results:
            for item in soup.select('.list-group-item, .class-box'):
                a_tags = item.select('a[href*="/novel/"]')
                for a_tag in a_tags:
                    title = clean_text(a_tag.get_text())
                    novel_url = a_tag.get('href', '')
                    if title and novel_url:
                        results.append({
                            'title': title,
                            'url': novel_url,
                        })

        pages = 1
        page_links = soup.select('.layui-laypage-default a[data-page]')
        if page_links:
            max_page = 1
            for pl in page_links:
                try:
                    p_num = int(pl.get('data-page', '1'))
                    if p_num > max_page:
                        max_page = p_num
                except:
                    pass
            pages = max_page
        else:
            for pl in soup.select('.page a[href*="page="], a[href*="?page="]'):
                href = pl.get('href', '') or ''
                m = re.search(r'[?&]page=(\d+)', href)
                if not m:
                    continue
                try:
                    p_num = int(m.group(1))
                    if p_num > pages:
                        pages = p_num
                except:
                    pass

        return jsonify({
            'results': results,
            'total': len(results),
            'page': int(page),
            'pages': pages,
        })
    except Exception as e:
        return jsonify({'error': str(e), 'results': [], 'total': 0, 'page': 1, 'pages': 0})


# ---------------------------------------------------------------------------
# 本地压缩包浏览 (小说/视频/图片)
# ---------------------------------------------------------------------------
ARCHIVE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'archives')
ACCEPT_ARCHIVE_EXTS = ('.zip', '.7z', '.rar')
TEXT_FILE_EXTS = ('.txt', '.md', '.log', '.ncode', '.srt', '.ass', '.json', '.text')

RAR_TOOL = None
for _tool in ('unrar', 'unar', 'bsdtar', '7z'):
    if shutil.which(_tool):
        RAR_TOOL = _tool
        break


def clean_archive_name(text):
    if not text:
        return ''
    return text.replace('\\', '/')


def decode_bytes(data):
    for enc in ('utf-8', 'gb18030', 'big5'):
        try:
            return data.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return data.decode('utf-8', errors='replace')


def guess_entry_type(name):
    ext = os.path.splitext(name)[1].lower()
    if ext in TEXT_FILE_EXTS:
        return 'text'
    if ext in IMAGE_EXTS:
        return 'image'
    if ext in VIDEO_EXTS:
        return 'video'
    return 'other'


def _flat_entries(archive_path):
    """返回压缩包内全部条目 [(path, is_dir, size)]，path 使用 / 分隔。"""
    ext = os.path.splitext(archive_path)[1].lower()
    if ext == '.zip':
        with zipfile.ZipFile(archive_path) as zf:
            out = []
            for info in zf.infolist():
                name = clean_archive_name(info.filename)
                if info.is_dir():
                    out.append((name.rstrip('/'), True, 0))
                else:
                    out.append((name.rstrip('/'), False, info.file_size))
            # 补齐隐式目录
            implicit = set()
            for path, is_dir, _ in out:
                if is_dir:
                    continue
                parts = path.split('/')
                if len(parts) > 1:
                    for i in range(1, len(parts)):
                        implicit.add('/'.join(parts[:i]))
            existing = {p for p, _, _ in out}
            for d in implicit:
                if d not in existing:
                    out.append((d, True, 0))
            return out
    elif ext == '.7z':
        with py7zr.SevenZipFile(archive_path) as z7:
            out = []
            for name in z7.getnames():
                name = clean_archive_name(name)
                is_dir = name.endswith('/')
                name = name.rstrip('/')
                if not name:
                    continue
                size = 0
                if not is_dir:
                    try:
                        info = z7.getinfo(name)
                        size = info.size if info and info.size else 0
                    except Exception:
                        size = 0
                out.append((name, is_dir, size))
            return out
    elif ext == '.rar':
        if not RAR_TOOL:
            raise RuntimeError('rar 需要外部工具，请安装 unrar/bsdtar/7z')
        import rarfile
        rarfile.UNRAR_TOOL = RAR_TOOL
        with rarfile.RarFile(archive_path) as rf:
            out = []
            for info in rf.infolist():
                name = clean_archive_name(info.filename)
                out.append((name.rstrip('/'), info.is_dir(), info.file_size))
            return out
    return []


def list_archive_entries(archive_path, inner_dir=''):
    """返回压缩包内指定目录下的第一级条目列表。"""
    inner_dir = inner_dir.strip('/')
    flat = _flat_entries(archive_path)
    entries = []
    seen = set()

    prefix = (inner_dir + '/') if inner_dir else ''

    for path, is_dir, size in flat:
        if not path:
            continue
        if prefix:
            if not path.startswith(prefix):
                continue
            rel = path[len(prefix):]
        else:
            rel = path
        if not rel:
            continue
        parts = rel.split('/')
        first = parts[0]
        if len(parts) > 1 or is_dir:
            key = ('dir', first)
            if key in seen:
                continue
            seen.add(key)
            entries.append({
                'name': first, 'is_dir': True, 'size': 0, 'type': 'dir',
                'path': (inner_dir + '/' + first).strip('/'),
            })
        else:
            key = ('file', first)
            if key in seen:
                continue
            seen.add(key)
            entries.append({
                'name': first, 'is_dir': False, 'size': size,
                'type': guess_entry_type(first), 'path': path,
            })

    entries.sort(key=lambda e: (0 if e['is_dir'] else 1, e['name']))
    return entries


def extract_entry_to(archive_path, entry_path, dest_dir):
    """将压缩包内单个文件解压到 dest_dir，返回解压后的文件绝对路径。"""
    ext = os.path.splitext(archive_path)[1].lower()
    entry_path = entry_path.strip('/')

    if ext == '.zip':
        with zipfile.ZipFile(archive_path) as zf:
            target = entry_path
            if target not in zf.namelist():
                for n in zf.namelist():
                    if clean_archive_name(n) == target or n.rstrip('/') == target:
                        target = n
                        break
            out_path = os.path.join(dest_dir, os.path.basename(target))
            with zf.open(target) as src, open(out_path, 'wb') as dst:
                dst.write(src.read())
            return out_path
    elif ext == '.7z':
        with py7zr.SevenZipFile(archive_path) as z7:
            tmp_root = dest_dir
            os.makedirs(tmp_root, exist_ok=True)
            z7.extract(path=tmp_root, targets=[entry_path])
        # py7zr 会按内部目录结构解压
        for root, _, files in os.walk(dest_dir):
            for f in files:
                cand = os.path.join(root, f)
                if os.path.basename(cand) == os.path.basename(entry_path):
                    return cand
        return os.path.join(dest_dir, os.path.basename(entry_path))
    elif ext == '.rar':
        if not RAR_TOOL:
            raise RuntimeError('rar 需要外部工具，请安装 unrar 或 bsdtar')
        import rarfile
        rarfile.UNRAR_TOOL = RAR_TOOL
        with rarfile.RarFile(archive_path) as rf:
            target = entry_path
            if target not in rf.namelist():
                for n in rf.namelist():
                    if clean_archive_name(n) == target:
                        target = n
                        break
            out_path = os.path.join(dest_dir, os.path.basename(target))
            with rf.open(target) as src, open(out_path, 'wb') as dst:
                dst.write(src.read())
            return out_path
    raise RuntimeError('不支持的压缩格式')


def safe_archive_rel(rel):
    """防止路径穿越。"""
    rel = (rel or '').replace('\\', '/').lstrip('/')
    if '..' in rel.split('/'):
        return None
    return rel


@app.route('/api/local/lib')
def api_local_lib():
    """列出 archives 目录下的压缩包及其元数据。支持 q 关键词搜索 与 author 作者筛选。"""
    rel = safe_archive_rel(request.args.get('dir', ''))
    if rel is None:
        return jsonify({'error': '非法路径'})
    q = (request.args.get('q') or '').strip()
    author_filter = (request.args.get('author') or '').strip()
    base = os.path.join(ARCHIVE_DIR, rel) if rel else ARCHIVE_DIR
    if not os.path.isdir(base):
        return jsonify({'error': '目录不存在'})
    meta = load_archive_meta()
    items = []
    for name in sorted(os.listdir(base)):
        if name.startswith('.'):
            continue
        full = os.path.join(base, name)
        rel_path = os.path.relpath(full, ARCHIVE_DIR).replace('\\', '/')
        if os.path.isdir(full):
            if q and q.lower() not in name.lower():
                continue
            items.append({'name': name, 'path': rel_path, 'is_dir': True, 'size': 0, 'type': 'dir'})
        else:
            ext = os.path.splitext(name)[1].lower()
            if ext not in ACCEPT_ARCHIVE_EXTS:
                continue
            m = meta.get(rel_path, {})
            author = m.get('author', '') or ''
            if author_filter and author.strip() != author_filter:
                continue
            if q and q.lower() not in name.lower() and q.lower() not in author.lower():
                continue
            items.append({
                'name': name, 'path': rel_path, 'is_dir': False, 'size': os.path.getsize(full),
                'type': 'archive', 'ext': ext, 'author': author, 'rating': m.get('rating', 0),
            })
    return jsonify({'items': items, 'dir': rel, 'q': q, 'author': author_filter})


@app.route('/api/local/authors')
def api_local_authors():
    """返回本地图库中已有的全部作者列表（用于下拉选择）。"""
    meta = load_archive_meta()
    authors = set()
    for rel_path, m in meta.items():
        author = (m.get('author') or '').strip()
        if author:
            authors.add(author)
    return jsonify({'authors': sorted(authors)})


META_FILE = os.path.join(ARCHIVE_DIR, '.meta.json')


def load_archive_meta():
    if os.path.exists(META_FILE):
        try:
            with open(META_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_archive_meta(meta):
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    with open(META_FILE, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)


@app.route('/api/local/meta', methods=['POST'])
def api_local_meta():
    data = request.get_json(force=True, silent=True) or {}
    rel = safe_archive_rel(data.get('path', ''))
    if not rel:
        return jsonify({'error': '非法路径'})
    meta = load_archive_meta()
    entry = meta.setdefault(rel, {})
    if 'author' in data:
        entry['author'] = str(data['author']).strip()
    if 'rating' in data:
        try:
            entry['rating'] = max(0, min(5, int(data['rating'])))
        except (TypeError, ValueError):
            pass
    save_archive_meta(meta)
    return jsonify({'ok': True})


@app.route('/api/local/entries')
def api_local_entries():
    rel = safe_archive_rel(request.args.get('archive', ''))
    if not rel:
        return jsonify({'error': '非法路径'})
    full = os.path.normpath(os.path.join(ARCHIVE_DIR, rel))
    if not full.startswith(ARCHIVE_DIR) or not os.path.isfile(full):
        return jsonify({'error': '压缩包不存在'})
    inner_dir = safe_archive_rel(request.args.get('dir', ''))
    if inner_dir is None:
        return jsonify({'error': '非法路径'})
    try:
        entries = list_archive_entries(full, inner_dir)
    except RuntimeError as e:
        return jsonify({'error': str(e)})
    except Exception as e:
        return jsonify({'error': '解析失败: ' + str(e)})
    if entries is None:
        return jsonify({'error': 'rar 需要外部工具支持，请安装 unrar/bsdtar/7z 后重启'})
    return jsonify({'entries': entries, 'archive': rel, 'dir': inner_dir})


def _extract_cached(rel, entry_path):
    """解压到缓存目录，返回本地路径。"""
    cache_root = os.path.join(ARCHIVE_DIR, '.cache')
    os.makedirs(cache_root, exist_ok=True)
    key = hashlib.md5((rel + '|' + entry_path).encode('utf-8')).hexdigest()
    ext = os.path.splitext(entry_path)[1]
    dest = os.path.join(cache_root, key + ext)
    if os.path.exists(dest):
        return dest
    full = os.path.normpath(os.path.join(ARCHIVE_DIR, rel))
    tmp = dest + '.tmp'
    out = extract_entry_to(full, entry_path, cache_root)
    if out != dest:
        os.rename(out, dest) if os.path.exists(out) else None
    return dest


@app.route('/api/local/file')
def api_local_file():
    """流式读取压缩包内单个文件（文本/图片/视频）。"""
    rel = safe_archive_rel(request.args.get('archive', ''))
    entry = safe_archive_rel(request.args.get('entry', ''))
    if not rel or not entry:
        return Response('bad request', status=400)
    full = os.path.normpath(os.path.join(ARCHIVE_DIR, rel))
    if not full.startswith(ARCHIVE_DIR) or not os.path.isfile(full):
        return Response('not found', status=404)
    ext = os.path.splitext(entry)[1].lower()

    try:
        local_path = _extract_cached(rel, entry)
    except Exception as e:
        return Response(str(e), status=500)

    mime = 'application/octet-stream'
    if ext in TEXT_FILE_EXTS:
        mime = 'text/plain; charset=utf-8'
    elif ext in IMAGE_EXTS:
        mime = {
            '.jpg': 'image/jpeg', '.jpeg': 'image/jpeg', '.png': 'image/png', '.gif': 'image/gif',
            '.bmp': 'image/bmp', '.webp': 'image/webp', '.svg': 'image/svg+xml', '.avif': 'image/avif',
        }.get(ext, 'image/jpeg')
    elif ext in VIDEO_EXTS:
        mime = {
            '.mp4': 'video/mp4', '.mkv': 'video/x-matroska', '.avi': 'video/x-msvideo',
            '.mov': 'video/quicktime', '.webm': 'video/webm', '.m4v': 'video/mp4',
            '.ts': 'video/mp2t', '.mpg': 'video/mpeg', '.mpeg': 'video/mpeg', '.flv': 'video/x-flv',
        }.get(ext, 'video/mp4')
    return send_file(local_path, mimetype=mime, conditional=True, as_attachment=False)


@app.route('/api/local/thumb')
def api_local_thumb():
    """压缩包内图片的小尺寸缩略图（磁盘缓存，用于图集网格展示）。"""
    rel = safe_archive_rel(request.args.get('archive', ''))
    entry = safe_archive_rel(request.args.get('entry', ''))
    if not rel or not entry:
        return Response('bad request', status=400)
    full = os.path.normpath(os.path.join(ARCHIVE_DIR, rel))
    if not full.startswith(ARCHIVE_DIR) or not os.path.isfile(full):
        return Response('not found', status=404)
    size = request.args.get('size', '200')
    try:
        thumb_size = max(120, min(400, int(size)))
    except Exception:
        thumb_size = 200
    cache_root = os.path.join(ARCHIVE_DIR, '.cache')
    os.makedirs(cache_root, exist_ok=True)
    key = 'thumb_%d_%s' % (thumb_size, hashlib.md5((rel + '|' + entry).encode('utf-8')).hexdigest())
    dest = os.path.join(cache_root, key + '.jpg')
    if os.path.exists(dest):
        return send_file(dest, mimetype='image/jpeg', conditional=True)
    try:
        local_path = _extract_cached(rel, entry)
    except Exception as e:
        return Response(str(e), status=500)
    try:
        from PIL import Image, ImageOps
        im = Image.open(local_path)
        im = ImageOps.exif_transpose(im)
        im.thumbnail((thumb_size, thumb_size), Image.LANCZOS)
        im.convert('RGB').save(dest, 'JPEG', quality=82)
    except Exception as e:
        return send_file(local_path, conditional=True)
    return send_file(dest, mimetype='image/jpeg', conditional=True)


@app.route('/api/local/read')
def api_local_read():
    """读取压缩包内文本文件内容。"""
    rel = safe_archive_rel(request.args.get('archive', ''))
    entry = safe_archive_rel(request.args.get('entry', ''))
    if not rel or not entry:
        return jsonify({'error': '非法请求'})
    try:
        local_path = _extract_cached(rel, entry)
        data = open(local_path, 'rb').read()
        return jsonify({'content': decode_bytes(data), 'name': os.path.basename(entry)})
    except Exception as e:
        return jsonify({'error': str(e)})


if __name__ == '__main__':
    print("=" * 50)
    print(f"  {APP_NAME}  v{APP_VERSION}")
    print("  访问 http://127.0.0.1:5000 开始使用")
    print("=" * 50)
    app.run(host='0.0.0.0', port=5000, debug=False, threaded=True)
