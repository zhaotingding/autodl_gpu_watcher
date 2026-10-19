"""离线训练服务测试只消费本地事件，不触发 tick/远端采样。"""
import queue
import time


def wait_training(service, timeout=3):
    deadline = time.monotonic() + timeout
    while True:
        try:
            kind, value = service.events.get_nowait()
        except queue.Empty:
            if not service.busy():
                return
            if time.monotonic() >= deadline:
                raise AssertionError("训练解析或连接收尾未在测试时限内完成")
            time.sleep(.001)
        else:
            if kind != "training":
                raise AssertionError(f"未预期的测试事件：{kind}")
            service.receive(value)
