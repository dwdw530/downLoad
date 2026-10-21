"""批量操作接口：状态筛选、计数、顺序、调度暂缓与删除记录边界（隔离环境）。"""
import time
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from downloader.core.task_manager import BatchBusyError
from test_features import FeatureFixture


class BatchOperateTests(FeatureFixture):
    def status(self, task_id):
        return self.db.get_task(task_id)['status']

    def make(self, name, status='pending'):
        task_id = self.create('/tiny', filename=name)
        self.db.update_task_status(task_id, status)
        return task_id

    def test_pause_only_handles_eligible_states_and_counts_each_id_once(self):
        running = self.make('running.bin', 'downloading')
        waiting = self.make('waiting.bin', 'pending')
        done = self.make('done.bin', 'completed')

        result = self.manager.batch_operate(
            [running, running, waiting, done, 'missing-id'], 'pause')

        self.assertEqual(result['action'], 'pause')
        self.assertEqual(result['total'], 4)
        self.assertEqual(result['succeeded'], [running, waiting])
        self.assertEqual({item['task_id']: item['reason'] for item in result['skipped']},
                         {done: 'status', 'missing-id': 'missing'})
        self.assertEqual(result['failed'], [])
        self.assertEqual(self.status(running), 'paused')
        self.assertEqual(self.status(waiting), 'paused')
        self.assertEqual(self.status(done), 'completed')

    def test_start_and_retry_respect_state_table(self):
        waiting = self.make('waiting.bin', 'pending')
        paused = self.make('paused.bin', 'paused')
        failed = self.make('failed.bin', 'failed')
        cancelled = self.make('cancelled.bin', 'cancelled')
        verify = self.make('verify.bin', 'verify_failed')
        done = self.make('done.bin', 'completed')

        started = []
        with patch.object(self.engine, 'start_download',
                          side_effect=lambda task_id, resume=False: started.append(task_id) or True):
            start_result = self.manager.batch_operate([waiting, paused, failed, done], 'start')
            retry_result = self.manager.batch_operate([failed, verify, cancelled, paused], 'retry')

        self.assertEqual(start_result['succeeded'], [waiting, paused])
        self.assertEqual([item['task_id'] for item in start_result['skipped']], [failed, done])
        # 并发门禁满后其余项正常入队，仍算成功启动；暂停任务不参与重试
        self.assertEqual(set(retry_result['succeeded']), {failed, cancelled, verify})
        self.assertEqual([item['task_id'] for item in retry_result['skipped']], [paused])
        self.assertEqual(self.manager.db.get_task(verify)['downloaded_size'], 0)

    def test_verify_failed_retry_resets_progress(self):
        task_id = self.make('broken.bin', 'verify_failed')
        self.db.update_task_progress(task_id, 4096, 0)
        with patch.object(self.engine, 'start_download', return_value=True):
            result = self.manager.batch_operate([task_id], 'retry')
        self.assertEqual(result['succeeded'], [task_id])
        self.assertEqual(self.db.get_task(task_id)['downloaded_size'], 0)

    def test_pause_holds_auto_dequeue_until_batch_finishes(self):
        running = self.make('a-running.bin')
        second = self.make('b-second.bin')
        third = self.make('c-third.bin')
        untouched = self.make('d-untouched.bin')
        started = []

        def fake_start(task_id, resume=False):
            started.append(task_id)
            return True

        with patch.object(self.engine, 'start_download', side_effect=fake_start):
            self.assertTrue(self.manager.start_task(running))
            result = self.manager.batch_operate([running, second, third], 'pause')

        self.assertEqual(result['succeeded'], [running, second, third])
        self.assertEqual(started[0], running)
        # 同批待暂停的 B/C 不能在被暂停前短暂启动；未选任务在批量结束后正常补位
        self.assertNotIn(second, started)
        self.assertNotIn(third, started)
        self.assertIn(untouched, started)
        self.assertEqual(started.count(running), 1)

    def test_one_item_failure_does_not_block_the_rest(self):
        first = self.make('first.bin')
        second = self.make('second.bin')
        with patch.object(self.manager, 'pause_task', side_effect=[RuntimeError('boom'), True]):
            result = self.manager.batch_operate([first, second], 'pause')
        self.assertEqual([item['task_id'] for item in result['failed']], [first])
        self.assertEqual(result['succeeded'], [second])
        # 异常后忙碌与暂缓都要释放，下一批仍可执行
        self.assertTrue(self.manager.begin_batch_operation())
        self.manager.end_batch_operation()

    def test_second_batch_is_rejected_while_busy_and_action_is_validated(self):
        self.assertTrue(self.manager.begin_batch_operation())
        try:
            with self.assertRaises(BatchBusyError):
                self.manager.batch_operate([], 'pause')
        finally:
            self.manager.end_batch_operation()
        with self.assertRaises(ValueError):
            self.manager.batch_operate([], 'unknown-action')
        empty = self.manager.batch_operate([], 'pause')
        self.assertEqual(empty, {'action': 'pause', 'total': 0,
                                 'succeeded': [], 'skipped': [], 'failed': []})

    def test_delete_records_keeps_files_and_skips_verifying(self):
        removable = self.make('keep.bin', 'paused')
        target = Path(self.db.get_task(removable)['save_path'])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b'user output')
        verifying = self.make('verifying.bin', 'verifying')

        result = self.manager.batch_operate([removable, verifying], 'delete_records')

        self.assertEqual(result['succeeded'], [removable])
        self.assertEqual([item['task_id'] for item in result['skipped']], [verifying])
        self.assertEqual(self.status(verifying), 'verifying')
        self.assertIsNone(self.db.get_task(removable))
        self.assertTrue(target.is_file())
        self.assertEqual(target.read_bytes(), b'user output')

    def test_order_follows_list_order_not_input_order(self):
        first = self.make('first.bin')
        second = self.make('second.bin')
        third = self.make('third.bin')
        result = self.manager.batch_operate([third, first, second], 'pause')
        self.assertEqual(result['succeeded'], [first, second, third])

    def test_shutdown_stops_submitting_remaining_start_actions(self):
        tasks = [self.make(f'task-{index}.bin') for index in range(3)]
        self.assertTrue(self.manager.begin_batch_operation('start'))
        self.manager._shutdown_started = True
        with patch.object(self.engine, 'start_download', return_value=True) as start:
            result = self.manager.batch_operate(tasks, 'start', reserved=True)
        start.assert_not_called()
        self.assertEqual(result['succeeded'], [])
        self.assertEqual({item['reason'] for item in result['skipped']}, {'shutdown'})
        self.assertEqual(len(result['skipped']), 3)

    def test_pause_reservation_holds_queue_before_worker_starts(self):
        waiting = self.make('waiting-before-worker.bin')
        generation = self.manager.get_activity_state()[0]
        self.assertTrue(self.manager.begin_batch_operation('pause'))
        try:
            self.assertEqual(self.manager.get_activity_state(), (generation + 1, True))
            with patch.object(self.engine, 'start_download', return_value=True) as start:
                self.manager._try_start_next_task()
                start.assert_not_called()
                self.assertFalse(self.manager.start_task(waiting))
                result = self.manager.batch_operate([waiting], 'pause', reserved=True)
                start.assert_not_called()
            self.assertEqual(result['succeeded'], [waiting])
        finally:
            self.manager.end_batch_operation()

    def test_reserved_call_requires_a_reservation_and_cannot_enter_twice(self):
        with self.assertRaises(BatchBusyError):
            self.manager.batch_operate([], 'pause', reserved=True)
        task = self.make('one-worker.bin')
        entered, release = threading.Event(), threading.Event()
        result = []

        def pause(_task_id):
            entered.set()
            self.assertTrue(release.wait(3))
            return True

        with patch.object(self.manager, 'pause_task', side_effect=pause):
            self.assertTrue(self.manager.begin_batch_operation('pause'))
            worker = threading.Thread(target=lambda: result.append(
                self.manager.batch_operate([task], 'pause', reserved=True)))
            worker.start()
            try:
                self.assertTrue(entered.wait(2))
                with self.assertRaises(BatchBusyError):
                    self.manager.batch_operate([task], 'pause', reserved=True)
                self.assertTrue(self.manager.get_activity_state()[1])
            finally:
                release.set()
                worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(result[0]['succeeded'], [task])

    def test_shutdown_rejects_new_batch_and_explicit_start(self):
        task = self.make('no-start-after-exit.bin', 'paused')
        self.manager.shutdown()
        # pause_all 等旧调用不能通过恢复调度标志绕过退出标志。
        self.manager._scheduling_paused = False
        self.assertFalse(self.manager.begin_batch_operation())
        with patch.object(self.engine, 'start_download') as start, \
                patch.object(self.engine, 'resume_download') as resume:
            self.assertFalse(self.manager.start_task(task))
            self.assertFalse(self.manager.resume_task(task))
        start.assert_not_called()
        resume.assert_not_called()

    def test_shutdown_stops_remaining_delete_actions_and_releases_hold(self):
        first = self.make('exit-first.bin', 'paused')
        second = self.make('exit-second.bin', 'paused')

        def delete(task_id):
            self.assertEqual(task_id, first)
            self.manager._shutdown_started = True
            self.manager._scheduling_paused = True
            return self.db.delete_task(task_id)

        with patch.object(self.manager, 'delete_task', side_effect=delete) as remove:
            result = self.manager.batch_operate([first, second], 'delete_records')
        remove.assert_called_once_with(first)
        self.assertIsNotNone(self.db.get_task(second))
        self.assertEqual(result['succeeded'], [first])
        self.assertEqual(result['skipped'], [{'task_id': second, 'reason': 'shutdown'}])
        self.assertEqual(self.manager._batch_hold, 0)
        self.assertFalse(self.manager.get_activity_state()[1])


if __name__ == '__main__':
    unittest.main()
