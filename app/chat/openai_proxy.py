from app import Config, db
from app.models import Message
from flask_login import current_user
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

    def ask_assistant(self, content, user):
        thread_id = user.gpt_thread
        if not thread_id:
            thread_id = self.create_thread()
            user.gpt_thread = thread_id
            db.session.commit()

        url = f'{self.url}/v1/ask-assistant'
        headers = {
            'Content-Type': 'application/json',
        }
        body = json.dumps({
            'assistant_id': Config.OPENAI_GPT_ASSISTANT_ID,
            'content': content,
            'thread_id': thread_id
        })
        response = requests.post(url, headers=headers, data=body)
        if response.status_code == 200:
            return response.json().get('content', '').strip()
        logging.warning(response.__dict__)
        return False