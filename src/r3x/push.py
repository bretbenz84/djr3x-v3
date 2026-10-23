"""Push notifications to the owner's phone through ntfy (owner choice, October 22, for sentry mode).

ntfy.sh is a free public service: whoever knows a topic name can read and post to it, so the topic is
a long random name kept in local/secrets.toml (ntfy_topic), never in tracked files or logs. A photo
travels as the message's attachment and stays on ntfy's server for a few hours. Sending is the only
network use; failures come back as a structured result and are retried twice.
"""
import re
import time

NTFY_SERVER = 'https://ntfy.sh'
TOPIC = re.compile(r'[A-Za-z0-9_-]{12,64}')
RETRY_SECONDS = (2., 5.)


def valid_topic(topic: str) -> bool:
    return bool(TOPIC.fullmatch(topic or ''))


def new_topic() -> str:
    """A private topic name: 'rex-' and 16 random URL-safe characters."""
    import secrets
    return 'rex-' + secrets.token_urlsafe(12)


def _header(text: str, limit: int = 250) -> str:
    """HTTP header values are plain ASCII; Rex's own wording only."""
    return ''.join(c if 32 <= ord(c) < 127 else ' ' for c in text.replace('’', "'"))[:limit].strip()


class Push:
    def __init__(self, topic: str, trace=None, *, post=None, wait=time.sleep):
        if not valid_topic(topic):
            raise ValueError('InvalidNtfyTopic')
        self.url = NTFY_SERVER + '/' + topic
        self.trace, self.wait = trace, wait
        self.post = post or self._post

    @staticmethod
    def _post(url, headers, body):
        import httpx
        response = httpx.post(url, headers=headers, content=body, timeout=httpx.Timeout(20.))
        return response.status_code

    def send(self, title: str, message: str, photo: bytes | None = None, *, priority: str = 'high',
             tags: str = 'rotating_light') -> dict:
        headers = {'Title': _header(title), 'Priority': priority, 'Tags': tags}
        if photo:
            # With an attachment the body is the file and the words travel in a header.
            headers.update({'Filename': 'rex.jpg', 'Message': _header(message, 1000)})
            body = photo
        else:
            body = message.encode('utf-8')
        status, error = None, None
        for attempt in range(len(RETRY_SECONDS) + 1):
            try:
                status = self.post(self.url, headers, body)
                error = None
                if status < 300 or 400 <= status < 429 or 429 < status < 500:
                    break   # Delivered, or refused for a reason a retry cannot fix.
            except Exception as exc:
                error = type(exc).__name__
            if attempt < len(RETRY_SECONDS):
                self.wait(RETRY_SECONDS[attempt])
        result = {'outcome': 'sent' if status is not None and status < 300 else 'failed',
                  'status': status, 'error': error, 'attempts': attempt + 1, 'photo': bool(photo)}
        if self.trace:
            self.trace.emit('push_notification', title=headers['Title'], **result)
        return result
