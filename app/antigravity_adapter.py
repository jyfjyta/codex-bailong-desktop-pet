"""Read-only Antigravity Desktop adapter: official Hooks, local brain transcripts, token stats & statusLine quota."""
from __future__ import annotations

import calendar
import hashlib
import json
import math
import os
import re
import select
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path

from connection_health import sample
from enhanced_core import Session, clean_text, operation_kinds, operation_label, safe_time
from platform_paths import data_dir
from token_analytics import UNKNOWN, migrate as migrate_analytics, model_name, report_snapshot
from token_calendar import counter, month_bounds

HOOK_MARKER = 'bailong-antigravity-hook'
HOOK_EVENTS = ('SessionStart', 'PreInvocation', 'PostInvocation', 'PreToolUse', 'PostToolUse', 'Stop')
_AGY_FETCH_LOCK = threading.Lock()

_USER_REQ_RE = re.compile(r'<USER_REQUEST>\s*([\s\S]*?)\s*</USER_REQUEST>', re.I)
_RESET_AFTER_RE = re.compile(
    r'quota\s+will\s+reset\s+after\s+(?:(\d+)\s*d)?\s*(?:(\d+)\s*h)?\s*(?:(\d+)\s*m)?\s*(?:(\d+)\s*s)?',
    re.I,
)


def antigravity_home(home: str | Path | None = None) -> Path:
    value = home or os.environ.get('BAILONG_ANTIGRAVITY_HOME') or (Path.home() / '.gemini' / 'antigravity')
    return Path(os.path.expandvars(str(value))).expanduser().resolve()


def hooks_config_path() -> Path:
    custom = os.environ.get('BAILONG_ANTIGRAVITY_HOOKS_CONFIG')
    if custom:
        return Path(custom).expanduser().resolve()
    return (Path.home() / '.gemini' / 'config' / 'hooks.json').resolve()


def hook_bridge_script_path() -> Path:
    name = 'antigravity-hook-bridge.ps1' if sys.platform == 'win32' else 'antigravity-hook-bridge.py'
    return data_dir() / name


def hook_events_path() -> Path:
    return data_dir() / 'antigravity-events.jsonl'


def statusline_snapshot_path() -> Path:
    return data_dir() / 'antigravity-statusline.json'


def find_agy_cli() -> Path | None:
    """Locate official Antigravity CLI (`agy`) even from GUI app bundles without shell PATH."""
    explicit = os.environ.get('BAILONG_AGY')
    if explicit:
        p = Path(os.path.expandvars(explicit)).expanduser()
        if p.is_file() and (sys.platform == 'win32' or os.access(p, os.X_OK)):
            return p.resolve()
    if sys.platform == 'win32':
        local = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local')))
        pf = Path(os.environ.get('ProgramFiles', r'C:\Program Files'))
        candidates = [
            local / 'agy' / 'bin' / 'agy.exe',
            Path.home() / '.local' / 'bin' / 'agy.exe',
            pf / 'Antigravity' / 'agy.exe',
        ]
        which = shutil.which('agy.exe') or shutil.which('agy')
    else:
        candidates = [
            Path.home() / '.local' / 'bin' / 'agy',
            Path('/opt/homebrew/bin/agy'),
            Path('/usr/local/bin/agy'),
        ]
        which = shutil.which('agy')
    if which:
        candidates.insert(0, Path(which))
    for path in candidates:
        try:
            if path.is_file() and (sys.platform == 'win32' or os.access(path, os.X_OK)):
                return path.resolve()
        except OSError:
            continue
    return None


def _system_proxy_env() -> dict[str, str]:
    """Build subprocess env inheriting OS system proxies when launched outside a shell."""
    env = dict(os.environ)
    if sys.platform == 'win32':
        local = os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local'))
        extra_bins = [str(Path(local) / 'agy' / 'bin'), str(Path.home() / '.local' / 'bin')]
    else:
        extra_bins = [str(Path.home() / '.local' / 'bin'), '/opt/homebrew/bin', '/usr/local/bin']
    cur_path = env.get('PATH', '' if sys.platform == 'win32' else '/usr/bin:/bin')
    parts = [p for p in cur_path.split(os.pathsep) if p]
    for b in extra_bins:
        if b not in parts:
            parts.insert(0, b)
    env['PATH'] = os.pathsep.join(parts)
    try:
        proxies = urllib.request.getproxies()
    except Exception:
        proxies = {}
    for key, env_names in (
        ('https', ('HTTPS_PROXY', 'https_proxy')),
        ('http', ('HTTP_PROXY', 'http_proxy')),
        ('all', ('ALL_PROXY', 'all_proxy')),
    ):
        val = proxies.get(key)
        if val:
            for name in env_names:
                env.setdefault(name, val)
    env.setdefault('NO_PROXY', 'localhost,127.0.0.1,::1')
    env.setdefault('no_proxy', 'localhost,127.0.0.1,::1')
    return env


def _win_statusline_capture_ps1(out_file: Path) -> str:
    escaped = str(out_file).replace("'", "''")
    return (
        "$ErrorActionPreference = 'SilentlyContinue'\n"
        "$raw = [Console]::In.ReadToEnd()\n"
        f"$dst = '{escaped}'\n"
        "$tmp = $dst + '.tmp'\n"
        "[IO.File]::WriteAllText($tmp, $raw, [Text.UTF8Encoding]::new($false))\n"
        "Move-Item -Force -Path $tmp -Destination $dst\n"
        "Write-Output 'ok'\n"
    )


