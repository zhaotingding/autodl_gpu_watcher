"""完整训练日志输入的范围、大小和现有 SSH 复用；不访问真实服务器或 AI。"""
import gc
from pathlib import Path
import queue
import sys
import tempfile
import tkinter as tk
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import weakref

from autodl_watcher import remote_probe as probe
from autodl_watcher.training_connection import TrainingConnection
from autodl_watcher.training_config import TrainingServer
from autodl_watcher.training_service import TrainingService
from autodl_watcher.training_tracker import TrainingTracker
from test_training import snapshot


class CompleteLogReadTest(unittest.TestCase):
    def read(self, path, identity):
        with patch.object(probe.os, 'getuid', return_value=path.stat().st_uid, create=True), \
             patch.object(probe.os, 'O_NONBLOCK', 0, create=True):
            return probe.read_complete_log(path, identity)

    def test_complete_file_keeps_training_head_and_tail_beyond_old_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'training.log'
            content = 'TRAIN epoch=1 loss=0.5\n' + 'CACHE frames=30\n' * 2000 + 'EVAL Dice=0.9\n'
            path.write_text(content, encoding='utf-8', newline='')
            info = path.stat()
            result = self.read(path, f'{info.st_dev}:{info.st_ino}')
            self.assertEqual(result['content'], content)
            self.assertEqual(result['size'], len(content.encode()))
            with self.assertRaisesRegex(ValueError, '身份'):
                self.read(path, 'wrong:file')

    def test_oversized_file_is_rejected_instead_of_sent_as_tail(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'training.log'
            path.write_bytes(b'x' * (4 * 1024 * 1024 + 1))
            info = path.stat()
            with self.assertRaisesRegex(ValueError, '4 MiB'):
                self.read(path, f'{info.st_dev}:{info.st_ino}')

    def test_remote_route_requires_current_task_log_and_rechecks_task_identity(self):
        request = {'operation': 'read_log', 'path': '/project/training.log', 'identity': '1:2'}
        with patch.object(probe, '_task_for_log', return_value={'key': 'task'}) as task, \
             patch.object(probe, 'task_files', return_value=['/project/training.log']), \
             patch.object(probe, 'read_complete_log', return_value={'content': 'full'}) as read:
            result = probe.read_training_log(request)
            self.assertEqual(result['log']['content'], 'full')
            self.assertEqual(task.call_count, 2)
            read.assert_called_once_with('/project/training.log', '1:2')
        with patch.object(probe, '_task_for_log', return_value={'key': 'task'}), \
             patch.object(probe, 'task_files', return_value=['/project/other.log']), \
             patch.object(probe, 'read_complete_log') as read:
            result = probe.read_training_log(request)
            self.assertIn('不属于', result['error'])
            read.assert_not_called()

    def test_task_birth_key_change_blocks_full_read(self):
        request = {'pid': 42, 'key': 'boot:42:100', 'project': '/project'}
        with patch.object(probe.Path, 'read_text', side_effect=['boot', 'btime 100']), \
             patch.object(probe.os, 'sysconf', return_value=100, create=True), \
             patch.object(probe, 'read_process', return_value={'key': 'boot:42:200', 'cwd': '/project'}):
            with self.assertRaisesRegex(ValueError, 'PID身份'):
                probe._task_for_log(request)


CHILD = '''import json,sys
json.loads(sys.stdin.readline())
for line in sys.stdin:
 request=json.loads(line)
 if request.get("operation")=="read_log":
  print(json.dumps({"operation":"read_log","log":{"path":request["path"],"identity":request["identity"],"content":"TRAIN original log","size":18}}),flush=True)
 else:
  print(json.dumps({"tasks":[],"gpus":[]}),flush=True)
'''


class FullLogConnectionTest(unittest.TestCase):
    def test_multiple_rule_windows_cannot_queue_unbounded_log_reads(self):
        connection = TrainingConnection(TrainingServer('s', 'server', 'offline'))
        connection._log_preview.acquire()
        try:
            with self.assertRaisesRegex(ValueError, '已有原日志读取'):
                connection.read_log({})
            self.assertEqual(connection.attempts, 0)
        finally:
            connection._log_preview.release()

    def test_full_read_reuses_authenticated_pipe_and_does_not_count_as_poll(self):
        connection = TrainingConnection(TrainingServer('s', 'server', 'offline'))
        self.addCleanup(connection.close)
        card = {'pid': 42, 'key': 'boot:42:100', 'log_path': '/project/training.log', 'log_identity': '1:2'}
        with patch.object(connection, '_command', return_value=[sys.executable, '-u', '-c', CHILD]):
            with self.assertRaisesRegex(OSError, '可复用'):
                connection.read_log(card)
            self.assertEqual(connection.attempts, 0)
            connection.collect()
            pid = connection.process.pid
            result = connection.read_log(card)
            self.assertEqual(result['content'], 'TRAIN original log')
            self.assertEqual((connection.attempts, connection.samples, connection.process.pid), (1, 1, pid))
            connection.collect()
            self.assertEqual((connection.attempts, connection.samples), (1, 2))

    def test_service_requires_current_card_source_identity_and_uses_active_connection_context(self):
        service = TrainingService(queue.Queue())
        server = TrainingServer('s', 'old-server', 'old-alias')
        service.register(server)
        state = service.states['s']
        card = {'key': 'task', 'alive': True, 'pid': 42, 'log_path': '/project/train.log',
                'log_identity': '1:2', 'log_sources': [{'path': '/project/train.log', 'identity': '1:2'}]}
        state['snapshot'] = {'cards': [card]}
        with patch.object(state['connection'], 'read_log', return_value={'content': 'full'}) as read:
            self.assertEqual(service.read_log('s', card)['content'], 'full')
            read.assert_called_once_with(card)
            with self.assertRaisesRegex(ValueError, '身份'):
                service.read_log('s', dict(card, log_identity='changed'))
        process = state['connection'].process = Mock(pid=123)
        process.poll.return_value = None
        state['pending'] = TrainingServer('s', 'new-server', 'new-alias')
        self.assertEqual(service.network_targets()[123], {
            'purpose': '训练 SSH · old-server', 'target': 'ssh://old-alias', 'kind': 'SSH'})

    def test_prepared_log_job_keeps_card_copy_but_not_service_or_tk(self):
        root = tk.Tk()
        root.withdraw()
        service = TrainingService(queue.Queue())
        service.ai = SimpleNamespace(root=root)  # 模拟界面适配器的间接Tk引用，不发起AI或SSH。
        connection = SimpleNamespace(read_log=Mock(return_value={'content': 'full'}))
        card = {'key': 'task', 'alive': True, 'pid': 42, 'log_path': '/project/train.log',
                'log_identity': '1:2', 'log_sources': [{'path': '/project/train.log', 'identity': '1:2'}]}
        service.states['s'] = {'snapshot': {'cards': [card]}, 'connection': connection}
        expected = dict(card)
        job = service.prepare_log_read('s', card)
        card['log_path'] = '/project/later.log'  # 准备后输入变化不能改写已验证作业的来源。
        service_ref, root_ref = weakref.ref(service), weakref.ref(root)
        root.destroy()
        del root, service
        gc.collect()  # 仅在主线程确认作业没有把已关闭Tk或服务带到后台。
        self.assertIsNone(service_ref())
        self.assertIsNone(root_ref())
        self.assertEqual(job()['content'], 'full')
        connection.read_log.assert_called_once_with(expected)

    def test_explicit_rule_source_stays_selected_instead_of_auxiliary_cache(self):
        value = snapshot('CACHE {"stage":"cache","frames":100,"total":200}')
        primary = value['tasks'][0]['logs'][0]
        primary['path'] = '/project/cache.log'
        training = dict(primary, path='/project/train.log', head='dedicated custom source', tail='')
        value['tasks'][0]['logs'].append(training)
        selected = {'log_selected': True, 'log_path': training['path'], 'message': 'manual source'}
        tracker = TrainingTracker(TrainingServer('s', 'server', 'offline'), lambda _: selected)
        card = tracker.update(value)['cards'][0]
        self.assertEqual(card['log_path'], training['path'])
        self.assertEqual({item['path'] for item in card['log_sources']}, {primary['path'], training['path']})


if __name__ == '__main__':
    unittest.main()
