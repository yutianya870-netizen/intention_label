#!/usr/bin/env python3
"""Local intent annotation API. Python 3.9+, standard library only."""
import argparse
import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LABELS = {"U", "D", "E", "R", "OTHER", "UNCLEAR"}
ALIASES = {"understand": "U", "understanding": "U", "decide": "D", "deciding": "D", "execute": "E", "executing": "E", "realign": "R", "realigning": "R"}


def text_content(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "\n".join(filter(None, (text_content(v) for v in value)))
    if isinstance(value, dict):
        if value.get("type") in {"thinking", "reasoning", "tool_use", "tool_result", "image", "input_image"}:
            return ""
        for key in ("text", "content", "value"):
            if key in value:
                return text_content(value[key])
    return ""


def import_dataset(raw, filename):
    """Read files as data, never execute JS. Preserve file order, exclude private reasoning."""
    raw = raw.lstrip("\ufeff").strip()
    report = {"filename": filename, "invalid_lines": 0, "skipped_records": 0, "duplicate_records": 0, "sessions": 0, "user_prompts": 0}
    if re.match(r"(?:window\.)?CLEANED_SESSIONS\s*=", raw):
        raw = re.sub(r"^(?:window\.)?CLEANED_SESSIONS\s*=\s*", "", raw).rstrip("; \n\r\t")
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict) and isinstance(obj.get("sessions"), list):
            records = obj["sessions"]
        elif isinstance(obj, list):
            records = obj
        else:
            records = [obj]
    except json.JSONDecodeError:
        records = []
        for line in raw.splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                report["invalid_lines"] += 1
    groups = {}
    seen = set()
    codex_roles = {r.get("payload", {}).get("role") for r in records if isinstance(r, dict) and r.get("type") == "response_item" and isinstance(r.get("payload"), dict) and r["payload"].get("type") == "message"}
    fallback_sid = next((r.get("payload", {}).get("id") for r in records if isinstance(r, dict) and r.get("type") == "session_meta"), None) or filename

    def add(row, sid, source, index):
        if not isinstance(row, dict) or row.get("isSidechain") or row.get("is_conversational") is False:
            report["skipped_records"] += 1
            return
        message = row.get("message") if isinstance(row.get("message"), dict) else row
        if row.get("type") == "response_item":
            message = row.get("payload", {})
            if message.get("type") != "message":
                report["skipped_records"] += 1
                return
        elif row.get("type") == "event_msg":
            payload = row.get("payload", {})
            kind = payload.get("type")
            if kind not in {"user_message", "agent_message"}:
                report["skipped_records"] += 1
                return
            message = {"role": "user" if kind == "user_message" else "assistant", "content": payload.get("message", "")}
            if message["role"] in codex_roles:
                report["skipped_records"] += 1
                return
        role = str(message.get("role") or row.get("role") or (row.get("type") if row.get("type") in {"user", "assistant"} else "") or ("user" if row.get("turn_type") == "user_prompt" else "")).lower()
        content = text_content(message.get("content", message.get("text", message.get("prompt", ""))))
        if role not in {"user", "assistant"} or not content.strip():
            report["skipped_records"] += 1
            return
        if role == "user" and (re.fullmatch(r"\s*<(environment_context|turn_aborted|system-reminder)>[\s\S]*?</\1>\s*", content) or content.startswith("Base directory for this skill:")):
            report["skipped_records"] += 1
            return
        ident = str(row.get("uuid") or row.get("turn_id") or f"{sid}#{index}")
        duplicate = (sid, ident, role, content)
        if duplicate in seen:
            report["duplicate_records"] += 1
            return
        seen.add(duplicate)
        key = (sid, source)
        groups.setdefault(key, []).append({"turn_id": ident, "role": role, "content": content, "source_index": index, "turn_number": row.get("turn_number", index)})

    for index, row in enumerate(records):
        if not isinstance(row, dict):
            report["skipped_records"] += 1
            continue
        turns = row.get("turns", row.get("messages", row.get("conversation")))
        if isinstance(turns, list):
            sid = str(row.get("session_id") or row.get("conversation_id") or row.get("id") or f"{filename}:{index}")
            source = str(row.get("source_file") or filename)
            for j, turn in enumerate(turns):
                add(turn, sid, source, j)
        else:
            sid = str(row.get("session_id") or row.get("sessionId") or row.get("conversation_id") or fallback_sid)
            add(row, sid, filename, index)
    sessions = []
    for (sid, source), turns in groups.items():
        count = sum(t["role"] == "user" for t in turns)
        if not count:
            continue
        uid = hashlib.sha256(json.dumps([sid, [(t["role"], t["content"]) for t in turns]], ensure_ascii=False).encode()).hexdigest()[:24]
        sessions.append({"uid": uid, "session_id": sid, "source_file": source, "turns": turns, "user_prompt_count": count})
        report["user_prompts"] += count
    report["sessions"] = len(sessions)
    if not sessions:
        raise ValueError("未识别到用户消息。请导入包含 turns/messages 或 role/content 的 JSON、JSONL、CLEANED_SESSIONS JS。index.json 只有索引，不能代替会话文件。")
    return {"sessions": sessions, "report": report}