def _run_agy_windows_conpty(
    cli: Path,
    settings_file: Path,
    cwd: str,
    out_file: Path,
    env: dict[str, str],
    timeout: float,
    cancel: threading.Event | None,
) -> dict | None:
    """Run official `agy --settings <file>` invisibly inside a Windows 10/11 ConPTY (zero window flash)."""
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    if not hasattr(kernel32, 'CreatePseudoConsole'):
        return None

    PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
    EXTENDED_STARTUPINFO_PRESENT = 0x00080000
    CREATE_NO_WINDOW = 0x08000000
    CREATE_UNICODE_ENVIRONMENT = 0x00000400

    class _COORD(ctypes.Structure):
        _fields_ = [('X', ctypes.c_short), ('Y', ctypes.c_short)]

    class _STARTUPINFOW(ctypes.Structure):
        _fields_ = [
            ('cb', wintypes.DWORD), ('lpReserved', wintypes.LPWSTR),
            ('lpDesktop', wintypes.LPWSTR), ('lpTitle', wintypes.LPWSTR),
            ('dwX', wintypes.DWORD), ('dwY', wintypes.DWORD),
            ('dwXSize', wintypes.DWORD), ('dwYSize', wintypes.DWORD),
            ('dwXCountChars', wintypes.DWORD), ('dwYCountChars', wintypes.DWORD),
            ('dwFillAttribute', wintypes.DWORD), ('dwFlags', wintypes.DWORD),
            ('wShowWindow', wintypes.WORD), ('cbReserved2', wintypes.WORD),
            ('lpReserved2', ctypes.c_void_p), ('hStdInput', wintypes.HANDLE),
            ('hStdOutput', wintypes.HANDLE), ('hStdError', wintypes.HANDLE),
        ]

    class _STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [('StartupInfo', _STARTUPINFOW), ('lpAttributeList', ctypes.c_void_p)]

    class _PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [
            ('hProcess', wintypes.HANDLE), ('hThread', wintypes.HANDLE),
            ('dwProcessId', wintypes.DWORD), ('dwThreadId', wintypes.DWORD),
        ]

    kernel32.CreatePipe.argtypes = [ctypes.POINTER(wintypes.HANDLE), ctypes.POINTER(wintypes.HANDLE), ctypes.c_void_p, wintypes.DWORD]
    kernel32.CreatePipe.restype = wintypes.BOOL
    kernel32.CreatePseudoConsole.argtypes = [_COORD, wintypes.HANDLE, wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p)]
    kernel32.CreatePseudoConsole.restype = ctypes.c_long
    kernel32.ClosePseudoConsole.argtypes = [ctypes.c_void_p]
    kernel32.ClosePseudoConsole.restype = None
    kernel32.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, ctypes.POINTER(ctypes.c_size_t)]
    kernel32.InitializeProcThreadAttributeList.restype = wintypes.BOOL
    kernel32.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, wintypes.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
    kernel32.UpdateProcThreadAttribute.restype = wintypes.BOOL
    kernel32.DeleteProcThreadAttributeList.argtypes = [ctypes.c_void_p]
    kernel32.DeleteProcThreadAttributeList.restype = None
    kernel32.CreateProcessW.argtypes = [
        wintypes.LPCWSTR, wintypes.LPWSTR, ctypes.c_void_p, ctypes.c_void_p,
        wintypes.BOOL, wintypes.DWORD, ctypes.c_void_p, wintypes.LPCWSTR,
        ctypes.POINTER(_STARTUPINFOEXW), ctypes.POINTER(_PROCESS_INFORMATION),
    ]
    kernel32.CreateProcessW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.TerminateProcess.argtypes = [wintypes.HANDLE, wintypes.UINT]
    kernel32.TerminateProcess.restype = wintypes.BOOL
    kernel32.ReadFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel32.ReadFile.restype = wintypes.BOOL
    kernel32.WriteFile.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]
    kernel32.WriteFile.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    in_r, in_w = wintypes.HANDLE(), wintypes.HANDLE()
    out_r, out_w = wintypes.HANDLE(), wintypes.HANDLE()
    if not kernel32.CreatePipe(ctypes.byref(in_r), ctypes.byref(in_w), None, 0):
        return None
    if not kernel32.CreatePipe(ctypes.byref(out_r), ctypes.byref(out_w), None, 0):
        kernel32.CloseHandle(in_r); kernel32.CloseHandle(in_w)
        return None

    hpc = ctypes.c_void_p()
    attr_buf = None
    pi = _PROCESS_INFORMATION()
    got: dict | None = None
    try:
        hr = kernel32.CreatePseudoConsole(_COORD(100, 30), in_r, out_w, 0, ctypes.byref(hpc))
        if hr != 0 or not hpc.value:
            return None
        # Child side ends are duplicated by ConPTY; close our copies.
        kernel32.CloseHandle(in_r); in_r = wintypes.HANDLE()
        kernel32.CloseHandle(out_w); out_w = wintypes.HANDLE()

        size = ctypes.c_size_t(0)
        kernel32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
        attr_buf = ctypes.create_string_buffer(size.value)
        if not kernel32.InitializeProcThreadAttributeList(attr_buf, 1, 0, ctypes.byref(size)):
            return None
        if not kernel32.UpdateProcThreadAttribute(
            attr_buf, 0, PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE, hpc, ctypes.sizeof(ctypes.c_void_p), None, None
        ):
            return None

        si = _STARTUPINFOEXW()
        si.StartupInfo.cb = ctypes.sizeof(_STARTUPINFOEXW)
        si.lpAttributeList = ctypes.cast(attr_buf, ctypes.c_void_p)

        cmdline = ctypes.create_unicode_buffer(subprocess.list2cmdline([str(cli), '--settings', str(settings_file)]))
        env_block = '\0'.join(f'{k}={v}' for k, v in env.items()) + '\0\0'
        env_buf = ctypes.create_unicode_buffer(env_block)

        ok = kernel32.CreateProcessW(
            None, cmdline, None, None, False,
            EXTENDED_STARTUPINFO_PRESENT | CREATE_NO_WINDOW | CREATE_UNICODE_ENVIRONMENT,
            ctypes.cast(env_buf, ctypes.c_void_p), cwd, ctypes.byref(si), ctypes.byref(pi),
        )
        if not ok:
            return None

        stop_drain = threading.Event()
        def _drain():
            buf = ctypes.create_string_buffer(4096)
            read_n = wintypes.DWORD(0)
            while not stop_drain.is_set():
                if not kernel32.ReadFile(out_r, buf, 4096, ctypes.byref(read_n), None) or read_n.value == 0:
                    break

        drainer = threading.Thread(target=_drain, name='BailongAgyConPtyDrain', daemon=True)
        drainer.start()

        deadline = time.monotonic() + max(4.0, float(timeout))
        while time.monotonic() < deadline:
            if cancel is not None and cancel.is_set():
                break
            if out_file.is_file():
                try:
                    parsed = json.loads(out_file.read_text(encoding='utf-8-sig'))
                    if isinstance(parsed, dict) and isinstance(parsed.get('quota'), dict) and parsed['quota']:
                        got = parsed
                        break
                except (OSError, ValueError):
                    pass
            if kernel32.WaitForSingleObject(pi.hProcess, 150) == 0:
                break
        stop_drain.set()
        if pi.hProcess and kernel32.WaitForSingleObject(pi.hProcess, 0) != 0:
            written = wintypes.DWORD(0)
            kernel32.WriteFile(in_w, b'\x04\x04', 2, ctypes.byref(written), None)
            if kernel32.WaitForSingleObject(pi.hProcess, 1500) != 0:
                kernel32.TerminateProcess(pi.hProcess, 0)
                kernel32.WaitForSingleObject(pi.hProcess, 1500)
        return got
    except Exception:
        return None
    finally:
        if attr_buf is not None:
            try:
                kernel32.DeleteProcThreadAttributeList(attr_buf)
            except Exception:
                pass
        if hpc.value:
            try:
                kernel32.ClosePseudoConsole(hpc)
            except Exception:
                pass
        for h in (pi.hThread, pi.hProcess, in_r, in_w, out_r, out_w):
            if h and getattr(h, 'value', h):
                try:
                    kernel32.CloseHandle(h)
                except Exception:
                    pass


