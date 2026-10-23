"""Polite, bounded HTTP. Never include a credential-bearing URL in errors."""
import time
import requests

class FetchError(RuntimeError):
    pass

class Client:
    def __init__(self, headers=None, interval=.15):
        self.session=requests.Session()
        self.session.headers.update(headers or {})
        self.interval=interval
        self.last=0.

    def get(self,url,params=None):
        for attempt in range(4):
            time.sleep(max(0,self.interval-(time.monotonic()-self.last)))
            self.last=time.monotonic()
            try:
                response=self.session.get(url,params=params,timeout=90)
            except requests.RequestException:
                if attempt<3:
                    time.sleep(2**attempt); continue
                raise FetchError('Financial data network failure; no credentials logged') from None
            if response.status_code in {429,500,502,503,504} and attempt<3:
                time.sleep(2**attempt); continue
            if response.status_code!=200:
                raise FetchError(f'Financial data HTTP {response.status_code}; check credentials/access')
            return response
        raise FetchError('Financial request exhausted retries')
