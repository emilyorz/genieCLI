#!/usr/bin/env python3
"""
test_vision.py - 測試送截圖給 TGenie 是否支援 vision
"""
import sys, base64, uuid, time
sys.path.insert(0, '.')
import config, requests, urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

cfg = config.load()

# 建立一個簡單的 1x1 紅色 PNG (最小測試圖)
# 或用真實截圖
import os
TEST_IMAGE = "screenshot.png"

if not os.path.exists(TEST_IMAGE):
    # 建立最小測試 PNG
    import struct, zlib
    def make_png():
        def chunk(name, data):
            c = name + data
            return struct.pack('>I', len(data)) + c + struct.pack('>I', zlib.crc32(c) & 0xffffffff)
        sig = b'\x89PNG\r\n\x1a\n'
        ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
        idat = chunk(b'IDAT', zlib.compress(b'\x00\xff\x00\x00'))
        iend = chunk(b'IEND', b'')
        return sig + ihdr + idat + iend
    with open(TEST_IMAGE, 'wb') as f:
        f.write(make_png())
    print(f"Created test image: {TEST_IMAGE}")
else:
    print(f"Using existing: {TEST_IMAGE}")

with open(TEST_IMAGE, 'rb') as f:
    img_bytes = f.read()

print(f"Image size: {len(img_bytes)} bytes")

# Build multipart
messages = [
    {
        "id": str(uuid.uuid4()),
        "role": "user",
        "content": [{"type": "text", "text": "What do you see in this image? Describe it briefly.", "reasonText": None}],
        "form": "text",
        "timestamp": int(time.time() * 1000),
        "tokenCount": 10,
    }
]

import json
messages_json = json.dumps(messages, ensure_ascii=False)
boundary = "----WebKitFormBoundary" + uuid.uuid4().hex[:16]
CRLF = "\r\n"

parts = (
    f"--{boundary}{CRLF}"
    f'Content-Disposition: form-data; name="modelName"{CRLF}{CRLF}'
    f"{cfg['defaultModel']}{CRLF}"
    f"--{boundary}{CRLF}"
    f'Content-Disposition: form-data; name="messages"{CRLF}{CRLF}'
    f"{messages_json}{CRLF}"
    f"--{boundary}{CRLF}"
    f'Content-Disposition: form-data; name="reasoningEffort"{CRLF}{CRLF}'
    f"disable{CRLF}"
).encode("utf-8")

file_header = (
    f"--{boundary}{CRLF}"
    f'Content-Disposition: form-data; name="files"; filename="{TEST_IMAGE}"{CRLF}'
    f'Content-Type: image/png{CRLF}{CRLF}'
).encode("utf-8")

body = parts + file_header + img_bytes + CRLF.encode("utf-8") + f"--{boundary}--{CRLF}".encode("utf-8")

print(f"\n=== REQUEST ===")
print(f"URL     : {cfg['endpoint']}/api/main/oaicompatible")
print(f"Boundary: {boundary}")
print(f"Body preview (text parts):\n{parts.decode('utf-8')}")
print(f"File header: {file_header.decode('utf-8')}")

authority = cfg["endpoint"].replace("https://", "")
headers = {
    "authority":          authority,
    "accept":             "*/*",
    "accept-encoding":    "gzip, deflate, br",
    "authorization":      f"Bearer {cfg['authToken']}",
    "origin":             cfg["frontendUrl"],
    "referer":            cfg["frontendUrl"] + "/",
    "sec-fetch-dest":     "empty",
    "sec-fetch-mode":     "cors",
    "sec-fetch-site":     "same-site",
    "content-type":       f"multipart/form-data; boundary={boundary}",
    "user-agent":         "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

session = requests.Session()
for ck in cfg.get("cookies", []):
    session.cookies.set(ck["name"], ck["value"], domain=ck["domain"])

try:
    resp = session.post(
        f"{cfg['endpoint']}/api/main/oaicompatible",
        headers=headers,
        data=body,
        timeout=60,
        verify=False,
    )
    print(f"\n=== RESPONSE ===")
    print(f"Status: {resp.status_code}")
    print(f"Body (first 500): {resp.text[:500]}")
except Exception as e:
    print(f"ERROR: {e}")
