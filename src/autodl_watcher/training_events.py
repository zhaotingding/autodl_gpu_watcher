"""纯日志事件解析，供默认监控与专用规则共同使用。"""
from __future__ import annotations

import ast
from datetime import datetime
import json
import math
import re


NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_FATAL = re.compile(r"(?:CUDA out of memory|OutOfMemoryError|(?<![\w.])[\w.]*(?:Error|Exception)\s*:|Segmentation fault|CUDA error:|\bSIG(?:KILL|SEGV|ABRT)\b|^\s*Killed\s*$|\[(?:ERROR|FATAL|CRITICAL)\]|Traceback \(most recent call last\))", re.I | re.M)
TIMESTAMP = re.compile(r"(\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(?:\.\d+)?(?:Z|[+-]\d\d:?\d\d)?)")
ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")  # 默认解析、专用规则和预览共用同一终端颜色边界。


def dictionary(text, *, malformed=None):  # 专用规则拒绝坏字典；通用日志可用空字典继续提取键值文本。
    try:
        value = json.loads(text)
    except ValueError:
        try:
            value = ast.literal_eval(text)
        except (ValueError, SyntaxError):
            return malformed
    return value if isinstance(value, dict) else None


def structured_event(line):  # 把带前缀JSON日志转成已有事件协议；普通批次追踪JSON不当成训练证据。
    match = re.search(r"(?:^|\s)(TRAIN|TEST|EVAL|EVALUATED|TRAINING_STARTED|TRAINING_COMPLETE|SMOKE_PASS|TRAINING_FAILED|ERROR)\s+(?:(\S+)\s+)?(\{.*\})\s*$", line)
    try:
        event = json.loads(match[3] if match else line)
    except ValueError:
        return {}
    if not isinstance(event, dict):
        return {}
    if match:
        kind = match[1]
        if kind == "EVALUATED":
            return {"event": "test", "datasets": {f"{match[2] or 'test'}/{name}": values for name, values in event.items() if isinstance(values, dict)}}
        event["event"] = {"TRAIN": "train", "TEST": "test", "EVAL": "test", "TRAINING_STARTED": "train",
                          "TRAINING_COMPLETE": "completed", "SMOKE_PASS": "completed", "TRAINING_FAILED": "error", "ERROR": "error"}[kind]
        event["smoke"] = kind == "SMOKE_PASS"
        if "steps" in event and "step" not in event:
            event["step"] = event["steps"]
        if "steps_total" in event:
            event["total_steps"] = event["steps_total"]
        if "steps_per_arm" in event:
            event["step"] = event["steps_per_arm"]
            raw = event.get("epoch")
            if isinstance(raw, (int, float)) and not isinstance(raw, bool) and math.isfinite(raw) and raw >= 0:
                event.update(raw_epoch=raw, epoch=raw + 1)  # 此协议Epoch从0开始；没有提供总轮数时保持未知。
        if event.get("status") in {"failed", "error", "aborted"}:
            event["event"] = "error"
    if "loss" in event:
        event["metrics"] = {**(event.get("metrics") if isinstance(event.get("metrics"), dict) else {}), "loss": event["loss"]}
    return event


def flat_metrics(values):  # 保留任意指标和嵌套分支名。
    result = {}
    for name, value in (values.items() if isinstance(values, dict) else ()):
        if isinstance(value, dict):
            result.update({f"{name}/{child}": number for child, number in flat_metrics(value).items()})
        elif isinstance(value, (int, float)) and not isinstance(value, bool):
            result[str(name)] = value
    return result


METADATA = {"epoch", "total_epochs", "raw_epoch", "step", "steps", "global_step", "iteration", "iter", "total_steps", "steps_total", "steps_per_arm",
             "pid", "time", "timestamp", "elapsed_seconds", "wall_time", "frames_done", "frames_total", "seed", "batch_size", "workers",
             "peak_gpu_allocated", "elapsed", "max_epochs"}


