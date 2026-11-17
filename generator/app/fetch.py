"""Defensive feed fetching with scheme/host checks, size limits and timeouts."""
from urllib.parse import urlsplit,urljoin
import httpx

class FetchError(Exception): pass

class Fetcher:
    def __init__(self,hosts,client=None):
        self.hosts=set(hosts)
        self.client=client or httpx.Client(timeout=httpx.Timeout(14,connect=6),
            follow_redirects=False,headers={'User-Agent':'LActuDuJour/1.0 (non-commercial personal news reader; contact via repository)','Accept':'application/rss+xml,application/atom+xml,text/xml,text/html,application/json,*/*;q=0.4'})
        self.robot_rules={}
    def can_read_article(self,url):
        """Respect publisher robots.txt; network trouble fails closed, never bypass paywalls."""
        from urllib.robotparser import RobotFileParser
        host=urlsplit(url).hostname
        if host not in self.hosts:return False
        if host not in self.robot_rules:
            robot=f'https://{host}/robots.txt'
            try:
                contents=self.get(robot,limit=150000).decode('utf-8','replace')
                parser=RobotFileParser()
                parser.parse(contents.splitlines())
                self.robot_rules[host]=parser
            except FetchError as exc:
                # Missing robots.txt has no restrictions; 401/403 and outages deny.
                self.robot_rules[host]=True if str(exc)=='HTTP 404' else False
        rule=self.robot_rules[host]
        return rule.can_fetch('LActuDuJour',url) if isinstance(rule,RobotFileParser) else bool(rule)
    def get(self,url,limit=500000):
        for _ in range(4):
            u=urlsplit(url)
            if (u.scheme!='https' or u.hostname not in self.hosts or u.username or u.password
                or u.port not in (None,443)):
                raise FetchError('Disallowed host / scheme')
            try:
                with self.client.stream('GET',url) as response:
                    if response.status_code in (301,302,303,307,308):
                        to=response.headers.get('location')
                        if not to: raise FetchError('No redirect target')
                        url=urljoin(url,to)
                        continue
                    if response.status_code!=200:
                        raise FetchError(f'HTTP {response.status_code}')
                    buf=bytearray()
                    for part in response.iter_bytes():
                        buf.extend(part)
                        if len(buf)>limit: raise FetchError('Payload too large')
                    return bytes(buf)
            except httpx.HTTPError:
                raise FetchError('Network unavailable') from None
        raise FetchError('Too many redirects')