def fetch_agy_statusline_quota(timeout: float = 18.0, cancel: threading.Event | None = None) -> dict | None:
    """Silently query live quota via official `agy --settings` ephemeral statusLine (zero prompts, zero tokens)."""
    cli = find_agy_cli()
    if cli is None:
        return None
    from desktop_core import atomic_json

    with _AGY_FETCH_LOCK:
        if cancel is not None and cancel.is_set():
            return None
        with tempfile.TemporaryDirectory(prefix='bailong-agy-') as tmp:
            out_file = Path(tmp) / 'statusline.json'
            if sys.platform == 'win32':
                cap_script = Path(tmp) / 'capture.ps1'
                cap_script.write_text(_win_statusline_capture_ps1(out_file), encoding='utf-8')
                status_cmd = f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{cap_script}"'
            else:
                py = '/usr/bin/python3' if Path('/usr/bin/python3').is_file() else sys.executable
                cap_script = Path(tmp) / 'capture.py'
                cap_script.write_text(
                    'import os, pathlib, sys\n'
                    'data = sys.stdin.read()\n'
                    f'dst = pathlib.Path({str(out_file)!r})\n'
                    'tmp = dst.with_suffix(".tmp")\n'
                    'tmp.write_text(data, encoding="utf-8")\n'
                    'os.replace(tmp, dst)\n'
                    'print("ok")\n',
                    encoding='utf-8',
                )
                status_cmd = f'"{py}" "{cap_script}"'

            settings_file = Path(tmp) / 'settings.json'
            settings_file.write_text(json.dumps({
                'statusLine': {
                    'type': 'command',
                    'command': status_cmd,
                    'enabled': True,
                },
                'altScreenMode': 'never',
                'trustedWorkspaces': [tmp],
            }), encoding='utf-8')

            if sys.platform == 'win32':
                got = _run_agy_windows_conpty(cli, settings_file, tmp, out_file, _system_proxy_env(), timeout, cancel)
            else:
                import pty
                master, slave = pty.openpty()
                proc = None
                got = None
                try:
                    proc = subprocess.Popen(
                        [str(cli), '--settings', str(settings_file)],
                        cwd=tmp,
                        env=_system_proxy_env(),
                        stdin=slave,
                        stdout=slave,
                        stderr=slave,
                        close_fds=True,
                    )
                    os.close(slave)
                    slave = -1
                    deadline = time.monotonic() + max(4.0, float(timeout))
                    while time.monotonic() < deadline:
                        if cancel is not None and cancel.is_set():
                            break
                        rlist, _, _ = select.select([master], [], [], 0.2)
                        if rlist:
                            try:
                                chunk = os.read(master, 4096)
                                if not chunk and proc.poll() is not None:
                                    break
                            except OSError:
                                break
                        if out_file.is_file():
                            try:
                                parsed = json.loads(out_file.read_text(encoding='utf-8'))
                                if isinstance(parsed, dict) and isinstance(parsed.get('quota'), dict) and parsed['quota']:
                                    got = parsed
                                    break
                            except (OSError, ValueError):
                                pass
                        if proc.poll() is not None:
                            break
                finally:
                    if slave >= 0:
                        try:
                            os.close(slave)
                        except OSError:
                            pass
                    if proc is not None and proc.poll() is None:
                        try:
                            os.write(master, b'\x04\x04')
                            proc.wait(timeout=2.0)
                        except Exception:
                            try:
                                proc.terminate()
                                proc.wait(timeout=2.0)
                            except Exception:
                                try:
                                    proc.kill()
                                except OSError:
                                    pass
                    try:
                        os.close(master)
                    except OSError:
                        pass

        if not got:
            return None
        # Persist only privacy-safe quota fields (no email/cwd/transcript path).
        sanitized = {
            'fetched_at': time.time(),
            'plan_tier': got.get('plan_tier'),
            'version': got.get('version'),
            'quota': got.get('quota'),
        }
        try:
            atomic_json(statusline_snapshot_path(), sanitized)
        except OSError:
            pass
        return sanitized


def antigravity_app_paths() -> list[Path]:
    if sys.platform == 'darwin':
        candidates = [
            Path('/Applications/Antigravity.app'),
            Path.home() / 'Applications' / 'Antigravity.app',
            Path('/Applications/Antigravity IDE.app'),
            Path.home() / 'Applications' / 'Antigravity IDE.app',
        ]
        return [p for p in candidates if p.is_dir()]
    if sys.platform == 'win32':
        local = Path(os.environ.get('LOCALAPPDATA', str(Path.home() / 'AppData' / 'Local')))
        pf = Path(os.environ.get('ProgramFiles', r'C:\Program Files'))
        candidates = [
            local / 'Programs' / 'Antigravity' / 'Antigravity.exe',
            local / 'Antigravity' / 'Antigravity.exe',
            pf / 'Antigravity' / 'Antigravity.exe',
            local / 'Programs' / 'Antigravity IDE' / 'Antigravity IDE.exe',
        ]
        return [p for p in candidates if p.is_file()]
    return []


def antigravity_desktop_open() -> bool | None:
    if sys.platform not in ('darwin', 'win32'):
        return None
    try:
        import psutil
        for proc in psutil.process_iter(['name', 'exe']):
            try:
                name = (proc.info.get('name') or '').lower()
                exe = (proc.info.get('exe') or '').lower().replace('\\', '/')
                if sys.platform == 'win32':
                    if name in ('antigravity.exe', 'antigravity ide.exe') or '/antigravity/antigravity.exe' in exe:
                        return True
                else:
                    if 'antigravity' in name or '/antigravity.app/' in exe or '/antigravity ide.app/' in exe:
                        return True
            except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
                continue
        return False
    except Exception:
        if sys.platform == 'darwin':
            try:
                done = subprocess.run(['pgrep', '-fi', 'Antigravity.app'], capture_output=True, timeout=2)
                return done.returncode == 0
            except OSError:
                return None
        return None


