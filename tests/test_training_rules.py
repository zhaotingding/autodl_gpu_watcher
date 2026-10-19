from training_helpers import wait_training
import copy
import json
from pathlib import Path
import queue
import tempfile
import time
import unittest
from unittest.mock import patch

from autodl_watcher.training_tracker import TrainingTracker
from autodl_watcher.training_config import TrainingServer
from autodl_watcher.training_events import parse_progress
from autodl_watcher.training_ai import AnalysisError
from autodl_watcher.training_rules import validate_profile, parse_rules, RuleStore, task_identity
from autodl_watcher.training_service import TrainingService
from autodl_watcher.training_ai_ui import AIAssistant
from test_training import snapshot

LINE='ITER {"pid":10,"round":2,"count":40,"stats":{"Dice":0.81,"PSNR":32.1}}'
PROFILE={'name':'solver专用','rules':[{'format':'json','contains':'ITER ', 'phase':'train','fields':{'epoch':'round','step':'count','pid':'pid'},'metrics_path':'stats','example':LINE}]}


class RuleEngineTest(unittest.TestCase):
    def test_future_values_and_new_metrics_are_extracted_not_frozen(self):
        profile=validate_profile(PROFILE,LINE,10)
        updated='ITER {"pid":10,"round":3,"count":80,"stats":{"Dice":0.9,"PSNR":34,"new_metric":0.5}}'
        event=parse_rules(profile,updated,10)[0]
        self.assertEqual((event['epoch'],event['step']),(3,80))
        self.assertEqual(event['metrics']['new_metric'],.5)
        self.assertEqual(parse_rules(profile,updated,99),[])

    def test_default_and_custom_rules_share_nested_metric_semantics(self):
        values = {"pid": 10, "epoch": 2, "step": 30, "metrics":
                  {"quality": {"PSNR": 32.1}, "loss": 2e-6, "ready": True}}  # 布尔标志不当作数值指标。
        for body in (json.dumps(values), repr(values)):  # 同时保留标准JSON和实际Python字典日志兼容。
            with self.subTest(body=body):
                line = "2026-10-15T08:00:00+08:00 " + body
                rule = {"format": "json", "phase": "train", "metrics_path": "metrics",
                        "fields": {"pid": "pid", "epoch": "epoch", "step": "step"}, "example": line}
                profile = validate_profile({"name": "自定义指标", "rules": [rule]}, line, 10)
                text = "warming up\r\x1b[32m" + line + "\x1b[0m"  # 控制序列去除后仍须保持原记录行号。
                default = parse_progress(text, expected_pid=10)
                index, event = parse_rules(profile, text, 10, with_lines=True)[0]
                self.assertEqual(index, default["progress_line"])
                self.assertEqual(event["metrics"], default["observations"]["metrics"])
                self.assertEqual(event["metrics"], {"quality/PSNR": 32.1, "loss": 2e-6})
                self.assertEqual((event["epoch"], event["step"]), (default["epoch"], default["step"]))
                self.assertEqual(event["time"], "2026-10-15T08:00:00+08:00")

    def test_text_template_test_phase_and_branch(self):
        line="EPOCH_DONE joint 9 16182 source_best 9 val {'auc':0.98,'ce':0.2}"
        rule={'format':'text','template':'EPOCH_DONE {arm:word} {epoch:number} {step:number} source_best {best:number} val {scores:json}', 'phase':'test','fields':{'epoch':'epoch','step':'step','dataset':'arm'},'metrics_path':'scores','example':line}
        value=validate_profile({'name':'q10','rules':[rule]},line)
        event=parse_rules(value,line.replace('9 16182','10 18000').replace('0.98','0.99'),10)[0]
        self.assertEqual(event['step'],18000)
        self.assertEqual(event['metrics']['auc'],.99)
        self.assertEqual(event['dataset'],'joint')

    def test_shared_log_does_not_borrow_another_pid_validation(self):
        line="EPOCH_DONE joint 9 16182 source_best 9 val {'auc':0.98}"
        rule={'format':'text','template':'EPOCH_DONE {arm:word} {epoch:number} {step:number} source_best {best:number} val {scores:json}', 'phase':'test','fields':{'epoch':'epoch','step':'step','dataset':'arm'},'metrics_path':'scores','example':line}
        text=LINE+'\n'+ 'TRAIN {"pid":99,"step":5}'+'\n'+line
        events=parse_rules({'rules':[PROFILE['rules'][0],rule]},text,10)
        self.assertEqual(len(events),1)
        self.assertEqual(events[0]['phase'],'train')

    def test_bad_examples_constants_and_commands_rejected(self):
        for change in [{'example':'made up'},{'fields':{'epoch':2}},{'command':'ls'},{'phase':'completed'}]:
            p=copy.deepcopy(PROFILE);p['rules'][0].update(change)
            with self.subTest(change=change),self.assertRaises(ValueError):validate_profile(p,LINE,10)
        with self.assertRaises(ValueError):validate_profile(PROFILE,LINE,99)

    def test_tracker_applies_fresh_metrics_preserves_errors_and_falls_back(self):
        entry={'profile':PROFILE,'log_path':'/project/training.log'}
        tracker=TrainingTracker(TrainingServer('s','s','host'),lambda task:entry)
        card=tracker.update(snapshot(LINE))['cards'][0]
        self.assertEqual(card['progress']['train']['metrics']['Dice'],.81)
        self.assertIn('专用规则已启用',card['rule_status'])
        card=tracker.update(snapshot('Epoch 1/20 loss=0.2\n' + LINE))['cards'][0]
        self.assertNotIn('最新未覆盖行',card['rule_status'])
        later=snapshot(LINE.replace('40','80').replace('0.81','0.92'))
        card=tracker.update(later)['cards'][0]
        self.assertEqual(card['progress']['step'],80)
        self.assertEqual(card['progress']['train']['metrics']['Dice'],.92)
        card=tracker.update(snapshot('some new format, nothing matches'))['cards'][0]
        self.assertIn('不适用',card['rule_status'])
        self.assertEqual(card['progress']['train'],{})
        card=tracker.update(snapshot(LINE+'\nCUDA out of memory'))['cards'][0]
        self.assertEqual(card['level'],'error')
        self.assertEqual(tracker.update(snapshot(LINE))['cards'][0]['level'],'error')

    def test_adapted_test_names_replace_default_test_names(self):
        text='TRAIN {"pid":10,"epoch":9,"steps":15200,"loss":0.07}\nEPOCH_DONE joint 9 16182 source_best 9 val {"auc":0.98}'
        example=text.splitlines()[1]
        profile={'name':'test','rules':[{'format':'text','template':'EPOCH_DONE {arm:word} {epoch:number} {step:number} source_best {best:number} val {scores:json}', 'phase':'test','fields':{'epoch':'epoch','step':'step','dataset':'arm'},'metrics_path':'scores','example':example}]}
        tracker=TrainingTracker(TrainingServer('s','s','host'),lambda t:{'profile':profile,'log_path':'/project/training.log'})
        card=tracker.update(snapshot(text))['cards'][0]
        self.assertEqual(list(card['progress']['tests']),['joint'])

    def test_same_record_outer_and_inner_timestamps_do_not_reject_rule(self):
        line = '2026-10-07T13:04:52.402788+08:00 TRAIN {"status":"training","time":"2026-10-07T13:04:52.402028+08:00","pid":10,"epoch":10,"steps":17700,"loss":0.0916}'
        profile = {'name':'Q10','rules':[{'format':'json','contains':'TRAIN ', 'phase':'train','fields':{'epoch':'epoch','step':'steps','pid':'pid','time':'time'},'metrics':{'自定义损失':'loss'},'example':line}]}
        tracker = TrainingTracker(TrainingServer('s','s','host'), lambda t: {'profile':profile,'log_path':'/project/training.log'})
        card = tracker.update(snapshot(line))['cards'][0]
        self.assertIn('专用规则已启用', card['rule_status'])
        self.assertEqual(card['progress']['train']['metrics'], {'自定义损失':.0916})
        next_line = line.replace('17700','18000').replace('0.0916','0.08')
        card = tracker.update(snapshot(next_line))['cards'][0]
        self.assertEqual(card['progress']['step'],18000)
        self.assertEqual(card['progress']['train']['metrics'], {'自定义损失':.08})
        # 规则只覆盖TRAIN时，后续测试及未覆盖的新训练格式依然推进，不让旧规则覆盖新进度。
        evaluation = '\n2026-10-07T13:06:00+08:00 EPOCH_DONE joint 10 18200 source_best 10 val {"auc":0.99}'
        card = tracker.update(snapshot(next_line + evaluation))['cards'][0]
        self.assertEqual(card['progress']['phase'],'test')
        self.assertEqual(card['progress']['step'],18200)
        self.assertEqual(card['progress']['train']['metrics'], {'自定义损失':.08})
        self.assertEqual(card['progress']['tests']['joint/validation']['metrics']['auc'],.99)
        self.assertIn('最新未覆盖行使用默认解析',card['rule_status'])
        card = tracker.update(snapshot(next_line + '\nEpoch 11/20 step=19000 loss=0.05'))['cards'][0]
        self.assertEqual(card['progress']['step'],19000)
        self.assertEqual(card['progress']['train']['metrics']['loss'],.05)

    def test_local_profile_keeps_validation_datasets_and_dynamic_metrics(self):
        from autodl_watcher.training_rules import local_profile
        train='2026-10-10T10:00:00 TRAIN {"pid":10,"epoch":2,"steps":40,"steps_total":100,"loss":0.1}'
        test='2026-10-10T10:01:00 EVAL {"pid":10,"status":"evaluating","dataset":"custom","step":20,"metrics":{"Dice":0.8}}'
        profile=local_profile(train+'\n'+test,10)
        events=parse_rules(profile,train.replace('10:00:00','10:02:00').replace('40','80')+'\n'+test.replace('0.8','0.9,"FID":12.5'),10)
        self.assertEqual(events[0]['step'],80)
        self.assertEqual(events[1]['phase'],'test')
        self.assertEqual(events[1]['dataset'],'custom')
        self.assertEqual(events[1]['metrics']['FID'],12.5)
        self.assertIsNone(local_profile(train,99))
        self.assertIsNone(local_profile('trace {"pid":10,"epoch":2,"indexes":[1,2],"loss":0.1}',10))
        self.assertIsNone(local_profile('a completely unknown training format',10))

    def test_cache_progress_log_wins_over_newer_trace_and_local_rule_reuses_it(self):
        from autodl_watcher.training_rules import local_profile
        line='2026-10-10T16:34:42+08:00 CACHE {"status":"caching","pid":10,"steps":15600,"steps_total":21576,"frames":249600,"parameter_updates":0}'
        value=snapshot(line)
        log=value['tasks'][0]['logs'][0];log['path']='/project/train_runner.log'
        trace=dict(log,path='/project/cache_trace.jsonl',head='{"step":15601,"epoch":8,"indexes":[1,2],"image_sha256":"trace-only"}',modified_at=log['modified_at']+1)
        value['tasks'][0]['logs'].append(trace)
        tracker=TrainingTracker(TrainingServer('s','s','host'))
        card=tracker.update(value)['cards'][0]
        self.assertEqual(card['log_path'],log['path'])
        self.assertNotIn('trace-only',card['details'])
        profile=local_profile(card['details'],10)
        self.assertEqual(profile['rules'][0]['phase'],'unknown')
        tracker.rule_lookup=lambda t:{'profile':profile,'log_path':log['path']}
        value['tasks'][0]['logs'][0]['head']=line.replace('15600','15700')
        card=tracker.update(value)['cards'][0]
        self.assertIn('专用规则已启用',card['rule_status'])
        self.assertEqual(card['progress']['step'],15700)
        self.assertEqual(card['status'],'进度可读 · 阶段未确认')
        self.assertEqual(card['progress']['observations']['metrics']['parameter_updates'],0)
        self.assertEqual(card['progress']['phase'],'unknown')

    def test_rule_identity_isolated_by_server_and_process_start(self):
        s=TrainingServer('s','s','host')
        task={'key':'boot:10:100'}
        self.assertNotEqual(task_identity(s,task),task_identity(TrainingServer('b','b','other'),task))
        self.assertNotEqual(task_identity(s,task),task_identity(s,{'key':'boot:10:101'}))


