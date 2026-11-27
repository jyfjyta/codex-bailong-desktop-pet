"""Reproducible Windows x64 portable/cards-only release build (no raw HD animations required).

Builds a self-contained BailongEnhanced.exe folder with all Live2D Typing Companion
assets, Codex & Antigravity linkage, fonts, sounds, and licenses, then packages a
ready-to-run .zip and matching .sha256 checksum. Classic full-body Bailong
animations can be downloaded on demand inside Settings -> 角色与素材.
"""
from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'app'))
from version import VERSION

APP_NAME = 'BailongEnhanced'
DEFAULT_RELEASE_TAG = f'{VERSION}-2'

DATA_DIRS = (
    'app/fonts',
    'app/sounds',
    'app/branding',
    'app/licenses',
    'app/typing_companion/assets',
)
DATA_FILES = (
    'app/book_catalog.json',
    'app/material_catalog.json',
    'outputs/白龙桌宠素材/素材清单.json',
    'README.md',
    'NOTICE.md',
)


def _ssl_runtime_data() -> list[str]:
    import ssl
    module = Path(ssl.__file__)
    cert = module.with_name('codex-rejected-cas.pem')
    if cert.is_file():
        return ['--add-data', f'{cert};.']
    return []


def build(release_version: str | None = None, make_zip: bool = True) -> Path:
    if sys.platform != 'win32':
        raise SystemExit('build_windows.py 只能在 Windows 上运行（可通过 GitHub Actions release-build.yml 云端构建）')

    rel_ver = (release_version or os.environ.get('BAILONG_RELEASE_VERSION') or DEFAULT_RELEASE_TAG).strip()
    stamp = time.strftime('%Y%m%d-%H%M%S')
    work = ROOT / 'build' / f'windows-{rel_ver}-{stamp}'
    dist = ROOT / 'release' / f'windows-{rel_ver}-{stamp}'
    work.mkdir(parents=True, exist_ok=True)
    dist.mkdir(parents=True, exist_ok=True)

    args = [
        sys.executable,
        str(ROOT / 'tools' / 'pyinstaller_keep_files.py'),
        '--noconfirm',
        '--windowed',
        '--onedir',
        '--name', APP_NAME,
        '--icon', str(ROOT / 'app' / 'branding' / 'bailong.ico'),
        '--distpath', str(dist),
        '--workpath', str(work / 'work'),
        '--specpath', str(work),
        '--paths', str(ROOT / 'app'),
        '--exclude-module', 'imageio_ffmpeg',
        '--hidden-import', 'typing_companion',
        '--hidden-import', 'win_input_hook',
        '--hidden-import', 'privacy_guard',
        '--hidden-import', 'antigravity_adapter',
        *_ssl_runtime_data(),
        str(ROOT / 'app' / 'enhanced.py'),
    ]
    subprocess.run(args, cwd=ROOT, check=True)

    bundle = dist / APP_NAME
    if not (bundle / f'{APP_NAME}.exe').is_file():
        raise SystemExit(f'未找到构建产物：{bundle / (APP_NAME + ".exe")}')

    # Trim unused Qt multimedia/3D/QML plugins to keep the portable ZIP compact.
    try:
        from package_trim import trim_qt
        trim_qt(bundle)
    except Exception as exc:
        print(f'警告：Qt 裁剪跳过（{exc}）')

    for rel in DATA_DIRS:
        src = ROOT / rel
        if src.is_dir():
            shutil.copytree(src, bundle / rel, dirs_exist_ok=True)

    for rel in DATA_FILES:
        src = ROOT / rel
        if src.is_file():
            dst = bundle / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    # Sanity check critical runtime files before archiving
    for required in (
        f'{APP_NAME}.exe',
        'app/branding/bailong.ico',
        'app/typing_companion/assets/bongo_body_idle.png',
        'app/material_catalog.json',
        'app/book_catalog.json',
    ):
        if not (bundle / required).is_file():
            raise SystemExit(f'Windows 打包缺少关键文件：{required}')

    print(f'已构建 Windows 免安装目录：{bundle}')
    if not make_zip:
        return bundle

    archive = dist / f'{APP_NAME}-{rel_ver}-windows-x64.zip'
    top_dir = f'{APP_NAME}-{rel_ver}-windows-x64'
    with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in sorted(bundle.rglob('*')):
            if path.is_file():
                zf.write(path, f'{top_dir}/{path.relative_to(bundle).as_posix()}')

    h = hashlib.sha256()
    with archive.open('rb') as f:
        for chunk in iter(lambda: f.read(1 << 20), b''):
            h.update(chunk)
    sha_file = Path(f'{archive}.sha256')
    sha_file.write_text(f'{h.hexdigest()}  {archive.name}\n', encoding='utf-8')
    print(f'已生成 {archive} ({archive.stat().st_size / (1024 * 1024):.1f} MiB)')
    print(f'SHA-256: {h.hexdigest()}')
    return archive


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--release-version', default=None, help='发布版本号后缀（如 1.0.1-2）')
    parser.add_argument('--no-zip', action='store_true', help='仅构建免安装目录，不压缩 ZIP')
    opts = parser.parse_args()
    build(release_version=opts.release_version, make_zip=not opts.no_zip)
