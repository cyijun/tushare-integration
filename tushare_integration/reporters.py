import importlib
import logging
from urllib.parse import urlparse

import requests


class Reporter(object):
    def send_report(self, subject: str, content: str, *args, **kwargs):
        raise NotImplementedError


class FeishuWebHookReporter(Reporter):
    def __init__(self, webhook: str, *args, **kwargs):
        self.webhook = webhook
        super(FeishuWebHookReporter, self).__init__(*args, **kwargs)

    def send_report(self, subject: str, content: str, *args, **kwargs):
        if not self.webhook:
            logging.info('No feishu webhook, skip send report')
            return

        # SSRF防护：校验域名
        parsed = urlparse(self.webhook)
        if not parsed.netloc.endswith('open.feishu.cn'):
            logging.error(f'Invalid feishu webhook domain: {parsed.netloc}')
            raise ValueError(f'Invalid feishu webhook domain: {parsed.netloc}')

        # 将content按照\n分割
        body = {
            "msg_type": "post",
            "content": {
                "post": {
                    "zh_cn": {
                        "title": subject,
                        "content": [[{"text": f'{_}\n', "tag": "text"} for _ in content.split('\n')]],
                    }
                }
            },
        }

        # 找到最后一个content，移除掉末尾的\n
        if body['content']['post']['zh_cn']['content'][-1][-1]['text'] == '\n':
            body['content']['post']['zh_cn']['content'][-1][-1]['text'] = body['content']['post']['zh_cn']['content'][
                -1
            ][-1]['text'][:-1]

        resp = requests.post(self.webhook, json=body, timeout=10)
        if resp.status_code != 200:
            logging.error(
                f'Send report to feishu webhook failed, status code: {resp.status_code}, response: {resp.text}'
            )
        else:
            logging.info(f'Send report to feishu webhook, status code: {resp.status_code}, response: {resp.text}')

    @classmethod
    def from_settings(cls, settings):
        return cls(webhook=settings.get('FEISHU_WEBHOOK'))


class ReporterLoader(object):
    ALLOWED_REPORTERS = ['tushare_integration.reporters.FeishuWebHookReporter']

    def __init__(self, settings: dict):
        self.settings = settings
        self.reporters = settings.get('REPORTERS', [])
        logging.info(f'Load reporters: {self.reporters}')

    def get_reporters(self):
        reporters = []
        for reporter in self.reporters:
            if reporter not in self.ALLOWED_REPORTERS:
                raise ValueError(f'Reporter {reporter} is not allowed')
            package, class_name = reporter.rsplit('.', 1)
            module = importlib.import_module(package)
            cls = getattr(module, class_name)
            reporters.append(cls.from_settings(self.settings))

        return reporters