def generic_event(line, event):
    if event.get("event") in {"train", "test", "progress", "completed", "error"}:
        if event["event"] in {"train", "test", "progress"} and "datasets" not in event:
            metrics = {name: value for name, value in flat_metrics(event).items()
                       if name not in METADATA and not name.startswith("metrics/")}
            event["metrics"] = {**metrics, **flat_metrics(event.get("metrics", {}))}
            if "global_step" in event and "step" not in event:
                event["step"] = event["global_step"]
        return event
    body = line[line.find("{"):] if "{" in line else ""
    values = event
    if not values and body:
        values = dictionary(body, malformed={})  # 花括号注释或不完整字典仍允许后续普通键值解析。
    if not isinstance(values, dict) or any(key in values for key in ("indexes", "image_sha256", "rng_sha256")):
        return event
    structured = bool(values)
    if not structured:
        values = {name: float(number) for name, number in re.findall(
            r"(?<![\w/])([A-Za-z_][\w./@%-]*)\s*[:=]\s*(" + NUMBER + r"|[-+]?(?:nan|inf))(?=$|[\s,;|\]])", line, re.I)}
    phase_text = str(values.get("phase", values.get("event", values.get("status", "")))) if structured else line
    phase = ("test" if re.search(r"\b(?:test(?:ing)?|eval(?:uation|uating)?|valid(?:ation|ating)?|val)\b", phase_text, re.I)
             else "train" if re.search(r"\btrain(?:ing)?\b", phase_text, re.I) else "unknown")
    epoch = re.search(r"\bEpoch\s*[:= ]\s*(\d+)(?:\s*/\s*(\d+))?", line, re.I) if not structured else None
    if epoch:
        values["epoch"] = int(epoch[1])
        if epoch[2]:
            values["total_epochs"] = int(epoch[2])
        if phase == "unknown":
            phase = "train"
    progress = {key: values[key] for key in ("epoch", "total_epochs", "pid", "total_steps") if key in values}
    for name in ("step", "steps", "global_step", "iteration", "iter"):
        if name in values:
            progress["step"] = values[name]
            break
    metrics = flat_metrics(values.get("metrics", {}))
    metrics.update({name: value for name, value in flat_metrics(values).items()
                    if name not in METADATA and not name.startswith("metrics/")})
    if not metrics or not (progress or phase != "unknown" or "metrics" in values or "loss" in values or "eval_loss" in values):
        return event
    if phase == "unknown" and "loss" in values:
        phase = "train"
    if phase == "unknown" and any(name.startswith(("eval_", "val_")) for name in metrics):
        phase = "test"
    return {**progress, "event": "progress", "phase": phase, "metrics": metrics,
            "dataset": str(values.get("dataset", "validation")), "generic": True}


