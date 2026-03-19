from typing import Dict, Optional
import os

import requests


class LLMApiClient:
    """云端大模型 API 调用封装。"""

    def __init__(
        self,
        base_url: str,
        api_path: str = "/v1/tongue-analyze",
        text_api_path: str = "/v1/text-chat",
        timeout: int = 90,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_path = api_path
        self.text_api_path = text_api_path
        self.timeout = timeout

    def analyze_tongue_image(
        self,
        image_path: str,
        user_note: str = "",
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> Dict:
        headers = {}
        if extra_headers:
            headers.update(extra_headers)

        question = user_note or "请从中医角度详细解读这张舌苔，包括体质分析和调理建议。"
        form_data = {
            "question": question,
            "max_new_tokens": "512",
        }
        url = f"{self.base_url}{self.api_path}"
        with open(image_path, "rb") as f:
            files = {"file": (os.path.basename(image_path), f, "application/octet-stream")}
            resp = requests.post(url, data=form_data, files=files, headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

    def text_chat(
        self,
        question: str,
        extra_headers: Optional[Dict[str, str]] = None,
        max_new_tokens: int = 512,
    ) -> Dict:
        headers = {}
        if extra_headers:
            headers.update(extra_headers)

        payload = {"question": question, "max_new_tokens": max_new_tokens}
        url = f"{self.base_url}{self.text_api_path}"
        resp = requests.post(url, json=payload, headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

