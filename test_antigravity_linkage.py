"""Unit & UI integration tests for Codex <-> Antigravity Desktop dual-provider linkage."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'app'))

from PySide6.QtWidgets import QApplication, QToolButton

APP = QApplication.instance() or QApplication([])

import antigravity_adapter as ag
from desktop_core import DEFAULTS, normalized
from quota_summary_data import today_tokens
from token_calendar import usage_series


def _iso_now(offset_sec: float = 0.0) -> str:
    return datetime.fromtimestamp(time.time() + offset_sec, timezone.utc).isoformat().replace('+00:00', 'Z')


class AntigravityAdapterTests(unittest.TestCase):
    def test_defaults_and_normalization_include_linkage_provider(self):
        self.assertEqual(DEFAULTS['linkage_provider'], 'codex')
        self.assertEqual(DEFAULTS['antigravity_quota_bucket'], 'gemini')
        self.assertEqual(normalized({'linkage_provider': 'antigravity'})['linkage_provider'], 'antigravity')
        self.assertEqual(normalized({'linkage_provider': 'unknown'})['linkage_provider'], 'codex')
        self.assertEqual(normalized({'antigravity_quota_bucket': '3p'})['antigravity_quota_bucket'], '3p')
        self.assertEqual(normalized({'antigravity_quota_bucket': 'bad'})['antigravity_quota_bucket'], 'gemini')

    def test_transcript_tail_parses_user_tool_done_error_and_429(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'ag'
            tf = root / 'brain' / 'conv-abc123' / '.system_generated' / 'logs' / 'transcript.jsonl'
            tf.parent.mkdir(parents=True, exist_ok=True)
            tf.write_text('', encoding='utf-8')
            hooks_file = Path(tmp) / 'events.jsonl'

            tail = ag.AntigravityTail(home=root, hooks_file=hooks_file, complete_settle_seconds=0.0)
            self.assertEqual(tail.poll(), [])  # seed empty

            lines = [
                json.dumps({
                    'step_index': 1,
                    'type': 'USER_INPUT',
                    'status': 'DONE',
                    'created_at': _iso_now(-2),
                    'content': '<USER_REQUEST>\n帮我重构桌宠联动模块\n</USER_REQUEST>',
                }),
                json.dumps({
                    'step_index': 2,
                    'type': 'PLANNER_RESPONSE',
                    'status': 'RUNNING',
                    'created_at': _iso_now(-1),
                    'tool_calls': [{'name': 'edit_file'}],
                }),
                json.dumps({
                    'step_index': 3,
                    'type': 'PLANNER_RESPONSE',
                    'status': 'DONE',
                    'created_at': _iso_now(),
                    'model': 'MODEL_PLACEHOLDER_M37',
                    'input_tokens': 1200,
                    'cache_read_tokens': 3800,
                    'output_tokens': 500,
                }),
            ]
            with tf.open('a', encoding='utf-8') as f:
                f.write('\n'.join(lines) + '\n')

            events = tail.poll()
            kinds = [e['kind'] for e in events]
            self.assertIn('status', kinds)
            self.assertIn('complete', kinds)
            comp = next(e for e in events if e['kind'] == 'complete')
            self.assertIn('重构桌宠联动模块', comp['title'])

            # Append an error and a 429 cooldown line
            extra = [
                json.dumps({
                    'step_index': 4,
                    'type': 'PLANNER_RESPONSE',
                    'status': 'ERROR',
                    'created_at': _iso_now(),
                }),
                json.dumps({
                    'step_index': 5,
                    'type': 'SYSTEM_NOTICE',
                    'created_at': _iso_now(),
                    'content': 'HTTP 429: Your quota will reset after 1h30m0s.',
                }),
            ]
            with tf.open('a', encoding='utf-8') as f:
                f.write('\n'.join(extra) + '\n')

            events2 = tail.poll()
            self.assertTrue(any(e['kind'] == 'error' for e in events2))
            q_ev = next((e for e in events2 if e['kind'] == 'quota'), None)
            self.assertIsNotNone(q_ev)
            self.assertEqual(q_ev['windows'][0]['name'], '配额冷却')
            self.assertEqual(q_ev['windows'][0]['remaining'], 0.0)
            self.assertGreater(q_ev['windows'][0]['resetsAt'], time.time() + 5000)

    def test_hooks_file_emits_realtime_events(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'ag'
            (root / 'brain').mkdir(parents=True, exist_ok=True)
            hooks_file = Path(tmp) / 'antigravity-events.jsonl'
            tail = ag.AntigravityTail(home=root, hooks_file=hooks_file, complete_settle_seconds=0.0)
            tail.poll()  # seed

            recs = [
                {'ts': time.time(), 'event': 'SessionStart', 'conversation_id': 'c-1', 'tool_name': ''},
                {'ts': time.time(), 'event': 'PreToolUse', 'conversation_id': 'c-1', 'tool_name': 'run_command'},
                {'ts': time.time(), 'event': 'Stop', 'conversation_id': 'c-1', 'tool_name': ''},
            ]
            hooks_file.write_text('\n'.join(json.dumps(r) for r in recs) + '\n', encoding='utf-8')
            events = tail.poll()
            states = [e.get('state') for e in events if e['kind'] == 'status']
            self.assertIn('thinking', states)
            self.assertIn('working', states)
            self.assertTrue(any(e['kind'] == 'complete' for e in events))

    def test_token_index_aggregates_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'ag'
            tf = root / 'brain' / 'conv-tok' / '.system_generated' / 'logs' / 'transcript.jsonl'
            tf.parent.mkdir(parents=True, exist_ok=True)
            now = time.time()
            lines = [
                json.dumps({'step_index': 1, 'type': 'USER_INPUT', 'created_at': _iso_now(-10), 'content': 'hi'}),
                json.dumps({
                    'step_index': 2,
                    'type': 'PLANNER_RESPONSE',
                    'status': 'DONE',
                    'created_at': _iso_now(-5),
                    'model': 'gemini-3-pro',
                    'input_tokens': 1000,
                    'cache_read_tokens': 4000,
                    'output_tokens': 500,
                }),
            ]
            tf.write_text('\n'.join(lines) + '\n', encoding='utf-8')
            idx = ag.AntigravityTokenIndex(Path(tmp) / 'ag-tokens.sqlite3', home=root)
            snap1 = idx.poll(now=now)
            snap2 = idx.poll(now=now)

            self.assertEqual(snap1['total'], 5500)
            self.assertEqual(snap2['total'], 5500)
            self.assertEqual(snap1['source'], '本机 Antigravity 会话日志')
            today = today_tokens(snap1, now=now)
            self.assertTrue(today['known'])
            self.assertIn('Antigravity', today['description'])
            series = usage_series(snap1, 'day', now=now)
            self.assertTrue(series['available'])
            self.assertEqual(series['total'], 5500)

    def test_configure_hooks_safely_merges_and_cleans_user_hooks(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / 'hooks.json'
            existing_user_hook = {'type': 'command', 'command': 'echo user-hook-kept'}
            cfg.write_text(json.dumps({'hooks': {'Stop': [existing_user_hook]}}), encoding='utf-8')
            with patch.dict(os.environ, {'BAILONG_ANTIGRAVITY_HOOKS_CONFIG': str(cfg), 'LOCALAPPDATA': tmp}):
                with patch('antigravity_adapter.data_dir', return_value=Path(tmp)):
                    self.assertFalse(ag.hooks_installed(cfg))
                    ag.configure_hooks(True, cfg)
                    self.assertTrue(ag.hooks_installed(cfg))
                    saved = json.loads(cfg.read_text(encoding='utf-8'))
                    self.assertIn(existing_user_hook, saved['hooks']['Stop'])
                    self.assertGreater(len(saved['hooks']['Stop']), 1)

                    ag.configure_hooks(False, cfg)
                    self.assertFalse(ag.hooks_installed(cfg))
                    cleaned = json.loads(cfg.read_text(encoding='utf-8'))
                    self.assertEqual(cleaned['hooks']['Stop'], [existing_user_hook])

    def test_read_antigravity_quota_statusline_and_desktop_fallback(self):
        from quota_summary_data import quota_items
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'ag'
            (root / 'brain').mkdir(parents=True, exist_ok=True)
            sl = Path(tmp) / 'antigravity-statusline.json'
            with patch('antigravity_adapter.statusline_snapshot_path', return_value=sl), \
                 patch('antigravity_adapter.find_agy_cli', return_value=None):
                windows, _, code = ag.read_antigravity_quota(root)
                self.assertEqual(windows, [])
                self.assertEqual(code, 'ag_desktop_only')

                sl.write_text(json.dumps({
                    'plan_tier': 'pro',
                    'quota': {
                        'gemini-5h': {'remaining_fraction': 0.54, 'reset_in_seconds': 3600},
                        'gemini-weekly': {'remaining_fraction': 0.77, 'reset_in_seconds': 86400},
                        '3p-5h': {'remaining_fraction': 1, 'reset_time': '1970-01-01T00:00:00Z', 'reset_in_seconds': 0},
                        '3p-weekly': {'remaining_fraction': 0.563, 'reset_in_seconds': 500000},
                    },
                }), encoding='utf-8')
                windows2, source2, code2 = ag.read_antigravity_quota(root, bucket='gemini')
                self.assertEqual(code2, 'ok')
                self.assertEqual([w['remaining'] for w in windows2], [54.0, 77.0])

                # 3p-5h epoch-0 full unused window must NOT be treated as expired!
                windows3p, _, code3p = ag.read_antigravity_quota(root, bucket='3p')
                self.assertEqual(code3p, 'ok')
                self.assertEqual(windows3p[0]['remaining'], 100.0)
                self.assertIsNone(windows3p[0]['resetsAt'])
                self.assertTrue(windows3p[0].get('full_unused'))
                items3p = quota_items(windows3p)
                self.assertEqual(items3p[0]['display'], '100%')
                self.assertEqual(items3p[0]['reset_text'], '满额 · 随时可用')
                self.assertFalse(items3p[0]['expired'])

                groups = ag.read_antigravity_all_groups()
                self.assertEqual(set(groups.keys()), {'gemini', '3p'})

    def test_intermediate_stop_and_background_running_never_emit_false_complete(self):
        from status_indicator import StatusIndicator
        from conversation_roster import ConversationRoster

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'ag'
            tf = root / 'brain' / 'conv-live' / '.system_generated' / 'logs' / 'transcript.jsonl'
            tf.parent.mkdir(parents=True, exist_ok=True)
            tf.write_text('', encoding='utf-8')
            # Register a subagent that must be ignored
            sub_meta = root / 'brain' / 'conv-live' / '.system_generated' / 'subagents' / 'sub-999.json'
            sub_meta.parent.mkdir(parents=True, exist_ok=True)
            sub_meta.write_text('{}', encoding='utf-8')
            sub_tf = root / 'brain' / 'sub-999' / '.system_generated' / 'logs' / 'transcript.jsonl'
            sub_tf.parent.mkdir(parents=True, exist_ok=True)
            sub_tf.write_text('', encoding='utf-8')

            hooks_file = Path(tmp) / 'antigravity-events.jsonl'
            tail = ag.AntigravityTail(home=root, hooks_file=hooks_file, complete_settle_seconds=0.12)
            tail.poll()  # seed

            # 1) Background GENERIC step is RUNNING, then interim PLANNER_RESPONSE(DONE, tools=[]) arrives -> must NOT complete!
            with tf.open('a', encoding='utf-8') as f:
                f.write('\n'.join([
                    json.dumps({'step_index': 1, 'type': 'USER_INPUT', 'status': 'DONE', 'created_at': _iso_now(-3), 'content': '打包并发布'}),
                    json.dumps({'step_index': 2, 'type': 'GENERIC', 'status': 'RUNNING', 'created_at': _iso_now(-2), 'content': 'building...'}),
                    json.dumps({'step_index': 3, 'type': 'PLANNER_RESPONSE', 'status': 'DONE', 'created_at': _iso_now(-1), 'content': '正在上传中...'}),
                ]) + '\n')
            ev1 = tail.poll()
            self.assertFalse(any(e['kind'] == 'complete' for e in ev1))
            self.assertEqual(ev1[-1]['state'], 'working')

            # 2) Background task finishes + Stop hook fires, then PreInvocation resumes within settle window -> NO false complete!
            with tf.open('a', encoding='utf-8') as f:
                f.write(json.dumps({'step_index': 2, 'type': 'GENERIC', 'status': 'DONE', 'created_at': _iso_now()}) + '\n')
            with hooks_file.open('a', encoding='utf-8') as f:
                f.write(json.dumps({'ts': time.time(), 'event': 'Stop', 'conversation_id': 'conv-live', 'tool_name': ''}) + '\n')
            ev2 = tail.poll()
            self.assertFalse(any(e['kind'] == 'complete' for e in ev2))
            self.assertIn('conv-live', tail.pending_complete)

            with hooks_file.open('a', encoding='utf-8') as f:
                f.write(json.dumps({'ts': time.time(), 'event': 'PreInvocation', 'conversation_id': 'conv-live', 'tool_name': ''}) + '\n')
            ev3 = tail.poll()
            self.assertNotIn('conv-live', tail.pending_complete)
            self.assertEqual(ev3[-1]['state'], 'thinking')
            self.assertTrue(ev3[-1]['clear_unread'])

            # 3) Subagent transcript completion is completely ignored
            with sub_tf.open('a', encoding='utf-8') as f:
                f.write('\n'.join([
                    json.dumps({'step_index': 1, 'type': 'USER_INPUT', 'status': 'DONE', 'created_at': _iso_now()}),
                    json.dumps({'step_index': 2, 'type': 'PLANNER_RESPONSE', 'status': 'DONE', 'created_at': _iso_now()}),
                ]) + '\n')
            self.assertEqual(tail.poll(), [])

            # 4) Genuine final Stop settles after complete_settle_seconds -> emits complete
            with hooks_file.open('a', encoding='utf-8') as f:
                f.write(json.dumps({'ts': time.time(), 'event': 'Stop', 'conversation_id': 'conv-live', 'tool_name': ''}) + '\n')
            self.assertEqual(tail.poll(), [])
            time.sleep(0.16)
            ev4 = tail.poll()
            self.assertTrue(any(e['kind'] == 'complete' for e in ev4))

            # 5) StatusIndicator + Roster immediately clear unread & show busy when the session resumes working!
            ind = StatusIndicator()
            roster = ConversationRoster()
            comp = next(e for e in ev4 if e['kind'] == 'complete')
            ind.notify_complete(comp)
            roster.observe(comp)
            self.assertEqual(ind.visual(), 'attention')

            with hooks_file.open('a', encoding='utf-8') as f:
                f.write(json.dumps({'ts': time.time(), 'event': 'PreInvocation', 'conversation_id': 'conv-live', 'tool_name': ''}) + '\n')
            ev5 = tail.poll()
            for e in ev5:
                ind.observe(e)
                roster.observe(e)
            self.assertEqual(ind.unread, [])
            self.assertEqual(ind.visual(), 'busy')
            chosen, extra = roster.selection(ind.unread)
            self.assertEqual(chosen[0]['state'], 'thinking')
            self.assertEqual(extra, 0)


class DualProviderUiTests(unittest.TestCase):
    def test_settings_switcher_hot_swaps_between_codex_and_antigravity(self):
        import enhanced
        import desktop
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            cfg = tmp_dir / 'settings.json'
            with patch.object(enhanced, 'STATE_DIR', tmp_dir), \
                 patch.object(enhanced, 'CONFIG', cfg), \
                 patch.object(desktop, 'STATE_DIR', tmp_dir), \
                 patch.object(desktop, 'CONFIG', cfg), \
                 patch('desktop_settings.autostart_enabled', return_value=False), \
                 patch('codex_startup.enabled', return_value=False):
                pet = desktop.DesktopPet(monitor=False)
                try:
                    pet.open_settings()
                    APP.processEvents()
                    s = pet.settings_dialog
                    s.build_page(2)
                    APP.processEvents()

                    self.assertEqual(pet.linkage_provider(), 'codex')
                    self.assertEqual(s.tabs.tabText(2), 'Codex')
                    self.assertEqual(pet.bubble.provider, 'codex')
                    btn = pet.bubble._editor_provider_button
                    self.assertIsNotNone(btn)
                    self.assertEqual(btn.objectName(), 'open_codex')
                    self.assertEqual(btn.text(), 'Codex')

                    # Switch to Antigravity via the top switcher bar
                    ag_idx = s.provider_switch.findData('antigravity')
                    self.assertGreaterEqual(ag_idx, 0)
                    s.provider_switch.setCurrentIndex(ag_idx, False)
                    APP.processEvents()

                    self.assertEqual(pet.linkage_provider(), 'antigravity')
                    self.assertEqual(pet.bubble.provider, 'antigravity')
                    self.assertEqual(s.tabs.tabText(2), 'Antigravity')
                    self.assertEqual(pet.bubble._editor_provider_button.text(), 'Antigravity')
                    s.tabs.setCurrentIndex(2)
                    APP.processEvents()
                    self.assertTrue(s.codex_section_widget.isHidden())
                    self.assertFalse(s.ag_section_widget.isHidden())

                    # Honest desktop quota state
                    pet.ag_quota_ready([], 'Antigravity 桌面端', 'ag_desktop_only')
                    self.assertIn('Antigravity', pet.bubble.quota_caption)
                    self.assertEqual(pet.connection_health.records['quota']['code'], 'ag_desktop_only')

                    # Switch back to Codex
                    cx_idx = s.provider_switch.findData('codex')
                    s.provider_switch.setCurrentIndex(cx_idx, False)
                    APP.processEvents()
                    self.assertEqual(pet.linkage_provider(), 'codex')
                    self.assertEqual(s.tabs.tabText(2), 'Codex')
                    self.assertEqual(pet.bubble._editor_provider_button.text(), 'Codex')
                    self.assertFalse(s.codex_section_widget.isHidden())
                    self.assertTrue(s.ag_section_widget.isHidden())
                finally:
                    pet.close()
                    APP.processEvents()


if __name__ == '__main__':
    unittest.main()
