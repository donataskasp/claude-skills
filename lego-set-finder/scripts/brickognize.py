"""Brickognize client: one piece per image, polite, cached by image hash.

Brickognize (https://brickognize.com) is a free image recognition service for LEGO parts, sets
and minifigures. No key is needed. Its predict endpoints are marked "legacy" in its OpenAPI spec
(https://api.brickognize.com/openapi.json) but answered normally when this skill was written.
"""
import concurrent.futures
import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
import uuid

API = "https://api.brickognize.com"
USER_AGENT = "lego-set-finder/1.0"
PARAMS = "predict_color=true&top_k_items=5&top_k_colors=5"


class CallLimit(Exception):
    pass


def _multipart(field, filename, data, content_type="image/jpeg"):
    boundary = "----lsf" + uuid.uuid4().hex
    head = ('--%s\r\nContent-Disposition: form-data; name="%s"; filename="%s"\r\n'
            'Content-Type: %s\r\n\r\n' % (boundary, field, filename, content_type)).encode()
    body = head + data + ("\r\n--%s--\r\n" % boundary).encode()
    return body, "multipart/form-data; boundary=" + boundary


class Client:
    def __init__(self, cache_dir, max_calls=300, workers=3, endpoint="predict/", log=print):
        self.cache_dir = cache_dir
        self.max_calls = max_calls
        self.workers = max(1, min(4, workers))
        self.endpoint = endpoint
        self.calls = 0
        self.cached = 0
        self.log = log
        self._lock = threading.Lock()
        self._last = 0.0
        os.makedirs(cache_dir, exist_ok=True)

    def _cache_path(self, data):
        key = hashlib.sha256(data + self.endpoint.encode() + PARAMS.encode()).hexdigest()
        return os.path.join(self.cache_dir, key[:2], key + ".json")

    def predict(self, path):
        with open(path, "rb") as f:
            data = f.read()
        cp = self._cache_path(data)
        if os.path.exists(cp):
            with open(cp, encoding="utf-8") as f:
                with self._lock:
                    self.cached += 1
                return json.load(f)
        with self._lock:
            if self.calls >= self.max_calls:
                raise CallLimit()
            self.calls += 1
            wait = self._last + 0.35 - time.time()  # at most about 3 requests a second in total
            self._last = max(time.time(), self._last + 0.35)
        if wait > 0:
            time.sleep(wait)
        # A fixed file name: crop names carry the user's photo file names (dates, phone models).
        body, ctype = _multipart("query_image", "piece.jpg", data)
        url = "%s/%s?%s" % (API, self.endpoint, PARAMS)
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "User-Agent": USER_AGENT, "Content-Type": ctype, "Accept": "application/json"})
        last_err = None
        for attempt in range(4):
            try:
                with urllib.request.urlopen(req, timeout=90) as r:
                    result = json.loads(r.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as e:
                last_err = "HTTP %s" % e.code
                if e.code in (429, 500, 502, 503, 504):
                    time.sleep(3 * (attempt + 1) ** 2)
                    continue
                if e.code == 400:  # no piece found in the image, or a bad image
                    result = {"items": [], "error": last_err}
                    break
                raise
            except (urllib.error.URLError, OSError, ValueError) as e:
                last_err = str(e)
                time.sleep(3 * (attempt + 1) ** 2)
        else:
            return {"items": [], "error": last_err or "no answer"}
        os.makedirs(os.path.dirname(cp), exist_ok=True)
        with open(cp, "w", encoding="utf-8") as f:
            json.dump(result, f)
        return result

    def predict_many(self, paths, done=None):
        """Predict every path. Returns {path: response}; stops cleanly at the call limit."""
        out = dict(done or {})
        todo = [p for p in paths if p not in out]
        stopped = False
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.workers) as ex:
            futs = {ex.submit(self.predict, p): p for p in todo}
            for i, fut in enumerate(concurrent.futures.as_completed(futs), 1):
                p = futs[fut]
                try:
                    out[p] = fut.result()
                except CallLimit:
                    stopped = True
                    continue
                if i % 10 == 0 or i == len(todo):
                    self.log("  %d/%d done (%d new calls, %d from cache)" % (i, len(todo), self.calls, self.cached))
        return out, stopped
