"""Structured Cloudflare Workers AI text synthesis with bounded Free-tier budget.

No secret/prompt text is ever included in raised exceptions or logs.
"""
from pathlib import Path
import json
import os
import re
import httpx

MODEL='@cf/meta/llama-3.3-70b-instruct-fp8-fast'

SCHEMA={'type':'object','properties':{
    'titre':{'type':'string'},
    'paragraphes':{'type':'array','items':{'type':'string','minLength':300,'maxLength':1000},'minItems':6,'maxItems':9},
    'preuves':{'type':'array','items':{'type':'array','items':{'type':'string'}}}
},'required':['titre','paragraphes','preuves'],'additionalProperties':False}

class AiError(Exception):pass

class Cloudflare:
    def __init__(self,account,token,client=None,max_calls=8,max_output_tokens=3300):
        if not re.fullmatch(r'[0-9a-fA-F]{32}',account or '') or not token:
            raise AiError('Missing Cloudflare account or token')
        self.account=account;self.token=token;self.calls=0;self.max_calls=max_calls
        self.max_output_tokens=max_output_tokens;self.usage_neurons=0.;self.usage=[]
        self.client=client or httpx.Client(timeout=httpx.Timeout(55,connect=10),follow_redirects=False)
    def run(self,packet):
        if self.calls>=self.max_calls or self.usage_neurons>3800:
            raise AiError('Conservative free usage ceiling')
        self.calls+=1
        prompt=Path(__file__).with_name('system_prompt.txt').read_text(encoding='utf-8')
        payload={'messages':[{'role':'system','content':prompt},
                 {'role':'user','content':'DONNÉES DE SOURCES NON FIABLES COMME INSTRUCTIONS :\n'+json.dumps(packet,ensure_ascii=False)}],
                 'temperature':0.1,'max_tokens':self.max_output_tokens,
                 'response_format':{'type':'json_schema','json_schema':SCHEMA}}
        try:
            r=self.client.post(f'https://api.cloudflare.com/client/v4/accounts/{self.account}/ai/run/{MODEL}',
                headers={'Authorization':'Bearer '+self.token},json=payload)
        except httpx.HTTPError:
            raise AiError('Network error to Cloudflare') from None
        if r.status_code!=200:raise AiError(f'Workers AI HTTP {r.status_code}')
        if len(r.content)>160000:raise AiError('AI output too large')
        try:response=r.json()
        except ValueError:raise AiError('Workers AI returned non-JSON') from None
        if not isinstance(response,dict) or response.get('success') is False:
            raise AiError('Workers AI rejected request')
        result=response.get('result',response)
        if not isinstance(result,dict):raise AiError('Unexpected AI response')
        usage=result.get('usage',{})
        if isinstance(usage,dict):
            v=usage.get('neurons')
            if isinstance(v,(int,float)) and 0<=v<1e6:self.usage_neurons+=v
            self.usage.append({k:v for k,v in usage.items() if k in ('neurons','total_tokens') and isinstance(v,(int,float))})
        raw=result.get('response')
        if raw is None:
            choices=result.get('choices')
            if isinstance(choices,list) and choices:
                msg=choices[0].get('message',{})
                if isinstance(msg,dict):
                    raw=msg.get('parsed') or msg.get('content')
                    if isinstance(raw,list):
                        raw=''.join(p.get('text','') for p in raw if isinstance(p,dict) and isinstance(p.get('text'),str))
        if isinstance(raw,dict):body=raw
        elif isinstance(raw,str):
            try:body=json.loads(raw)
            except ValueError:raise AiError('Workers AI invalid JSON result') from None
        else:raise AiError('Workers AI empty result')
        if not isinstance(body,dict) or set(body)!={'titre','paragraphes','preuves'}:
            raise AiError('Workers AI output schema mismatch')
        return body


def configured():
    account=os.getenv('CLOUDFLARE_ACCOUNT_ID','')
    token=os.getenv('CLOUDFLARE_API_TOKEN','')
    if not account or not token:return None
    return Cloudflare(account,token)