def open_antigravity() -> bool:
    if sys.platform == 'darwin':
        for app in antigravity_app_paths():
            try:
                done = subprocess.run(['open', '-a', str(app)], capture_output=True, timeout=5)
                if done.returncode == 0:
                    return True
            except (OSError, subprocess.TimeoutExpired):
                continue
        try:
            done = subprocess.run(['open', '-a', 'Antigravity'], capture_output=True, timeout=5)
            return done.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            return False
    if sys.platform == 'win32':
        for app in antigravity_app_paths():
            try:
                subprocess.Popen([str(app)], creationflags=0x08000000)
                return True
            except OSError:
                continue
        try:
            os.startfile('Antigravity')  # type: ignore[attr-defined]
            return True
        except OSError:
            return False
    return False


def _win_hook_bridge_ps1() -> str:
    return (
        "param([string]$Event = 'unknown', [string]$OutPath = '', [string]$Marker = '')\n"
        "$ErrorActionPreference = 'SilentlyContinue'\n"
        "if (-not $OutPath) { $OutPath = Join-Path $env:LOCALAPPDATA 'BailongEnhanced\\antigravity-events.jsonl' }\n"
        "$raw = ''\n"
        "try { if ([Console]::IsInputRedirected) { $raw = [Console]::In.ReadToEnd() } } catch {}\n"
        "$payload = @{}\n"
        "try { if ($raw.Trim()) { $parsed = $raw | ConvertFrom-Json; if ($parsed) { $payload = $parsed } } } catch {}\n"
        "$cid = ''\n"
        "if ($payload.conversation_id) { $cid = [string]$payload.conversation_id }\n"
        "elseif ($env:ANTIGRAVITY_CONVERSATION_ID) { $cid = [string]$env:ANTIGRAVITY_CONVERSATION_ID }\n"
        "$tool = ''\n"
        "if ($payload.tool_name) { $tool = [string]$payload.tool_name }\n"
        "elseif ($payload.name) { $tool = [string]$payload.name }\n"
        "$st = ''\n"
        "if ($payload.status) { $st = [string]$payload.status }\n"
        "$ts = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0\n"
        "$rec = @{ ts = $ts; event = $Event; conversation_id = $cid.Substring(0, [Math]::Min(96, $cid.Length)); tool_name = $tool.Substring(0, [Math]::Min(64, $tool.Length)); status = $st.Substring(0, [Math]::Min(32, $st.Length)) }\n"
        "$json = $rec | ConvertTo-Json -Compress\n"
        "$dir = Split-Path -Parent $OutPath\n"
        "if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Force -Path $dir | Out-Null }\n"
        "$lines = @()\n"
        "if (Test-Path $OutPath) { $lines = @(Get-Content -Path $OutPath -Encoding UTF8 | Select-Object -Last 199) }\n"
        "$lines += $json\n"
        "$tmp = $OutPath + '.tmp'\n"
        "[IO.File]::WriteAllText($tmp, (($lines -join \"`n\") + \"`n\"), [Text.UTF8Encoding]::new($false))\n"
        "Move-Item -Force -Path $tmp -Destination $OutPath\n"
    )


def _hook_bridge_source() -> str:
    return (
        '#!/usr/bin/env python3\n'
        '"""Lightweight read-only Antigravity hook notifier for Bailong pet."""\n'
        'import json, os, sys, time\n'
        'from pathlib import Path\n'
        'event = sys.argv[1] if len(sys.argv) > 1 else "unknown"\n'
        'out = Path(sys.argv[2]) if len(sys.argv) > 2 else Path.home() / "Library/Application Support/BailongEnhanced/antigravity-events.jsonl"\n'
        'raw = ""\n'
        'try:\n'
        '    if not sys.stdin.isatty():\n'
        '        raw = sys.stdin.read(65536)\n'
        'except Exception:\n'
        '    raw = ""\n'
        'payload = {}\n'
        'try:\n'
        '    parsed = json.loads(raw) if raw.strip() else {}\n'
        '    if isinstance(parsed, dict):\n'
        '        payload = parsed\n'
        'except Exception:\n'
        '    payload = {}\n'
        'record = {\n'
        '    "ts": time.time(),\n'
        '    "event": event,\n'
        '    "conversation_id": str(payload.get("conversation_id") or os.environ.get("ANTIGRAVITY_CONVERSATION_ID") or "")[:96],\n'
        '    "tool_name": str(payload.get("tool_name") or payload.get("name") or "")[:64],\n'
        '    "status": str(payload.get("status") or "")[:32],\n'
        '}\n'
        'try:\n'
        '    out.parent.mkdir(parents=True, exist_ok=True)\n'
        '    lines = out.read_text(encoding="utf-8").splitlines()[-199:] if out.exists() else []\n'
        '    lines.append(json.dumps(record, ensure_ascii=False))\n'
        '    tmp = out.with_suffix(".tmp")\n'
        '    tmp.write_text("\\n".join(lines) + "\\n", encoding="utf-8")\n'
        '    os.replace(tmp, out)\n'
        'except Exception:\n'
        '    pass\n'
    )


def _is_our_hook(entry: object) -> bool:
    if not isinstance(entry, dict):
        return False
    cmd = str(entry.get('command') or '')
    if HOOK_MARKER in cmd or 'antigravity-hook-bridge.py' in cmd or 'antigravity-hook-bridge.ps1' in cmd:
        return True
    nested = entry.get('hooks')
    if isinstance(nested, list):
        return any(_is_our_hook(item) for item in nested)
    return False


def hooks_installed(config_path: Path | None = None) -> bool:
    path = config_path or hooks_config_path()
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    hooks = data.get('hooks') if isinstance(data, dict) else None
    if not isinstance(hooks, dict):
        return False
    return any(isinstance(entries, list) and any(_is_our_hook(e) for e in entries) for entries in hooks.values())