class RuleAutomationTest(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'ai.yaml'
        self.path.write_text(json.dumps({'base_url':'https://example.com/v1','model':'test','auto_generate':True}))
        self.service=TrainingService(queue.Queue())
        self.ai=AIAssistant(None,self.path,self.service);self.service.ai=self.ai
        self.server=TrainingServer('s','s','host');self.service.register(self.server)
        state=self.service.states['s'];state['active']=True;state['snapshot']=state['tracker'].update(snapshot(LINE))

    def tearDown(self):self.temp.cleanup()

    def wait(self):
        deadline=time.monotonic()+3
        while self.ai.busy and time.monotonic()<deadline:
            self.ai.poll();time.sleep(.01)
        self.assertFalse(self.ai.busy);self.ai.poll();wait_training(self.service)

    def test_auto_once_reuse_after_reload_and_apply_to_card(self):
        with patch('autodl_watcher.training_ai_ui.analyze',return_value={'profile':PROFILE,'service_tier':'flex'}) as api:
            stamp=self.service.states['s']['snapshot']['received_at']
            self.ai.tick();self.wait()
            for _ in range(10):self.ai.tick()
            self.assertEqual(api.call_count,1)
            self.assertEqual(self.service.states['s']['snapshot']['received_at'],stamp)
            self.ai=AIAssistant(None,self.path,self.service);self.service.ai=self.ai
            self.ai.tick();self.assertFalse(self.ai.busy);self.ai.poll();wait_training(self.service);self.assertEqual(api.call_count,1)
            self.service.reparse('s');wait_training(self.service)
            self.assertIn('专用规则已启用',self.service.states['s']['snapshot']['cards'][0]['rule_status'])

    def test_failure_not_repeated_each_sample_or_restart(self):
        with patch('autodl_watcher.training_ai_ui.analyze',side_effect=AnalysisError('HTTP 429')) as api:
            self.ai.tick();self.wait()
            for _ in range(10):self.ai.tick()
            self.assertEqual(api.call_count,1)
            self.assertIn('429',self.service.states['s']['snapshot']['cards'][0]['rule_status'])
            self.ai=AIAssistant(None,self.path,self.service);self.service.ai=self.ai
            self.ai.tick();self.assertEqual(api.call_count,1)

    def test_failed_manual_update_preserves_applied_rule_during_request_and_after_restart(self):
        card=self.service.states['s']['snapshot']['cards'][0]
        identity=task_identity(self.server,card)
        self.ai.store.put(identity,{'profile':PROFILE,'log_path':card['log_path'],'message':'专用规则已生成'})
        from threading import Event
        release=Event()
        def fail(*args, **kwargs):
            release.wait(3)
            raise AnalysisError('AI返回空规则')
        with patch('autodl_watcher.training_ai_ui.analyze',side_effect=fail) as api:
            self.ai.generate('s',card,LINE);wait_training(self.service)
            current=self.service.states['s']['snapshot']['cards'][0]
            self.assertIn('专用规则已启用',current['rule_status'])
            self.assertEqual(current['progress']['step'],40)
            release.set();self.wait()
            current=self.service.states['s']['snapshot']['cards'][0]
            self.assertIn('保留原专用规则',current['rule_status'])
            self.assertNotIn('继续默认监控',current['rule_status'])
            self.assertEqual(self.ai.entry('s',card)['profile'],PROFILE)
            self.ai=AIAssistant(None,self.path,self.service);self.service.ai=self.ai
            for _ in range(5):self.ai.tick()
            api.assert_called_once()
            self.service.states['s']['snapshot']=self.service.states['s']['tracker'].update(snapshot(LINE.replace('40','80')))
            self.assertEqual(self.service.states['s']['snapshot']['cards'][0]['progress']['step'],80)

    def test_empty_ai_local_fallback_does_not_replace_existing_ai_profile(self):
        card=self.service.states['s']['snapshot']['cards'][0]
        self.ai.store.put(task_identity(self.server,card),{'profile':PROFILE,'log_path':card['log_path']})
        with patch('autodl_watcher.training_ai_ui.analyze',return_value={'profile':{'name':'local','rules':[]},'rule_origin':'local'}) as api:
            self.ai.generate('s',card,LINE);self.wait()
        self.assertEqual(self.ai.entry('s',card)['profile'],PROFILE)
        self.assertIn('保留原专用规则',self.service.states['s']['snapshot']['cards'][0]['rule_status'])
        api.assert_called_once()

    def test_interrupted_update_reloads_original_rule_without_paid_retry(self):
        card=self.service.states['s']['snapshot']['cards'][0]
        self.ai.store.put(task_identity(self.server,card),{'profile':PROFILE,'log_path':card['log_path'],'message':'正在更新专用规则','attempted':True})
        self.ai=AIAssistant(None,self.path,self.service);self.service.ai=self.ai
        with patch('autodl_watcher.training_ai_ui.analyze') as api:
            self.ai.tick();self.service.reparse('s');wait_training(self.service);api.assert_not_called()
        status=self.service.states['s']['snapshot']['cards'][0]['rule_status']
        self.assertIn('专用规则已启用',status)
        self.assertIn('上次规则请求未完成',status)

    def test_global_config_applies_to_new_server_but_rules_do_not_cross(self):
        self.ai.config["prompt"] = "提取新指标FID与所有验证数据集"
        other=TrainingServer('b','4090','other');self.service.register(other)
        state=self.service.states['b'];state['active']=True;state['snapshot']=state['tracker'].update(snapshot(LINE))
        with patch('autodl_watcher.training_ai_ui.analyze',return_value={'profile':PROFILE}) as api:
            self.ai.tick();self.wait();self.ai.tick();self.wait()
            self.assertEqual(api.call_count,2)
            self.assertEqual(api.call_args_list[0].args[0],api.call_args_list[1].args[0])
            self.assertEqual(api.call_args_list[1].args[0]["prompt"],"提取新指标FID与所有验证数据集")
            self.assertEqual(len(self.ai.store.entries),2)

    def test_bad_cache_pauses_auto_and_shows_reason_on_card(self):
        self.path.with_name('training_rules.yaml').write_text('{bad json')
        self.ai=AIAssistant(None,self.path,self.service);self.service.ai=self.ai
        with patch('autodl_watcher.training_ai_ui.analyze') as api:
            self.ai.tick();api.assert_not_called()
            self.service.reparse('s');wait_training(self.service)
        self.assertIn('规则缓存无法读取',self.service.states['s']['snapshot']['cards'][0]['rule_status'])

    def test_no_auto_without_opt_in_and_no_live_key_read(self):
        self.ai.config['auto_generate']=False
        with patch('autodl_watcher.training_ai_ui.analyze') as api:
            self.ai.tick();api.assert_not_called()


if __name__=='__main__':unittest.main()
