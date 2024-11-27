from app import Config
import requests
import json
import logging


class OpenAIProxy:
    def __init__(self):
        self.url = Config.OPENAI_PROXY_ADDR

    def create_thread(self):
        url = f'{self.url}/v1/create_thread'
        response = requests.post(url)
        return response.text

    def ask_assistant(self, assistant_id, content, thread_id=None):
        url = f'{self.url}/v1/ask-assistant'
        headers = {
            'Content-Type': 'application/json',
        }
        body = json.dumps({
            'assistant_id': assistant_id,
            'content': content,
            'thread_id': thread_id
        })
        response = requests.post(url, headers=headers, data=body)
        if response.status_code == 200:
            return response.json().get('content', '').strip()
        logging.warning(response.json())
        return False