def configure_hooks(enable: bool, config_path: Path | None = None) -> bool:
    """Safely merge or remove Bailong lifecycle hooks inside ~/.gemini/config/hooks.json without touching other hooks."""
    from desktop_core import atomic_json

    path = config_path or hooks_config_path()
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(data, dict):
            data = {}
    except (OSError, ValueError):
        data = {}

    hooks = data.get('hooks')
    if not isinstance(hooks, dict):
        hooks = {}

    for ev in list(hooks.keys()):
        entries = hooks.get(ev)
        if isinstance(entries, list):
            cleaned = [e for e in entries if not _is_our_hook(e)]
            if cleaned:
                hooks[ev] = cleaned
            else:
                hooks.pop(ev, None)

    if enable:
        script = hook_bridge_script_path()
        script.parent.mkdir(parents=True, exist_ok=True)
        events_file = hook_events_path()
        if sys.platform == 'win32':
            script.write_text(_win_hook_bridge_ps1(), encoding='utf-8')
            for ev in HOOK_EVENTS:
                cmd = f'powershell.exe -NoProfile -ExecutionPolicy Bypass -File "{script}" -Event {ev} -OutPath "{events_file}" -Marker {HOOK_MARKER}'
                entry: dict
                if ev in ('PreToolUse', 'PostToolUse'):
                    entry = {'matcher': '.*', 'hooks': [{'type': 'command', 'command': cmd}]}
                else:
                    entry = {'type': 'command', 'command': cmd}
                existing = hooks.get(ev)
                if not isinstance(existing, list):
                    existing = []
                existing.append(entry)
                hooks[ev] = existing
        else:
            script.write_text(_hook_bridge_source(), encoding='utf-8')
            try:
                script.chmod(0o755)
            except OSError:
                pass
            py = '/usr/bin/python3' if Path('/usr/bin/python3').is_file() else 'python3'
            for ev in HOOK_EVENTS:
                cmd = f'{py} "{script}" {ev} "{events_file}" # {HOOK_MARKER}'
                entry = (
                    {'matcher': '.*', 'hooks': [{'type': 'command', 'command': cmd}]}
                    if ev in ('PreToolUse', 'PostToolUse')
                    else {'type': 'command', 'command': cmd}
                )
                existing = hooks.get(ev)
                if not isinstance(existing, list):
                    existing = []
                existing.append(entry)
                hooks[ev] = existing

    data['hooks'] = hooks
    atomic_json(path, data)
    return enable


def _parse_reset_after(text: str, stamp: float) -> float | None:
    if not isinstance(text, str) or 'reset after' not in text.lower():
        return None
    m = _RESET_AFTER_RE.search(text)
    if not m:
        return None
    days = int(m.group(1) or 0)
    hours = int(m.group(2) or 0)
    mins = int(m.group(3) or 0)
    secs = int(m.group(4) or 0)
    delta = days * 86400 + hours * 3600 + mins * 60 + secs
    if delta <= 0:
        return None
    return stamp + delta


def _extract_user_title(content: str) -> str:
    if not isinstance(content, str):
        return ''
    m = _USER_REQ_RE.search(content)
    raw = m.group(1) if m else content
    return clean_text(raw, 42)


def _discover_transcripts(home: Path, limit: int | None = None) -> list[Path]:
    roots = [home / 'brain']
    # Unify Desktop & official CLI transcripts when using the default ~/.gemini/antigravity home.
    default_home = (Path.home() / '.gemini' / 'antigravity').resolve()
    if home == default_home:
        cli_brain = (Path.home() / '.gemini' / 'antigravity-cli' / 'brain').resolve()
        if cli_brain.is_dir() and cli_brain not in roots:
            roots.append(cli_brain)
    found: list[tuple[float, Path]] = []
    seen: set[str] = set()
    for brain in roots:
        if not brain.is_dir():
            continue
        try:
            for entry in brain.iterdir():
                if not entry.is_dir():
                    continue
                tf = entry / '.system_generated' / 'logs' / 'transcript.jsonl'
                try:
                    if tf.is_file():
                        resolved = str(tf.resolve())
                        if resolved in seen:
                            continue
                        seen.add(resolved)
                        found.append((tf.stat().st_mtime, tf))
                except OSError:
                    continue
        except OSError:
            continue
    found.sort(key=lambda item: item[0], reverse=True)
    files = [p for _, p in found]
    return files[:limit] if limit is not None else files


def _parse_statusline_windows(payload: dict, bucket: str = 'gemini', fetched_at: float | None = None) -> list[dict]:
    quota = payload.get('quota') if isinstance(payload, dict) else None
    if not isinstance(quota, dict):
        return []
    prefix = '3p' if bucket == '3p' else 'gemini'
    base_ts = float(fetched_at or payload.get('fetched_at') or time.time())
    plan = payload.get('plan_tier')
    windows: list[dict] = []
    for key, label in ((f'{prefix}-5h', '5小时'), (f'{prefix}-weekly', '一周')):
        item = quota.get(key)
        if not isinstance(item, dict):
            continue
        frac = item.get('remaining_fraction')
        if isinstance(frac, (int, float)) and not isinstance(frac, bool) and math.isfinite(frac):
            rem = round(max(0.0, min(100.0, float(frac) * 100.0)), 1)
        else:
            rem = None
        reset_raw = item.get('reset_time')
        resets_at = safe_time(reset_raw) if reset_raw else None
        if resets_at is not None and resets_at <= 86400:
            resets_at = None
        if resets_at is None:
            in_sec = item.get('reset_in_seconds')
            if isinstance(in_sec, (int, float)) and not isinstance(in_sec, bool) and in_sec > 0:
                resets_at = base_ts + float(in_sec)
        w_dict: dict = {'name': label, 'remaining': rem, 'resetsAt': resets_at, 'bucket': prefix}
        if resets_at is None and rem is not None and rem >= 99.9:
            w_dict['full_unused'] = True
        if plan:
            w_dict['plan'] = str(plan)[:40].lower()
        windows.append(w_dict)
    return windows


def _read_cached_statusline(max_age: float = 6 * 3600) -> tuple[dict | None, float]:
    sl_path = statusline_snapshot_path()
    if not sl_path.is_file():
        return None, 0.0
    try:
        st = sl_path.stat()
        age = max(0.0, time.time() - st.st_mtime)
        if age > max_age:
            return None, age
        payload = json.loads(sl_path.read_text(encoding='utf-8'))
        if isinstance(payload, dict) and isinstance(payload.get('quota'), dict) and payload['quota']:
            return payload, age
    except (OSError, ValueError, TypeError):
        pass
    return None, 0.0


def read_antigravity_all_groups() -> dict[str, list[dict]]:
    """Return both Gemini and Claude/GPT quota groups from the latest cached statusLine snapshot."""
    payload, _ = _read_cached_statusline(max_age=24 * 3600)
    if not payload:
        return {}
    fetched_at = float(payload.get('fetched_at') or time.time())
    groups: dict[str, list[dict]] = {}
    for bucket in ('gemini', '3p'):
        wins = _parse_statusline_windows(payload, bucket=bucket, fetched_at=fetched_at)
        if wins:
            groups[bucket] = wins
    return groups


