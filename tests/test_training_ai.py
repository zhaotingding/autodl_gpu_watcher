import gc
import json
from functools import partial
import yaml
from pathlib import Path
import tempfile
import threading
import time
import tkinter as tk
import unittest
from unittest.mock import MagicMock, patch

import requests
from autodl_watcher import ai_credentials
from autodl_watcher.training_ai import AnalysisError, analyze, decode_result, excerpt, save_settings, settings
from autodl_watcher.training_ai_ui import AIAssistant, AISettings
from autodl_watcher.training_service import TrainingService
from autodl_watcher.training_config import TrainingServer
from test_training import snapshot
from training_helpers import wait_training
import queue

CONFIG = {"base_url": "https://example.com/v1", "protocol": "responses", "model": "example-model", "service_tier": "flex", "auto_generate": False}
SOURCE = "Epoch 2 Dice=0.81 custom_quality=4.2"
RESULT = {"name": "Dice规则", "rules": [{"format": "text", "template": "Epoch {epoch:number} Dice={dice:number} custom_quality={quality:number}", "phase": "train", "fields": {"epoch": "epoch"}, "metrics": {"Dice": "dice", "质量": "quality"}, "example": SOURCE}]}


def sse(event):
    return b"data: " + json.dumps(event).encode()


def completed():
    return sse({"type": "response.completed", "response": {"status": "completed", "service_tier": "flex", "output": [{"content": [{"type": "output_text", "text": json.dumps(RESULT)}]}]}})