def context_for(session, index):
    turns = session.get("turns", [])
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(turns) or turns[index].get("role") != "user":
        raise ValueError("目标必须是现有用户消息")
    # Critical leakage barrier: slice BEFORE reading/serializing anything downstream.
    prefix = turns[:index + 1]
    context = [{"turn_id": t.get("turn_id", str(i)), "role": t["role"], "content": t["content"]} for i, t in enumerate(prefix) if t.get("role") in {"user", "assistant"}]
    return {"conversation_id": session["session_id"], "target_turn_id": turns[index]["turn_id"], "prior_context": context[:-1], "target_user_turn": context[-1]}


def build_request(data):
    c = data["config"]
    context = context_for(data["session"], data["target_index"])
    system = str(data["system_prompt"])
    if not system.strip():
        raise ValueError("提示词不能为空")
    user = json.dumps(context, ensure_ascii=False)
    protocol = c.get("protocol", "chat")
    base = str(c.get("base_url", "")).strip().rstrip("/")
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValueError("Base URL 必须是无密码、无查询参数的 HTTP(S) 地址")
    model = str(c.get("model", "")).strip()
    if not model:
        raise ValueError("请填写模型 ID")
    limit = int(c.get("max_tokens", 4096))
    if not 256 <= limit <= 131072:
        raise ValueError("输出 token 上限须为 256–131072")
    headers = {"Content-Type": "application/json"}
    key = str(c.get("api_key", ""))
    if protocol == "chat":
        url = base + "/chat/completions"
        headers["Authorization"] = "Bearer " + key
        body = {"model": model, "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}], "max_tokens": limit, "stream": False}
    elif protocol == "responses":
        url = base + "/responses"
        headers["Authorization"] = "Bearer " + key
        body = {"model": model, "instructions": system, "input": [{"role": "user", "content": user}], "max_output_tokens": limit, "store": False}
    elif protocol == "anthropic":
        url = base + "/messages"
        headers.update({"x-api-key": key, "anthropic-version": "2023-06-01"})
        body = {"model": model, "system": system, "messages": [{"role": "user", "content": user}], "max_tokens": limit}
    elif protocol == "gemini":
        url = base + "/models/" + urllib.parse.quote(model.removeprefix("models/"), safe="") + ":generateContent"
        headers["x-goog-api-key"] = key
        body = {"systemInstruction": {"parts": [{"text": system}]}, "contents": [{"role": "user", "parts": [{"text": user}]}], "generationConfig": {"maxOutputTokens": limit, "responseMimeType": "application/json"}}
    else:
        raise ValueError("不支持的 API 协议")
    extra = c.get("extra", {})
    if not isinstance(extra, dict):
        raise ValueError("额外参数必须是 JSON 对象")
    # Allow sampling/reasoning options, but never alternate messages/tools/state that leak data.
    allowed = {"temperature", "top_p", "seed", "reasoning_effort", "reasoning", "thinking", "enable_thinking", "max_completion_tokens", "generationConfig", "response_format"}
    unknown = set(extra) - allowed
    if unknown:
        raise ValueError("不允许的额外参数：" + ", ".join(sorted(unknown)))
    if "max_completion_tokens" in extra:
        body.pop("max_tokens", None)
    if "generationConfig" in extra and protocol == "gemini":
        body["generationConfig"].update(extra["generationConfig"])
        extra = {k: v for k, v in extra.items() if k != "generationConfig"}
    body.update(extra)
    payload_hash = hashlib.sha256(json.dumps(body, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    return url, headers, body, {"context": context, "history_messages": len(context["prior_context"]), "target_index": data["target_index"], "future_excluded": True, "payload_sha256": payload_hash}


def parse_prediction(raw):
    clean = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip())
    try:
        obj = json.loads(clean)
    except json.JSONDecodeError:
        start, end = clean.find("{"), clean.rfind("}")
        obj = json.loads(clean[start:end + 1]) if start >= 0 and end > start else None
    if not isinstance(obj, dict):
        raise ValueError("模型未返回 JSON 对象")
    primary = obj.get("primary", obj.get("label", ""))
    primary = ALIASES.get(str(primary).lower(), str(primary).upper())
    if primary not in LABELS:
        raise ValueError("模型主标签不合法：" + str(primary))
    obj["primary"] = primary
    for field in ("secondary", "queued"):
        values = obj.get(field, [])
        if not isinstance(values, list) or any(v not in LABELS for v in values):
            raise ValueError(field + " 必须为合法标签数组")
        obj[field] = values
    if not isinstance(obj.get("needs_review", False), bool):
        raise ValueError("needs_review 必须是布尔值")
    obj.setdefault("needs_review", primary in {"OTHER", "UNCLEAR"})
    return obj


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("API 返回重定向，请填写最终 Base URL；密钥不会转发到重定向地址")


def classify(data):
    url, headers, body, audit = build_request(data)
    started = time.monotonic()
    request = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode(), headers=headers, method="POST")
    try:
        with urllib.request.build_opener(NoRedirect()).open(request, timeout=min(300, max(5, int(data["config"].get("timeout", 120))))) as response:
            answer = json.loads(response.read(16 * 1024 * 1024))
    except urllib.error.HTTPError as exc:
        details = exc.read(8192).decode("utf-8", "replace")
        key = str(data["config"].get("api_key", ""))
        if key:
            details = details.replace(key, "[REDACTED]")
        raise ValueError(f"API HTTP {exc.code}: {details[:1200]}") from None
    protocol = data["config"].get("protocol", "chat")
    if protocol == "chat":
        raw = text_content(answer.get("choices", [{}])[0].get("message", {}).get("content", ""))
    elif protocol == "responses":
        raw = "\n".join(text_content(i.get("content", [])) for i in answer.get("output", []) if i.get("type") == "message")
    elif protocol == "anthropic":
        raw = text_content(answer.get("content", []))
    else:
        raw = text_content([p for p in answer.get("candidates", [{}])[0].get("content", {}).get("parts", []) if not p.get("thought")])
    result = {"raw_output": raw, "audit": audit, "usage": answer.get("usage", answer.get("usageMetadata", {})), "latency_ms": round((time.monotonic() - started) * 1000), "response_model": answer.get("model", answer.get("modelVersion", "")), "response_id": answer.get("id", "")}
    try:
        result["prediction"] = parse_prediction(raw)
        # Identifiers come from the dataset, never trust a model-generated ID.
        result["prediction"].update({"conversation_id": audit["context"]["conversation_id"], "turn_id": audit["context"]["target_turn_id"]})
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        result["error"] = "输出解析失败：" + str(exc)
    return result


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / "static"), **kwargs)

    def log_message(self, fmt, *args):
        pass  # No dataset, API body or credential logs.

    def json_response(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        try:
            host = self.headers.get("Host", "")
            allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
            origin = self.headers.get("Origin")
            if host not in allowed or (origin and origin not in {"http://" + h for h in allowed}):
                return self.json_response({"error": "仅接受本机同源请求"}, 403)
            if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
                return self.json_response({"error": "需要 application/json"}, 415)
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 256 * 1024 * 1024:
                return self.json_response({"error": "请求为空或超过 256 MB"}, 413)
            data = json.loads(self.rfile.read(size))
            if self.path == "/api/import":
                result = import_dataset(data["text"], data.get("filename", "upload"))
            elif self.path == "/api/preview":
                url, _, body, audit = build_request(data)
                result = {"url": url, "body": body, "audit": audit}
            elif self.path == "/api/classify":
                result = classify(data)
            else:
                return self.json_response({"error": "不存在的接口"}, 404)
            self.json_response(result)
        except (ValueError, KeyError, IndexError, TypeError, AttributeError, urllib.error.URLError, TimeoutError, OSError) as exc:
            message = str(exc)
            key = str(locals().get("data", {}).get("config", {}).get("api_key", ""))
            if key:
                message = message.replace(key, "[REDACTED]")
            self.json_response({"error": message}, 400)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8789)
    args = parser.parse_args()
    service = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"意图标注工作台：http://127.0.0.1:{args.port}\n按 Ctrl+C 退出。", flush=True)
    try:
        service.serve_forever()
    except KeyboardInterrupt:
        service.server_close()