def read_antigravity_quota(
    home: str | Path | None = None,
    bucket: str = 'gemini',
    live: bool = True,
    max_cache_age: float = 60.0,
    cancel: threading.Event | None = None,
) -> tuple[list[dict], str, str]:
    """Read documented Antigravity quota via official agy CLI statusLine JSON or active 429 cooldown notice.

    Returns (windows, source_label, status_code):
      - status_code == 'ok' when real percentage windows are read from documented statusLine JSON.
      - status_code == 'ag_rate_limited' when an active 429 cooldown timestamp is observed in recent transcripts.
      - status_code == 'ag_desktop_only' when agy CLI is not installed and no statusLine snapshot exists.
    """
    now = time.time()
    cached, age = _read_cached_statusline(max_age=6 * 3600)

    # Fast path: use fresh cache without spawning agy subprocess
    if cached and (not live or age <= max_cache_age):
        windows = _parse_statusline_windows(cached, bucket=bucket)
        if windows:
            return windows, 'agy CLI', 'ok'

    # Live path: silently run official `agy --settings` ephemeral statusLine (zero prompts, zero tokens)
    if live and find_agy_cli() is not None:
        fresh = fetch_agy_statusline_quota(cancel=cancel)
        if fresh:
            windows = _parse_statusline_windows(fresh, bucket=bucket)
            if windows:
                return windows, 'agy CLI', 'ok'
        if cached:
            windows = _parse_statusline_windows(cached, bucket=bucket)
            if windows:
                return windows, 'agy CLI 缓存', 'ok'
    elif cached:
        windows = _parse_statusline_windows(cached, bucket=bucket)
        if windows:
            return windows, 'statusLine', 'ok'

    # Fallback: check recent Antigravity Desktop transcripts for an active HTTP 429 reset notice
    root = antigravity_home(home)
    latest_reset: float | None = None
    for tf in _discover_transcripts(root, limit=6):
        try:
            size = tf.stat().st_size
            with tf.open('rb') as f:
                if size > 65536:
                    f.seek(size - 65536)
                    f.readline()
                for line in f:
                    if b'reset after' not in line.lower():
                        continue
                    try:
                        obj = json.loads(line)
                    except ValueError:
                        continue
                    stamp = safe_time(obj.get('created_at'))
                    reset_ts = _parse_reset_after(str(obj.get('content') or ''), stamp)
                    if reset_ts and reset_ts > now and (latest_reset is None or reset_ts > latest_reset):
                        latest_reset = reset_ts
        except OSError:
            continue

    if latest_reset is not None:
        return [{'name': '配额冷却', 'remaining': 0.0, 'resetsAt': latest_reset}], 'Antigravity', 'ag_rate_limited'

    return [], 'Antigravity 桌面端', 'ag_desktop_only'


class AntigravityTail:
    """Read-only tailer combining ~/.gemini/antigravity/brain/*/transcript.jsonl & official Hooks events."""

    def __init__(self, home: str | Path | None = None, hooks_file: Path | None = None) -> None:
        self.home = antigravity_home(home)
        self.hooks_file = hooks_file or hook_events_path()
        self.offsets: dict[str, int] = {}
        self.hook_offset = 0
        self.sessions: dict[str, Session] = {}
        self.seeded = False
        self.files: list[Path] = []
        self.health = sample('unknown', 'starting')

    def _status_event(self, sid: str, s: Session, activity: str = 'commentary', turn_started: bool = False) -> dict:
        return dict(
            kind='status',
            session=sid,
            state=s.state,
            text=s.message,
            title=s.title,
            turn=s.turn,
            activity=activity,
            display=s.display or s.message,
            compacting=False,
            waiting_reason='',
            waiting=0,
            user_message=turn_started,
            turn_started=turn_started,
            active=sum(not x.done for x in self.sessions.values()),
        )

    def _apply_transcript_line(self, sid: str, obj: dict, historical: bool) -> list[dict]:
        s = self.sessions.setdefault(sid, Session(title='Antigravity 对话', message='等待 Antigravity 新消息'))
        s.updated = time.monotonic()
        typ = str(obj.get('type') or '')
        status = str(obj.get('status') or '')
        stamp = safe_time(obj.get('created_at'))
        events: list[dict] = []

        if typ == 'USER_INPUT':
            title = _extract_user_title(str(obj.get('content') or ''))
            if title:
                s.title = title
            s.turn = str(obj.get('step_index', stamp))
            s.done = False
            s.state = 'thinking'
            s.message = ('正在处理：' + s.title) if title else '收到任务，正在思考…'
            s.display = '正在思考'
            if not historical:
                events.append(self._status_event(sid, s, activity='thinking', turn_started=True))
            return events

        if typ == 'PLANNER_RESPONSE':
            tool_calls = obj.get('tool_calls') or []
            if isinstance(tool_calls, list) and tool_calls:
                kinds: list[str] = []
                for tc in tool_calls:
                    if isinstance(tc, dict):
                        for k in operation_kinds({'name': tc.get('name', '')}):
                            if k not in kinds:
                                kinds.append(k)
                label = operation_label(kinds or ['tool'], finished=False)
                s.done = False
                s.state = 'working'
                s.message = label
                s.display = label
                if not historical:
                    events.append(self._status_event(sid, s, activity=(kinds[0] if kinds else 'tool')))
                return events

            if status == 'DONE':
                was_active = not s.done
                s.done = True
                s.state = 'complete'
                s.message = '任务已完成，可以查看 Antigravity 回复啦。'
                s.display = '任务已完成'
                if not historical and was_active:
                    events.append(dict(kind='complete', session=sid, turn=s.turn, title=s.title, text=s.message))
                    events.append(self._status_event(sid, s, activity='commentary'))
                return events

            if status in ('ERROR', 'FAILED', 'CANCELLED'):
                s.done = True
                s.state = 'interrupted'
                s.message = 'Antigravity 任务中断或出错'
                s.display = s.message
                if not historical:
                    events.append(dict(kind='error', session=sid, title=s.title, text=s.message))
                    events.append(self._status_event(sid, s))
                return events

        # Detect HTTP 429 quota cooldown announcements inside system messages
        reset_ts = _parse_reset_after(str(obj.get('content') or ''), stamp)
        if reset_ts and reset_ts > time.time() and not historical:
            events.append(dict(
                kind='quota',
                windows=[{'name': '配额冷却', 'remaining': 0.0, 'resetsAt': reset_ts}],
                observed=stamp,
                source='session',
            ))
        return events

    def _poll_hooks(self) -> list[dict]:
        if not self.hooks_file.is_file():
            return []
        try:
            size = self.hooks_file.stat().st_size
        except OSError:
            return []
        if not self.seeded:
            self.hook_offset = size
            return []
        if size < self.hook_offset:
            self.hook_offset = 0
        if size == self.hook_offset:
            return []

        events: list[dict] = []
        try:
            with self.hooks_file.open('rb') as f:
                f.seek(self.hook_offset)
                for raw in f:
                    if not raw.endswith(b'\n'):
                        break
                    self.hook_offset = f.tell()
                    try:
                        rec = json.loads(raw)
                    except ValueError:
                        continue
                    ev = str(rec.get('event') or '')
                    sid = str(rec.get('conversation_id') or 'antigravity-hook')
                    s = self.sessions.setdefault(sid, Session(title='Antigravity 对话', message='等待 Antigravity 新消息'))
                    s.updated = time.monotonic()
                    if ev in ('SessionStart', 'PreInvocation'):
                        s.done = False
                        s.state = 'thinking'
                        s.message = 'Antigravity 正在思考…'
                        s.display = '正在思考'
                        events.append(self._status_event(sid, s, activity='thinking', turn_started=(ev == 'SessionStart')))
                    elif ev == 'PreToolUse':
                        tool = str(rec.get('tool_name') or 'tool')
                        kinds = operation_kinds({'name': tool}) or ['tool']
                        s.done = False
                        s.state = 'working'
                        s.message = operation_label(kinds, finished=False)
                        s.display = s.message
                        events.append(self._status_event(sid, s, activity=kinds[0]))
                    elif ev == 'PostToolUse':
                        tool = str(rec.get('tool_name') or 'tool')
                        kinds = operation_kinds({'name': tool}) or ['tool']
                        s.done = False
                        s.state = 'working'
                        s.message = operation_label(kinds, finished=True)
                        s.display = s.message
                        events.append(self._status_event(sid, s, activity=kinds[0]))
                    elif ev == 'Stop':
                        was_active = not s.done
                        s.done = True
                        s.state = 'complete'
                        s.message = 'Antigravity 任务已完成'
                        s.display = '任务已完成'
                        if was_active:
                            events.append(dict(kind='complete', session=sid, turn=s.turn or str(rec.get('ts', '')), title=s.title, text=s.message))
                        events.append(self._status_event(sid, s))
        except OSError:
            pass
        return events

    def poll(self) -> list[dict]:
        files = _discover_transcripts(self.home, limit=16)
        self.files = files
        events: list[dict] = []
        historical = not self.seeded

        for tf in files:
            sid = tf.parents[2].name
            key = str(tf)
            try:
                size = tf.stat().st_size
            except OSError:
                continue
            offset = self.offsets.get(key)
            if offset is None:
                # On first seed, inspect only the tail (~48KB) so startup stays instant even on large transcripts
                start = max(0, size - 49152)
                try:
                    with tf.open('rb') as f:
                        f.seek(start)
                        if start > 0:
                            f.readline()
                        for raw in f:
                            if not raw.endswith(b'\n'):
                                break
                            offset = f.tell()
                            try:
                                obj = json.loads(raw)
                            except ValueError:
                                continue
                            events.extend(self._apply_transcript_line(sid, obj, historical=True))
                        self.offsets[key] = f.tell()
                except OSError:
                    continue
                continue

            if size < offset:
                offset = 0
            if size == offset:
                continue
            try:
                with tf.open('rb') as f:
                    f.seek(offset)
                    for raw in f:
                        if not raw.endswith(b'\n'):
                            break
                        offset = f.tell()
                        try:
                            obj = json.loads(raw)
                        except ValueError:
                            continue
                        events.extend(self._apply_transcript_line(sid, obj, historical=historical))
                    self.offsets[key] = offset
            except OSError:
                continue

        events.extend(self._poll_hooks())
        self.seeded = True

        # Auto-expire stale active sessions if transcript stopped updating > 120s ago
        now_m = time.monotonic()
        for sid, s in list(self.sessions.items()):
            if not s.done and (now_m - s.updated) > 120.0:
                s.done = True
                s.state = 'idle'
                s.message = '等待 Antigravity 新消息'
                s.display = s.message
                events.append(self._status_event(sid, s))

        if files or hooks_installed():
            self.health = sample('available', 'ok' if files else 'logs_idle', time.time())
        else:
            self.health = sample('degraded', 'logs_missing')
        return events