class AIBackendTest(unittest.TestCase):
    def call(self, lines, config=None, code=200, failure=None):
        with patch('autodl_watcher.training_ai.ai_credentials.read',return_value='test-secret-value'), patch('autodl_watcher.training_ai.requests.Session') as factory:
            session=factory.return_value.__enter__.return_value
            response=session.post.return_value.__enter__.return_value
            response.status_code=code
            response.iter_lines.return_value=lines
            if failure:
                session.post.side_effect=failure
            try:
                result=analyze(config or CONFIG,SOURCE)
            finally:
                self.assertEqual(session.post.call_count,1)
            return result,session.post.call_args

    def test_responses_complete_and_no_redirect_or_store(self):
        result,call=self.call([completed()])
        self.assertEqual(result['profile'],RESULT)
        self.assertEqual(result['service_tier'],'flex')
        self.assertEqual(call.kwargs['json']['service_tier'],'flex')
        self.assertEqual(call.kwargs['timeout'],(10,900))
        self.assertFalse(call.kwargs['allow_redirects'])
        self.assertFalse(call.kwargs['json']['store'])
        self.assertTrue(call.kwargs['stream'])
        self.assertNotIn('test-secret-value',json.dumps(call.kwargs['json']))

    def test_chat_stop_and_done_required(self):
        lines=[sse({'choices':[{'index':0,'delta':{'content':json.dumps(RESULT)},'finish_reason':None}]}),sse({'choices':[{'index':0,'delta':{},'finish_reason':'stop'}]}),b'data: [DONE]']
        result,call=self.call(lines,{**CONFIG,'protocol':'chat'})
        self.assertEqual(result['profile'],RESULT)
        self.assertTrue(call.args[0].endswith('/chat/completions'))
        with self.assertRaisesRegex(AnalysisError,'完成标记'):
            self.call(lines[:-1],{**CONFIG,'protocol':'chat'})

    def test_eof_failed_truncated_and_http_error_do_not_retry(self):
        cases=[([],200),([sse({'type':'response.incomplete'})],200),([],429),([],302),([],401)]
        for lines,code in cases:
            with self.subTest(code=code,lines=lines), self.assertRaises(AnalysisError):
                self.call(lines,code=code)
        with self.assertRaisesRegex(AnalysisError,'截断'):
            self.call([sse({'choices':[{'delta':{},'finish_reason':'length'}]})],{**CONFIG,'protocol':'chat'})

    def test_network_error_does_not_expose_secret_or_retry(self):
        with self.assertRaises(AnalysisError) as error:
            self.call([],failure=requests.ConnectionError('Bearer test-secret-value'))
        self.assertNotIn('test-secret-value',str(error.exception))

    def test_missing_key_never_sends(self):
        with patch('autodl_watcher.training_ai.ai_credentials.read',return_value=''),patch('autodl_watcher.training_ai.requests.Session') as factory,self.assertRaisesRegex(AnalysisError,'尚未保存'):
            analyze(CONFIG,SOURCE)
        factory.assert_not_called()

    def test_save_public_settings_without_key_or_msgx_namespace(self):
        with tempfile.TemporaryDirectory() as folder, patch('autodl_watcher.training_ai.ai_credentials.save') as save:
            path=Path(folder)/'ai.yaml'
            save_settings(path,{**CONFIG,'api_key':'not-allowed'},'test-key')
            self.assertEqual(yaml.safe_load(path.read_text()),settings(CONFIG))
            save.assert_called_once_with(CONFIG['base_url'],'test-key')
            self.assertTrue(ai_credentials._target(CONFIG['base_url']).startswith('autodl-watcher/'))
            self.assertNotEqual(ai_credentials._target(CONFIG['base_url']),ai_credentials._target('https://other.example/v1'))

    def test_invalid_profile_rejected_and_no_code_accepted(self):
        with self.assertRaises(ValueError):
            decode_result(json.dumps({'name':'x','rules':[]}),SOURCE)
        with self.assertRaises(ValueError):
            decode_result(json.dumps({**RESULT,'command':'whoami'}),SOURCE)

    def test_empty_ai_rules_build_verified_local_profile_once_and_keep_usage(self):
        from test_training_rules import LINE
        from autodl_watcher.training_rules import parse_rules
        source='TRAIN {"pid":10,"epoch":2,"steps":40,"steps_total":100,"loss":0.1,"custom_quality":0.81}'
        final=sse({'type':'response.completed','response':{'status':'completed','usage':{'input_tokens':6954,'output_tokens':279},
                  'output':[{'content':[{'type':'output_text','text':'{"name":"unknown","rules":[]}'}]}]}})
        with patch('autodl_watcher.training_ai.ai_credentials.read',return_value='test-key'),patch('autodl_watcher.training_ai.requests.Session') as factory:
            response=factory.return_value.__enter__.return_value.post.return_value.__enter__.return_value
            response.status_code=200;response.iter_lines.return_value=[final]
            result=analyze(CONFIG,source,10)
            factory.return_value.__enter__.return_value.post.assert_called_once()
        self.assertEqual(result['rule_origin'],'local')
        self.assertEqual(result['call']['tokens']['input'],6954)
        event=parse_rules(result['profile'],source.replace('40','60').replace('0.81','0.9'),10)[0]
        self.assertEqual(event['step'],60)
        self.assertEqual(event['metrics']['custom_quality'],.9)
        self.assertIn('未自动重试',result['generation_note'])

    def test_standard_and_unknown_returned_tier(self):
        result,call=self.call([completed()],{**CONFIG,'service_tier':'default'})
        self.assertEqual(call.kwargs['json']['service_tier'],'default')
        self.assertEqual(call.kwargs['timeout'],(10,180))
        self.assertEqual(result['service_tier'],'flex')  # 返回等级与请求分开记录。

    def test_manual_log_is_not_silently_truncated_and_common_secrets_hidden(self):
        source='a'*13000+'\napi_key="secret123" password=pwd123 Authorization: Bearer bearer123 sk-somethinglong'
        result=excerpt(source)
        self.assertTrue(result.startswith('a'*13000))
        for secret in ('secret123','pwd123','bearer123','sk-somethinglong'):
            self.assertNotIn(secret,result)
        with self.assertRaises(AnalysisError):
            settings({**CONFIG,'base_url':'https://user:pass@example.com/v1'})

    def test_long_log_preserves_earlier_training_examples_in_request(self):
        source = SOURCE + '\n' + 'cache records without training metrics\n' * 1000
        with patch('autodl_watcher.training_ai.ai_credentials.read', return_value='test-secret-value'), \
             patch('autodl_watcher.training_ai.requests.Session') as factory:
            session = factory.return_value.__enter__.return_value
            response = session.post.return_value.__enter__.return_value
            response.status_code = 200
            response.iter_lines.return_value = [completed()]
            value = analyze(CONFIG, source)
        sent = session.post.call_args.kwargs['json']['input'][1]['content']
        self.assertEqual(sent, source)
        self.assertEqual(value['profile'], RESULT)
        self.assertGreater(len(sent), 12000)

    def test_configurable_input_budget_rejects_before_key_read_or_paid_request(self):
        with patch('autodl_watcher.training_ai.ai_credentials.read') as key, \
             patch('autodl_watcher.training_ai.requests.Session') as network:
            with self.assertRaisesRegex(AnalysisError, '超过本次上限'):
                analyze({**CONFIG, 'max_log_chars': 1000}, 'x' * 1001)
        key.assert_not_called()
        network.assert_not_called()
        self.assertEqual(settings({**CONFIG, 'max_log_chars': '300000'})['max_log_chars'], 300000)


