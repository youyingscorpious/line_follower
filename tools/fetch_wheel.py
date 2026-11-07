"""断点续传下载器：网络很不稳定，用 Range 分块 + 无限重试拉取 wheel。"""

import http.client
import os
import ssl
import sys
import time

CHUNK = 512 * 1024
ctx = ssl.create_default_context()
UA = {"User-Agent": "pip/24.0", "Accept-Encoding": "identity"}


def fetch_range(host, path, start, end):
    c = http.client.HTTPSConnection(host, 443, timeout=25, context=ctx)
    c.request("GET", path, headers={**UA, "Range": f"bytes={start}-{end}"})
    r = c.getresponse()
    if r.status not in (200, 206):
        raise RuntimeError(f"status {r.status}")
    data = bytearray()
    while len(data) < (end - start + 1):
        b = r.read(65536)
        if not b:
            break
        data.extend(b)
    c.close()
    return bytes(data)


def download(url, dest, log=print):
    host = url.split("/")[2]
    path = "/" + url.split("/", 3)[3].split("#")[0]
    part = dest + ".part"
    total = None
    attempt_fail = 0
    while True:
        done = os.path.getsize(part) if os.path.exists(part) else 0
        if total is not None and done >= total:
            break
        start = done
        end = start + CHUNK - 1
        if total is not None:
            end = min(end, total - 1)
        try:
            chunk = fetch_range(host, path, start, end)
            if not chunk:
                raise RuntimeError("empty chunk")
            with open(part, "ab") as f:
                f.write(chunk)
                f.flush()
                os.fsync(f.fileno())
            attempt_fail = 0
            log(f"{os.path.basename(dest)}: {start + len(chunk)} bytes")
        except Exception as e:
            attempt_fail += 1
            if total is None and attempt_fail == 1:
                # 探一下总大小
                try:
                    c = http.client.HTTPSConnection(host, 443, timeout=20, context=ctx)
                    c.request("GET", path, headers={**UA, "Range": "bytes=0-0"})
                    r = c.getresponse()
                    cr = r.getheader("Content-Range") or ""
                    r.read()
                    c.close()
                    if "/" in cr:
                        total = int(cr.split("/")[1])
                        log(f"total size = {total}")
                except Exception:
                    total = None
            if attempt_fail > 400:
                raise SystemExit(f"下载失败: {e}")
            time.sleep(min(3.0, 0.2 * attempt_fail))
            continue
        if total is None:
            # 没拿到总大小：靠一次空块判断结束
            try:
                if len(chunk) < 1:
                    break
            except Exception:
                pass
    os.replace(part, dest)
    log(f"done: {dest} ({os.path.getsize(dest)} bytes)")


if __name__ == "__main__":
    url, dest = sys.argv[1], sys.argv[2]
    download(url, dest, log=lambda m: print(m, flush=True))