def parse_progress(text, utc_offset="+0000", expected_pid=None):  # DeepfakeBench适配器及通用JSONL事件协议；指标总是携带各自epoch/step。
    result = {"phase": "unknown", "epoch": None, "total_epochs": None, "raw_epoch": None, "step": None,
              "train": {}, "tests": {}, "observations": {}, "error": "", "completed": False, "progress_at": None, "progress_line": None}
    deepfake = bool(re.search(r"Epoch\[\d+\]|training-metric,|nEpochs:", text))
    last_epoch = re.search(r"\bnEpochs:\s*(\d+)", text)
    start_epoch = re.search(r"\bstart_epoch:\s*(\d+)", text)
    first = int(start_epoch[1]) if start_epoch else 0
    if deepfake and last_epoch:
        result["total_epochs"] = int(last_epoch[1]) - first + 1  # 已核验此项目range(start_epoch,nEpochs+1)，包含末轮。
    stamp = None
    event_pid = None
    for index, line in enumerate(text.replace("\r", "\n").splitlines()):
        match = TIMESTAMP.search(line)
        if match:
            try:
                parsed_time = datetime.fromisoformat(match[1].replace("Z", "+00:00"))
                stamp = (parsed_time if parsed_time.tzinfo else parsed_time.replace(tzinfo=datetime.strptime(utc_offset, "%z").tzinfo)).timestamp()
            except ValueError:
                stamp = None
        advanced = False
        line = ANSI_ESCAPE.sub("", line)  # 只去除终端控制序列，保留原行号和日志顺序。
        event = generic_event(line, structured_event(line))
        if "pid" in event:
            event_pid = event["pid"]
        evaluation = re.search(r"\bEPOCH_DONE (\S+) (\d+) (\d+) source_best \d+ val (\{.*\})\s*$", line)
        if evaluation and (expected_pid is None or event_pid == expected_pid):
            try:
                values = ast.literal_eval(evaluation[4])
            except (ValueError, SyntaxError):
                values = {}
            event = {"event": "test", "epoch": int(evaluation[2]), "step": int(evaluation[3]),
                     "dataset": evaluation[1] + "/validation", "metrics": values}
        if expected_pid is not None and event.get("pid", expected_pid) != expected_pid:
            continue  # 合并日志中的其他PID不能提供本任务进度或显存。
        if event.get("event") in {"train", "test", "progress", "completed", "error"}:
            phase = event.get("phase", event["event"])
            if phase in {"train", "test"}:
                result["phase"] = phase
            elif event.get("generic"):
                result["phase"] = "unknown"
            for key in ("epoch", "total_epochs", "raw_epoch", "step", "total_steps"):
                value = event.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0:
                    result[key] = value
            groups = event.get("datasets") if isinstance(event.get("datasets"), dict) else {str(event.get("dataset", "test")): event.get("metrics", {})}
            invalid = False
            for dataset, values in groups.items():
                metrics = flat_metrics(values)
                invalid |= any(not math.isfinite(value) for value in metrics.values())
                metrics = {key: value for key, value in metrics.items() if math.isfinite(value)}
                if metrics:
                    summary = {"metrics": metrics, "epoch": result["epoch"], "step": result["step"], "time": stamp}
                    if phase == "test":
                        result["tests"][str(dataset)] = summary
                    elif phase == "train":
                        result["train"] = summary
                    else:
                        result["observations"] = summary
            result["completed"] = event["event"] == "completed"
            result["smoke"] = event.get("smoke", False)
            if event["event"] == "error" or invalid:
                result.update(error="训练指标出现NaN/Inf" if invalid else str(event.get("message", "训练错误"))[:500], completed=False)
            peak = event.get("peak_gpu_allocated")
            if expected_pid is not None and event.get("pid") == expected_pid and isinstance(peak, (int, float)) and not isinstance(peak, bool) and math.isfinite(peak) and peak >= 0:
                result["peak_memory_bytes"] = peak  # 仅接受本进程明确上报的PyTorch峰值，不替代NVML实时用量。
            advanced = True
        boundary = re.search(r"\bEPOCH_END\s+(\d+)\s+steps_per_arm\s+(\d+)\b", line)
        if boundary:
            result.update(raw_epoch=int(boundary[1]), epoch=int(boundary[1]) + 1, step=int(boundary[2]), phase="between")
            advanced = True
        epoch = re.search(r"Epoch\[(\d+)\]\s+start", line)
        generic = re.search(r"\bEpoch\s*[:= ]\s*(\d+)\s*/\s*(\d+)", line, re.I)
        if epoch:
            result.update(raw_epoch=int(epoch[1]), epoch=int(epoch[1]) - first + 1, phase="train", error="", completed=False)
            advanced = True
        elif generic:
            result.update(epoch=int(generic[1]), total_epochs=int(generic[2]), phase="test" if event.get("phase") == "test" else "train", error="", completed=False)
            advanced = True
        if "===> Test start!" in line:
            result["phase"] = "test"
            advanced = True
        if "===> Test Done!" in line:
            result["phase"] = "train"
            advanced = True
        step = re.search(r"(?:Iter:|\bstep:)\s*(\d+)", line)
        if step:
            result["step"] = int(step[1])
        dataset = re.search(r"dataset:\s*(\S+)", line)
        metrics = {name: float(value) for name, value in re.findall(r"(?:training|testing)-(?:metric|loss),\s*([\w]+):\s*(" + NUMBER + ")", line)}
        metrics = {"loss" if name == "overall" else name: value for name, value in metrics.items() if math.isfinite(value)}
        if metrics:
            phase = "test" if "testing-" in line else "train"
            old = result["tests"].get(dataset[1], {}) if phase == "test" and dataset else result["train"]
            if old.get("epoch") != result["epoch"] or old.get("step") != result["step"]:
                old = {}
            summary = {"epoch": result["epoch"], "step": result["step"], "time": stamp, "metrics": {**old.get("metrics", {}), **metrics}}
            if phase == "test" and dataset:
                result["tests"][dataset[1]] = summary
            elif phase == "train":
                result["train"] = summary
            result.update(phase=phase, completed=False)
            advanced = True
        if "Stop Training on best Testing metric" in line or "Experiment summary saved to" in line:
            result.update(completed=True, error="")
            advanced = True
        if _FATAL.search(line) or re.search(r"(?:training|testing)-(?:loss|metric),\s*\w+:\s*[-+]?(?:nan|inf)\b", line, re.I):
            result.update(error=line.strip()[-500:], completed=False)
            advanced = True
        if advanced:
            result["progress_line"] = index
            if stamp is not None:
                result["progress_at"] = stamp
    return result


def log_text(log):
    if log.get("gap"):
        config = "\n".join(line for line in log["head"].splitlines() if re.match(r"\s*(?:nEpochs|start_epoch):", line))
        return config + "\n" + log["tail"]  # 日志截断后不能借用文件头旧轮次解释当前指标。
    return log["head"] + log["tail"]