class AntigravityTokenIndex:
    """Incremental local token counter for Antigravity Desktop transcripts (compatible with TokenUsageIndex snapshots)."""

    def __init__(self, db_path: Path, home: str | Path | None = None) -> None:
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.home = antigravity_home(home)

    def connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path, timeout=5)
        db.execute('CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, offset INTEGER, highwater INTEGER)')
        db.execute('CREATE TABLE IF NOT EXISTS usage (fingerprint TEXT PRIMARY KEY, stamp REAL, tokens INTEGER)')
        db.execute('CREATE INDEX IF NOT EXISTS usage_stamp ON usage(stamp)')
        migrate_analytics(db)
        return db

    def _scan_transcript(self, db: sqlite3.Connection, tf: Path, stop) -> bool:
        key = str(tf.resolve())
        size = tf.stat().st_size
        row = db.execute(
            'SELECT offset, highwater, session, model, turn, input_water, output_water FROM files WHERE path=?',
            (key,),
        ).fetchone()
        offset, previous, sid, model, turn, pi, po = row or (0, 0, tf.parents[2].name, UNKNOWN, '', 0, 0)
        if size < offset:
            offset, previous, sid, model, turn, pi, po = 0, 0, tf.parents[2].name, UNKNOWN, '', 0, 0
        if size == offset and row:
            return True

        with tf.open('rb') as stream:
            stream.seek(offset)
            while not stop():
                line = stream.readline(4 * 1024 * 1024)
                if not line:
                    break
                if not line.endswith(b'\n'):
                    if len(line) < 4 * 1024 * 1024:
                        break
                    while line and not line.endswith(b'\n') and not stop():
                        line = stream.readline(4 * 1024 * 1024)
                    if not line.endswith(b'\n'):
                        break
                    offset = stream.tell()
                    continue
                offset = stream.tell()
                if b'PLANNER_RESPONSE' not in line and b'USER_INPUT' not in line:
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(obj, dict):
                    continue
                stamp = safe_time(obj.get('created_at'))
                typ = obj.get('type')
                step_idx = obj.get('step_index', '')
                if typ == 'USER_INPUT':
                    fp = hashlib.sha256(f'ag:msg:{sid}:{step_idx}:user'.encode()).hexdigest()
                    db.execute('INSERT OR IGNORE INTO messages VALUES (?,?,?,?)', (fp, stamp, sid, 'user'))
                    continue
                if typ != 'PLANNER_RESPONSE':
                    continue
                fp_a = hashlib.sha256(f'ag:msg:{sid}:{step_idx}:assistant'.encode()).hexdigest()
                db.execute('INSERT OR IGNORE INTO messages VALUES (?,?,?,?)', (fp_a, stamp, sid, 'assistant'))

                in_tok = counter(obj.get('input_tokens')) or 0
                cache_tok = counter(obj.get('cache_read_tokens')) or 0
                out_tok = counter(obj.get('output_tokens')) or 0
                total_in = in_tok + cache_tok
                total = total_in + out_tok
                if total <= 0:
                    continue
                m = model_name(obj.get('model')) or model or UNKNOWN
                model = m
                fp_u = hashlib.sha256(f'ag:tok:{sid}:{step_idx}:{total_in}:{out_tok}'.encode()).hexdigest()
                db.execute(
                    'INSERT OR IGNORE INTO usage (fingerprint, stamp, tokens, session, model, input_tokens, output_tokens, known) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, 1)',
                    (fp_u, stamp, total, sid, m, total_in, out_tok),
                )
        db.execute('INSERT OR REPLACE INTO files VALUES (?, ?, ?, ?, ?, ?, ?, ?)', (key, offset, previous, sid, model, turn, pi, po))
        return True

    def poll(self, now: float | None = None, stop=lambda: False) -> dict:
        now = time.time() if now is None else now
        files = _discover_transcripts(self.home)
        errors = 0
        readable = 0
        with closing(self.connect()) as db, db:
            for tf in files:
                if stop():
                    break
                try:
                    self._scan_transcript(db, tf, stop)
                    readable += 1
                except OSError:
                    errors += 1
            start, _ = month_bounds(now)
            days = {f'{start:%Y-%m}-{day:02}': 0 for day in range(1, calendar.monthrange(start.year, start.month)[1] + 1)}
            history = dict(db.execute(
                "SELECT date(stamp,'unixepoch','localtime'), SUM(tokens) FROM usage WHERE stamp<=? GROUP BY date(stamp,'unixepoch','localtime')",
                (now,),
            ))
            for day, val in history.items():
                if day in days:
                    days[day] = val
            current = datetime.fromtimestamp(now)
            midnight = current.replace(hour=0, minute=0, second=0, microsecond=0)
            hours = [0] * 24
            for hour, val in db.execute(
                "SELECT strftime('%H',stamp,'unixepoch','localtime'), SUM(tokens) FROM usage WHERE stamp>=? AND stamp<=? "
                "GROUP BY strftime('%H',stamp,'unixepoch','localtime')",
                (midnight.timestamp(), now),
            ):
                hours[int(hour)] = val
            result = {
                'report': report_snapshot(db, now),
                'month': start.strftime('%Y-%m'),
                'days': days,
                'total': sum(days.values()),
                'history_days': history,
                'history_start': min(history, default=None),
                'all_total': sum(history.values()),
                'today': current.strftime('%Y-%m-%d'),
                'hours': hours,
                'checked': now,
                'partial': bool(errors),
                'source': '本机 Antigravity 会话日志',
                'log_files': len(files),
                'readable_files': readable,
                'format_unknown': False,
            }
            if not readable and not history:
                result['unavailable'] = True
                result['report'] = {}
            return result


