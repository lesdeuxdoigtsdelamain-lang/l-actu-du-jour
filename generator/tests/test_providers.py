"""No live credentials or external network in the unit tests."""
import json
import httpx
import pytest
from generator.app.providers import Groq, Providers, MODEL
from generator.app.cloudflare import Cloudflare, AiError

ARTICLE={'titre':'Article de test sans actualité réelle',
         'paragraphes':['Paragraphe fictif']*6,'preuves':[['f1']]*6}

def groq_response(req):
    assert str(req.url)=='https://api.groq.com/openai/v1/chat/completions'
    payload=json.loads(req.content)
    assert payload['model']==MODEL
    assert payload['response_format']['json_schema']['strict'] is True
    assert payload['max_completion_tokens']==2400
    assert req.headers['Authorization']=='Bearer TEST_ONLY'
    return httpx.Response(200,headers={'x-ratelimit-remaining-tokens':'7000'},
        json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(ARTICLE)}}],
              'usage':{'total_tokens':1234}})

def test_groq_json_and_usage():
    g=Groq('TEST_ONLY',httpx.Client(transport=httpx.MockTransport(groq_response)),sleeper=lambda x:None)
    assert g.run({'facts':[]})==ARTICLE
    assert g.calls==1 and g.tokens==1234

def test_groq_rate_limit_waits_then_recovers():
    seen=[]
    sleeps=[]
    def respond(req):
        seen.append(req)
        if len(seen)==1:return httpx.Response(429,headers={'retry-after':'2'})
        return groq_response(req)
    g=Groq('TEST_ONLY',httpx.Client(transport=httpx.MockTransport(respond)),sleeper=sleeps.append)
    assert g.run({})==ARTICLE
    assert g.calls==2 and sleeps==[3]

def test_primary_groq_does_not_use_cloudflare():
    g=Groq('TEST_ONLY',httpx.Client(transport=httpx.MockTransport(groq_response)))
    cf=Cloudflare('a'*32,'TEST_ONLY',client=httpx.Client(transport=httpx.MockTransport(
        lambda req:pytest.fail('Fallback must not run when Groq succeeds'))))
    p=Providers(g,cf)
    assert p.run({})==ARTICLE
    assert p.usage_stats()['providers']==['groq']
    assert cf.calls==0

def test_groq_failure_switches_to_cloudflare_once():
    g=Groq('TEST_ONLY',httpx.Client(transport=httpx.MockTransport(
        lambda req:httpx.Response(429,headers={'retry-after':'3600'},content=b'SECRET_DO_NOT_LOG'))))
    cf=Cloudflare('a'*32,'TEST_ONLY',client=httpx.Client(transport=httpx.MockTransport(
        lambda req:httpx.Response(200,json={'success':True,'result':{'response':ARTICLE,'usage':{'neurons':10}}}))))
    p=Providers(g,cf)
    assert p.run({})==ARTICLE
    assert p.run({})==ARTICLE
    assert g.calls==1 and cf.calls==2
    assert p.usage_stats()['providers']==['cloudflare']
    assert p.usage_neurons==20
    assert 'SECRET_DO_NOT_LOG' not in ' '.join(p.errors)

def test_both_unavailable_fail_without_secret():
    g=Groq('TEST_ONLY',httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(401,content=b'SECRET_DO_NOT_LOG'))))
    p=Providers(g)
    with pytest.raises(AiError) as err:p.run({})
    assert 'Groq HTTP 401' in str(err.value)
    assert 'SECRET_DO_NOT_LOG' not in str(err.value)