class AIUITest(unittest.TestCase):
    def setUp(self):
        gc.collect()  # 用例反复创建Tk；旧窗口循环必须在主线程回收，不能留给日志工作线程触发GC。
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'ai.yaml'
        self.path.write_text(json.dumps(CONFIG))
        self.root=tk.Tk();self.root.withdraw()
        self.service=TrainingService(queue.Queue())
        self.assistant=AIAssistant(self.root,self.path,self.service)
        self.service.ai=self.assistant
        self.service.register(TrainingServer("s","s","host"))
        self.service.states["s"]["snapshot"]=self.service.states["s"]["tracker"].update(snapshot(SOURCE))
        self.card=self.service.states["s"]["snapshot"]["cards"][0]
        self.read_log_mock = MagicMock(return_value={
            'path': self.card['log_path'], 'content': SOURCE, 'size': len(SOURCE.encode())})
        self.read_log = patch.object(self.service, 'prepare_log_read', side_effect=lambda server_id, card:
            partial(self.read_log_mock, server_id, dict(card)))  # 只模拟已准备的只读作业；禁止实际SSH。
        self.prepare_log_mock = self.read_log.start()

    def tearDown(self):
        wait_training(self.service)  # 等本例本地重解析结束，避免把后台作业带入下一Tk解释器。
        for timer in self.root.tk.call('after','info'):
            self.root.after_cancel(timer)
        self.root.destroy();self.temp.cleanup()
        self.read_log.stop()
        self.service.ai = None
        self.assistant.root = self.assistant.service = None  # 断开测试fixture的服务/视图循环，不改变生产解析结果。
        self.root = self.service = self.assistant = self.read_log = self.read_log_mock = self.prepare_log_mock = None
        gc.collect()  # 即使其他控件留下循环，也由当前主线程完成Tcl对象释放。

    def loaded(self, dialog):
        deadline = time.monotonic() + 2
        while dialog._loading and time.monotonic() < deadline:
            self.root.update();time.sleep(.01)
        self.assertFalse(dialog._loading)

    def test_manual_window_loads_complete_selected_log_without_paid_call(self):
        full = SOURCE + '\n' + 'CACHE placeholder\n' * 2000
        self.read_log_mock.return_value = {'path': self.card['log_path'], 'content': full, 'size': len(full.encode())}
        with patch('autodl_watcher.training_ai_ui.analyze') as api:
            dialog = self.assistant.explain('s', self.card)
            self.loaded(dialog)
            self.assertEqual(dialog.source.get('1.0', 'end-1c'), full)
            self.assertIn('Token', dialog.source_info.cget('text'))
            self.assertIn(self.card['log_path'], dialog.source_info.cget('text'))
            self.read_log_mock.assert_called_once()
            self.assertFalse(dialog.card.get('log_selected'))  # 默认来源仍可随真实训练阶段演进，不锁死前置CACHE日志。
            api.assert_not_called()
            dialog.destroy()

    def test_failed_full_read_does_not_pretend_cached_tail_is_complete(self):
        self.read_log_mock.side_effect = ValueError('日志已轮转')
        with patch('autodl_watcher.training_ai_ui.analyze') as api:
            dialog = self.assistant.explain('s', self.card)
            self.loaded(dialog)
            self.assertEqual(dialog.source.get('1.0', 'end-1c'), '')
            self.assertIn('未使用短片段冒充全文', dialog.source_info.cget('text'))
            api.assert_not_called()
            dialog.destroy()

    def test_preparing_full_read_failure_is_displayed_without_starting_worker_or_paid_call(self):
        self.prepare_log_mock.side_effect = ValueError('任务身份已变化')
        with patch('autodl_watcher.training_ai_ui.threading.Thread') as worker, \
             patch('autodl_watcher.training_ai_ui.analyze') as api:
            dialog = self.assistant.explain('s', self.card)
            self.loaded(dialog)
            self.assertEqual(dialog.source.get('1.0', 'end-1c'), '')
            self.assertIn('任务身份已变化', dialog.source_info.cget('text'))
            self.assertIn('未使用短片段冒充全文', dialog.source_info.cget('text'))
            worker.assert_not_called()
            self.read_log_mock.assert_not_called()
            api.assert_not_called()
            dialog.destroy()

    def test_edited_preview_over_budget_is_rejected_before_paid_request(self):
        with patch('autodl_watcher.training_ai_ui.analyze') as api:
            dialog = self.assistant.explain('s', self.card)
            self.loaded(dialog)
            dialog.config_snapshot['max_log_chars'] = 1000
            dialog.source.delete('1.0', 'end');dialog.source.insert('1.0', 'x' * 1001)
            dialog.start()
            self.assertIn('超过本次上限', dialog.status.cget('text'))
            self.assertFalse(self.assistant.busy)
            api.assert_not_called()
            dialog.destroy()

    def test_explicit_log_selection_changes_full_read_source(self):
        alternative = '/project/actual-training.log'
        card = dict(self.card, log_sources=[{'path': self.card['log_path'], 'identity': '1:1'},
                                           {'path': alternative, 'identity': '1:2'}])
        self.read_log_mock.side_effect = lambda server_id, selected: {'path': selected['log_path'],
            'content': 'source=' + selected['log_path'], 'size': 100}
        dialog = self.assistant.explain('s', card)
        self.loaded(dialog)
        dialog.log_path.set(alternative)
        dialog.read_full_log()
        self.loaded(dialog)
        self.assertEqual(dialog.card['log_path'], alternative)
        self.assertEqual(dialog.card['log_identity'], '1:2')
        self.assertTrue(dialog.card['log_selected'])
        self.assertEqual(dialog.source.get('1.0', 'end-1c'), 'source=' + alternative)
        dialog.destroy()

    def test_settings_save_clears_key_and_does_not_call_api(self):
        with patch('autodl_watcher.training_ai.ai_credentials.read',return_value=''),patch('autodl_watcher.training_ai.ai_credentials.save') as save,patch('autodl_watcher.training_ai.requests.Session') as network:
            dialog=AISettings(self.root,self.path)
            dialog.key.insert(0,'ui-secret')
            dialog.save()
            self.assertEqual(dialog.key.get(),'')
            self.assertNotIn('ui-secret',self.path.read_text())
            self.assertIn('未保存密钥',dialog.status.cget('text'))
            save.assert_called_once();network.assert_not_called()

    def test_global_prompt_save_restore_and_usage_page_without_api(self):
        from autodl_watcher.training_ai import DEFAULT_PROMPT
        with patch('autodl_watcher.training_ai.ai_credentials.read',return_value=''), patch('autodl_watcher.training_ai.requests.Session') as api:
            dialog=AISettings(self.root,self.path,self.assistant.reload)
            dialog.prompt.delete('1.0','end');dialog.prompt.insert('1.0','监控所有分支的FID和Dice')
            dialog.save()
            self.assertEqual(self.assistant.config['prompt'],'监控所有分支的FID和Dice')
            dialog.reset_prompt();dialog.save()
            self.assertEqual(self.assistant.config['prompt'],DEFAULT_PROMPT)
            dialog.refresh_usage()
            deadline=time.monotonic()+2
            while dialog._usage_pending and time.monotonic()<deadline:
                self.root.update();time.sleep(.01)
            self.assertIn('累计：0次请求',dialog.usage_text.get('1.0','end'))
            api.assert_not_called()
            dialog.destroy()

    def test_usage_refresh_is_background_and_destroy_cancels_its_timer(self):
        release, finished = threading.Event(), threading.Event()
        with patch('autodl_watcher.training_ai.ai_credentials.read',return_value=''):
            dialog = AISettings(self.root, self.path)
        def slow_summary():
            release.wait(2); finished.set(); return '模拟汇总'
        with patch.object(dialog.usage, 'summary', side_effect=slow_summary):
            started = time.monotonic(); dialog.refresh_usage()
            self.assertLess(time.monotonic() - started, .1)
            self.assertTrue(dialog._usage_pending)
            dialog.destroy()
            self.assertIsNone(dialog._usage_timer)
            release.set()
            deadline = time.monotonic() + 1  # 沿用原一秒界限，等待期间允许 Tk 处理主线程事件。
            while not finished.is_set() and time.monotonic() < deadline:
                self.root.update()
                time.sleep(.01)
            self.assertTrue(finished.is_set())  # 后台汇总应在窗口销毁后完成，且不重新建立计时器。
            self.root.update()

    def test_generation_window_reports_current_card_fallback_and_later_recovery(self):
        from autodl_watcher.training_rules import task_identity
        identity=task_identity(self.service.states['s']['server'],self.card)
        self.assistant.store.put(identity,{'profile':RESULT,'log_path':self.card['log_path'],'message':'专用规则已生成'})
        state=self.service.states['s']
        state['snapshot']=state['tracker'].update(snapshot('a new unrecognized format'))
        dialog=self.assistant.explain('s',self.card)
        self.loaded(dialog)
        self.assertIn('当前片段未匹配规则',dialog.output.get('1.0','end'))
        self.assertNotIn('已用于主卡片',dialog.output.get('1.0','end'))
        state['snapshot']=state['tracker'].update(snapshot(SOURCE))
        dialog.after_cancel(dialog.timer);dialog.timer=None;dialog._poll()
        self.assertIn('专用规则已启用',dialog.output.get('1.0','end'))
        dialog.destroy()

    def test_manual_only_single_request_and_main_thread_result(self):
        started,release=threading.Event(),threading.Event()
        def response(config,source,pid,**kwargs):
            started.set();release.wait(3)
            return {'profile':RESULT,'model':'example-model','analyzed_at':time.time(),'service_tier':'flex'}
        card=dict(self.card)
        with patch('autodl_watcher.training_ai_ui.analyze',side_effect=response) as api:
            dialog=self.assistant.explain("s",card)
            self.loaded(dialog)
            self.root.update();api.assert_not_called()
            dialog.start();self.assertTrue(started.wait(1))
            other=self.assistant.explain("s",card);other.start()
            self.assertIn('已有 AI 请求',other.status.cget('text'))
            release.set()
            deadline=time.monotonic()+3
            while str(dialog.send.cget('state'))=='disabled' and time.monotonic()<deadline:
                self.root.update();time.sleep(.02)
            self.assertIn('Dice',dialog.output.get('1.0','end'))
            self.assertEqual(card['status'],self.card['status'])
            wait_training(self.service)
            self.assertIn('专用规则',self.service.states['s']['snapshot']['cards'][0]['rule_status'])
            api.assert_called_once()
            dialog.destroy();other.destroy();self.root.update()

    def test_close_pending_window_cancels_timer_and_preserves_lock_until_done(self):
        release=threading.Event()
        def response(config,source,pid,**kwargs):
            release.wait(3)
            raise AnalysisError('模拟失败')
        with patch('autodl_watcher.training_ai_ui.analyze',side_effect=response):
            dialog=self.assistant.explain("s",self.card)
            self.loaded(dialog)
            dialog.start();dialog.destroy()
            self.assertIsNone(dialog.timer)
            self.assertTrue(self.assistant.busy)
            release.set()
            deadline=time.monotonic()+2
            while self.assistant.busy and time.monotonic()<deadline:
                self.assistant.poll();time.sleep(.01)
            self.root.update()
            self.assertFalse(self.assistant.busy)


if __name__=='__main__':
    unittest.main()