def check_antigravity_connection(
    home: str | Path | None = None,
    bucket: str = 'gemini',
    cancel: threading.Event | None = None,
) -> dict:
    from desktop_core import VERSION

    root = antigravity_home(home)
    app_paths = antigravity_app_paths()
    desktop_open = antigravity_desktop_open()
    cli_path = find_agy_cli()
    transcripts = _discover_transcripts(root)
    hooks_on = hooks_installed()
    windows, q_source, q_code = read_antigravity_quota(root, bucket=bucket, live=True, max_cache_age=20.0, cancel=cancel)
    known_quota = any(isinstance(w.get('remaining'), (int, float)) for w in windows)
    groups = read_antigravity_all_groups() if known_quota else {}

    caps = {
        'tasks': sample('available' if (transcripts or hooks_on) else 'degraded', 'ok' if transcripts else 'logs_idle', time.time() if transcripts else None),
        'completion': sample('available' if (transcripts or hooks_on) else 'degraded', 'ok' if transcripts else 'logs_idle', time.time() if transcripts else None),
        'read': sample('degraded', 'ag_read_manual'),
        'tokens': sample('available' if transcripts else 'unavailable', 'ok' if transcripts else 'token_missing', time.time() if transcripts else None),
        'quota': sample('available' if known_quota else 'degraded', q_code, time.time() if known_quota else None),
        'compaction': sample('available' if hooks_on else 'degraded', 'ok' if hooks_on else 'ag_hooks_optional', time.time() if hooks_on else None),
    }

    def _fmt_group(items: list[dict]) -> str:
        parts = []
        for w in items:
            rem = w.get('remaining')
            pct = f'{rem:g}%' if isinstance(rem, (int, float)) else '—'
            parts.append(f"{w['name']} {pct}")
        return ' / '.join(parts)

    quota_line = '账户剩余额度%：'
    if known_quota and groups:
        seg = []
        if 'gemini' in groups:
            seg.append('Gemini（' + _fmt_group(groups['gemini']) + '）')
        if '3p' in groups:
            seg.append('Claude & GPT（' + _fmt_group(groups['3p']) + '）')
        quota_line += f'已通过 {q_source} 实时获取 — ' + '；'.join(seg)
    elif known_quota:
        quota_line += f'已通过 {q_source} 读取（' + _fmt_group(windows) + '）'
    else:
        quota_line += '暂未获取到剩余%（安装官方 agy CLI 并登录即可自动同步）'

    lines = [
        'Antigravity 连接检查',
        '桌面应用：' + ('已运行' if desktop_open is True else '已安装（未运行）' if app_paths else ('未检测到 Antigravity.exe' if sys.platform == 'win32' else '未检测到 /Applications/Antigravity.app')),
        '官方 agy CLI（后台查额度）：' + (f'已找到（{cli_path}）' if cli_path else '未安装（安装后可自动读取官方剩余%）'),
        f'本地会话日志：已发现 {len(transcripts)} 个对话记录（支持实时状态与本地 Token 统计）',
        '官方 Hooks 实时推送：' + ('已启用（毫秒级状态同步）' if hooks_on else '未启用（可点上方按钮一键启用；未启用时自动轮询本地日志）'),
        quota_line,
        '数据目录：' + str(root),
    ]
    return dict(
        version=VERSION,
        checked=time.time(),
        provider='antigravity',
        desktop_open=desktop_open,
        cli_found=bool(cli_path),
        home=str(root),
        transcript_count=len(transcripts),
        hooks_installed=hooks_on,
        quota_windows=windows,
        quota_groups=groups,
        quota_read=known_quota,
        capabilities=caps,
        summary='\n'.join(lines),
    )
