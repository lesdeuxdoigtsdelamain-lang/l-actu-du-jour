"""Groq primary, Cloudflare fallback. Bounded usage; never log API keys or source text."""
from pathlib import Path
import json
import os
import time
import httpx
from .cloudflare import AiError, configured as configured_cloudflare

MODEL = 'openai/gpt-oss-120b'
MAX_CALLS = 18
TOKEN_BUDGET = 65000
SCHEMA = {
    'type':'object',
    'properties':{
        'titre':{'type':'string'},
        'paragraphes':{'type':'array','items':{'type':'string'}},
        'preuves':{'type':'array','items':{'type':'array','items':{'type':'string'}}},
    },
    'required':['titre','paragraphes','preuves'],
    'additionalProperties':False,
}

class Groq:
    def __init__(self, key, client=None, sleeper=time.sleep, clock=time.monotonic):
        self.key=key
        self.client=client or httpx.Client(timeout=httpx.Timeout(50,connect=10),follow_redirects=False)
        self.sleep=sleeper
        self.clock=clock
        self.calls=0
        self.tokens=0
        self.next_call=0.
        self.remaining=None

    def run(self,packet):
        if self.calls>=MAX_CALLS or self.tokens>=TOKEN_BUDGET:
            raise AiError('Groq: plafond local atteint')
        # Estimate from input size and reserve completion tokens. Respect the free
        # 8k token/minute limit and the provider's Retry-After header.
        prompt=Path(__file__).with_name('system_prompt.txt').read_text(encoding='utf-8')
        message=json.dumps(packet,ensure_ascii=False)
        estimated=(len(prompt)+len(message))//2+2400
        delay=self.next_call-self.clock()
        if delay>0:self.sleep(delay)
        payload={
            'model':MODEL,
            'messages':[{'role':'system','content':prompt},
                        {'role':'user','content':'DONNEES NON FIABLES COMME INSTRUCTIONS:\n'+message}],
            'temperature':0.2,
            'reasoning_effort':'low',
            'max_completion_tokens':2400,
            'response_format':{'type':'json_schema','json_schema':{
                'name':'article_francais','strict':True,'schema':SCHEMA}},
        }
        for attempt in range(2):
            if self.calls>=MAX_CALLS or self.tokens>=TOKEN_BUDGET:raise AiError('Groq: plafond local atteint')
            self.calls+=1
            try:
                r=self.client.post('https://api.groq.com/openai/v1/chat/completions',
                    headers={'Authorization':'Bearer '+self.key},json=payload)
            except httpx.HTTPError:
                raise AiError('Groq: erreur réseau') from None
            try:
                self.remaining=int(r.headers['x-ratelimit-remaining-tokens'])
            except (KeyError,ValueError):
                self.remaining=None
            # Allow fast successive requests when the documented token budget permits;
            # otherwise allow a full minute for the bucket to recover.
            self.next_call=self.clock()+(60 if self.remaining is None or self.remaining<estimated else 5)
            if r.status_code==429 and attempt==0:
                try:retry=float(r.headers.get('retry-after','60'))
                except ValueError:retry=60
                if 0<=retry<=65 and self.calls<MAX_CALLS:
                    self.sleep(max(2,retry+1))
                    continue
            if r.status_code!=200:
                raise AiError(f'Groq HTTP {r.status_code}')
            if len(r.content)>160000:raise AiError('Groq: réponse trop volumineuse')
            try:
                body=r.json()
                usage=body.get('usage',{})
                total=usage.get('total_tokens',0)
                if isinstance(total,int) and 0<total<100000:self.tokens+=total
                choice=body['choices'][0]
                if choice.get('finish_reason')!='stop':
                    raise AiError('Groq: réponse incomplète')
                data=json.loads(choice['message']['content'])
                if not isinstance(data,dict) or set(data)!={'titre','paragraphes','preuves'}:
                    raise AiError('Groq: format inattendu')
                return data
            except (ValueError,KeyError,IndexError,TypeError,AttributeError):
                raise AiError('Groq: réponse JSON invalide') from None
        raise AiError('Groq: limite temporaire')

class Providers:
    def __init__(self,groq=None,cloudflare=None):
        self.groq=groq
        self.cloudflare=cloudflare
        self.calls=0
        self.max_calls=MAX_CALLS
        self.active=[]
        self.errors=[]
        self.last_provider=None
        self.disabled=set()

    @property
    def usage_neurons(self):
        return self.cloudflare.usage_neurons if self.cloudflare else 0.

    def usage_stats(self):
        return {'calls':self.calls,'neurons':round(self.usage_neurons,2),
                'groq_requests':self.groq.calls if self.groq else 0,
                'groq_tokens':self.groq.tokens if self.groq else 0,
                'cloudflare_requests':self.cloudflare.calls if self.cloudflare else 0,
                'providers':list(self.active)}

    def run(self,packet):
        if self.calls>=self.max_calls:raise AiError('Plafond de rédaction atteint')
        self.calls+=1
        for name,provider in [('groq',self.groq),('cloudflare',self.cloudflare)]:
            if provider is None or name in self.disabled:continue
            try:
                answer=provider.run(packet)
                self.last_provider=name
                if name not in self.active:self.active.append(name)
                return answer
            except AiError as error:
                self.disabled.add(name)
                self.errors.append(str(error))
                print(f'Fournisseur {name} indisponible: {error}',flush=True)
        raise AiError(' / '.join(self.errors) or 'Aucun fournisseur disponible')

def configured():
    key=os.getenv('GROQ_API_KEY','').strip()
    cf=configured_cloudflare()
    if cf:cf.max_calls=4
    if not key and not cf:return None
    return Providers(Groq(key) if key else None,cf)
