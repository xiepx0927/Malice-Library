#!/usr/bin/env python3
"""Malice-Library 发布打包脚本。

用法:
    python3 build_release.py [--to v1.0.1] [--prev v1.0.0]
说明:
    生成 dist/ 下的两个包:
      完整包   Malice-Library-{to}.zip                (含全部源码, 供中途用户)
      增量包   Malice-Library-update-{prev}-{to}.zip  (仅本次变更文件 + UPDATE.md)

    变更清单由 git diff --name-status {prev} {to} 自动得出。
    省略 --prev 时只生成完整包。
"""
import argparse
import hashlib
import os
import re
import subprocess
import sys
import zipfile

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DIST_DIR = os.path.join(BASE_DIR, 'dist')

# 打包进完整包的源码文件（相对仓库根）。
FULL_ASSETS = [
    'app.py',
    'build_release.py',
    'requirements.txt',
    'README.md',
    'start.sh',
    'templates/index.html',
    'packaging/使用说明.txt',
    'packaging/启动.bat',
]
# 增量/更新包中额外附带的构建说明文档（不属于"本次变更"但随包说明更新用法）。
UPDATE_DOCS = ['UPDATE.md', 'README.md']


def git(root, *args):
    r = subprocess.run(['git', '-C', root] + list(args),
                       capture_output=True, text=True)
    if r.returncode != 0:
        return ''
    return r.stdout


def changed_files(root, to, prev):
    """git diff --name-status {prev} {to}，返回 {相对路径: 状态字母}。"""
    out = git(root, 'diff', '--name-status', prev, to)
    d = {}
    for line in out.splitlines():
        parts = line.split('\t')
        if len(parts) >= 2:
            st = parts[0][0]
            path = parts[-1].strip()
            if path and path not in ('.gitignore',):
                d[path] = st
    return d


def file_hash(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for c in iter(lambda: f.read(65536), b''):
            h.update(c)
    return h.hexdigest()[:16]


def semver(tag):
    m = re.match(r'^v?(\d+)\.(\d+)\.(\d+)', tag or '')
    if not m:
        return (0, 0, 0)
    return tuple(int(x) for x in m.groups())


def base_name(path):
    return os.path.basename(path)


def build_full(to):
    os.makedirs(DIST_DIR, exist_ok=True)
    out = os.path.join(DIST_DIR, f'Malice-Library-{to}.zip')
    files = []
    for a in FULL_ASSETS:
        p = os.path.join(BASE_DIR, a)
        if os.path.exists(p):
            files.append((a, p))
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
        for a, p in files:
            zf.write(p, a)
    return out, len(files)


def build_incremental(to, prev):
    changes = changed_files(BASE_DIR, to, prev)
    if not changes:
        return None
    os.makedirs(DIST_DIR, exist_ok=True)
    out = os.path.join(DIST_DIR, f'Malice-Library-update-{prev}-{to}.zip')

    notes = git(BASE_DIR, 'log', '--pretty=format:%h %s', f'{prev}..{to}')
    manifest = {
        'from_version': prev,
        'to_version': to,
        'files': {},
    }
    updates = []
    for path, st in changes.items():
        rel = path.replace('\\', '/')
        src = os.path.join(BASE_DIR, path)
        if os.path.isfile(src):
            manifest['files'][rel] = {'status': st, 'sha256': file_hash(src)}
            if st != 'D':
                updates.append((rel, src))

    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
        for rel, src in updates:
            zf.write(src, 'files/' + rel)
        for doc in UPDATE_DOCS:
            dp = os.path.join(BASE_DIR, doc)
            if os.path.exists(dp):
                zf.write(dp, base_name(doc))
        md = update_md(prev, to, changes, notes, manifest)
        zf.writestr('UPDATE.md', md)
        zf.writestr('manifest.json', __import__('json').dumps(manifest, ensure_ascii=False, indent=2))
    return out


def update_md(prev, to, changes, notes, manifest):
    ls = []
    for path, st in sorted(changes.items()):
        mark = {'A': '新增', 'M': '修改', 'D': '删除', 'R': '改名'}.get(st, st)
        ls.append(f'- [{mark}] `{path}`')
    ls_text = '\n'.join(ls) if ls else '- （无文件变更）'
    commit_text = notes or '（无提交说明）'
    return (
        f'# Malice-Library 增量更新包\n\n'
        f'- 从版本: `{prev}`\n'
        f'- 到版本: `{to}`\n'
        f'\n## 本次变更文件\n\n{ls_text}\n\n'
        f'## 变更说明\n\n{commit_text}\n\n'
        f'## 如何应用\n\n'
        f'1. 将 `files/` 目录下的文件，按原路径覆盖到程序根目录（如 `files/app.py` 覆盖根目录 `app.py`）。\n'
        f'2. 删除 `files/` 中标注为删除的文件（本包中不存在已删文件本体，见 `UPDATE.md` 清单）。\n'
        f'3. 重启程序。\n\n'
        f'## 图库安全\n\n'
        f'更新只涉及源码文件；`archives/` 本地图库、`cos_cookie.json` 与 `.meta.json` '
        f'绝不会被打包或覆盖。\n'
        f'完整校验清单见同包 `manifest.json`（每文件 SHA256）。\n'
        f'也可在程序内点击「↻ 检查更新」→ 一键更新，自动完成备份与替换。\n'
    )


def main():
    ap = argparse.ArgumentParser(description='Malice-Library release builder')
    ap.add_argument('--to', required=True, help='目标版本标签，如 v1.0.1')
    ap.add_argument('--prev', default=None, help='上一版本标签，如 v1.0.0；省略则只生成完整包')
    args = ap.parse_args()

    to = args.to if args.to.startswith('v') else 'v' + args.to
    prev = args.prev if (args.prev and args.prev.startswith('v')) else (('v' + args.prev) if args.prev else None)

    full, n = build_full(to)
    print(f'[OK] 完整包:  {full}  ({n} files)')

    if prev and semver(prev) < semver(to):
        inc = build_incremental(to, prev)
        if inc:
            print(f'[OK] 增量包:  {inc}')
            print('     变更文件:')
            for p, s in changed_files(BASE_DIR, to, prev).items():
                print(f'        [{s}] {p}')
        else:
            print('[WARN] 两版本间无文件变更，未生成增量包')
    else:
        print('[SKIP] 未提供 --prev 或版本不递增，跳过增量包')


if __name__ == '__main__':
    main()