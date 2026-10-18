"""Run tests without allowing accidental paid model calls or external downloads.

Usage (from the repository root):
    python backend/tests/run_offline.py [test_research_workflow test_qa_scope ...]
MockTransport, ASGITransport and explicitly mocked client methods remain usable.
"""

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

os.environ.update({"LANGCHAIN_TRACING_V2": "false", "DASHSCOPE_API_KEY": "",
                   "SILICONFLOW_API_KEY": "", "SEMANTIC_SCHOLAR_API_KEY": ""})
test_dir = Path(__file__).resolve().parent
sys.path[:0] = [str(test_dir.parent), str(test_dir)]
original_send = httpx.AsyncClient.send


async def offline_send(client, request, **kwargs):
    if not isinstance(client._transport, (httpx.MockTransport, httpx.ASGITransport)):
        raise AssertionError(f"Offline test attempted external HTTP: {request.method} {request.url.host}")
    return await original_send(client, request, **kwargs)


if __name__ == "__main__":
    names = sys.argv[1:]
    suite = (unittest.defaultTestLoader.loadTestsFromNames(names) if names
             else unittest.defaultTestLoader.discover(str(test_dir)))
    with patch.object(httpx.AsyncClient, "send", new=offline_send):
        result = unittest.TextTestRunner(verbosity=1).run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
