import logging
from datetime import datetime
from config import Config
import requests
import json
import os


class YAGPT:
    def __init__(self):
        self.iam_token = YAGPT.get_yandex_iam_token()

    @staticmethod
    def create_yandex_iam_token():
        iam_url = 'https://iam.api.cloud.yandex.net/iam/v1/tokens'
        payload = {
            'yandexPassportOauthToken': Config.YANDEX_OAUTH_TOKEN
        }
        iam = requests.post(url=iam_url, data=json.dumps(payload))

        try:
            with open(Config.IAM_CREDENTIALS_FILE, 'w') as token:
                token.write(f"{iam.json()['iamToken']}\n{iam.json()['expiresAt']}")
            return iam.json()['iamToken'].strip()
        except Exception as e:
            logging.info(e)
            return None

    @staticmethod
    def get_yandex_iam_token():
        if os.path.exists(Config.IAM_CREDENTIALS_FILE):
            with open(Config.IAM_CREDENTIALS_FILE, 'r') as f:
                iam_token = f.readline().strip()
                expiration_date = f.readline().strip()
                expires_at = datetime.strptime(expiration_date[:26] + expiration_date[-1:], '%Y-%m-%dT%H:%M:%S.%fZ')
                if datetime.now() > expires_at:
                    logging.info('IAM токен истек, генерируем новый')
                    return YAGPT.create_yandex_iam_token()
                else:
                    return iam_token
        else:
            return YAGPT.create_yandex_iam_token()

    def completion(self, text, model=Config.YANDEX_GPT_MODEL, stream=False, temperature=0.6, max_tokens=2000):
        url = 'https://llm.api.cloud.yandex.net/foundationModels/v1/completion'
        headers = {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {self.iam_token}',
            'x-folder-id': Config.YANDEX_CATALOG_ID
        }
        payload = {
            'modelUri': f'gpt://{Config.YANDEX_CATALOG_ID}/{model}',
            "completionOptions": {
                "stream": stream,
                "temperature": temperature,
                "maxTokens": str(max_tokens)
            },
            "messages": [
                {
                    "role": "user",
                    "text": text
                }
            ]
        }

        response = requests.post(url=url, headers=headers, data=json.dumps(payload))
        if os.environ.get('DEBUG'):
            logging.info(response.status_code)
            logging.info(response.json())
        return response.json()['result']['alternatives'][0]['message']['text']
