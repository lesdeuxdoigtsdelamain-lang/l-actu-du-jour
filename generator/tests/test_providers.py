"""No live credentials or external network in the unit tests."""
import json
import httpx
import pytest
from generator.app.providers import Groq, Providers, MODEL, MAX_CALLS, TOKEN_BUDGET
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


def test_cloudflare_schema_follows_editorial_mode_without_mutating_default():
    schemas=[]
    def respond(req):
        schema=json.loads(req.content)['response_format']['json_schema']
        schemas.append(schema)
        count=schema['properties']['paragraphes']['minItems']
        body={**ARTICLE,'paragraphes':ARTICLE['paragraphes'][:count],
              'preuves':ARTICLE['preuves'][:count]}
        return httpx.Response(200,json={'success':True,'result':{'response':body}})
    cf=Cloudflare('a'*32,'TEST_ONLY',client=httpx.Client(transport=httpx.MockTransport(respond)))
    assert len(cf.run({'mode_editorial':'tour_france'})['paragraphes'])==4
    assert len(cf.run({'mode_editorial':'article'})['paragraphes'])==6
    for schema,count in zip(schemas,[4,6]):
        for key in ('paragraphes','preuves'):
            assert schema['properties'][key]['minItems']==count
            assert schema['properties'][key]['maxItems']==count
        assert schema['properties']['paragraphes']['items']['minLength']==300


@pytest.mark.parametrize('failure',['server','network','truncated','invalid_json'])
def test_transient_groq_failure_does_not_disable_later_articles(failure):
    calls=[]
    def respond(req):
        calls.append(req)
        if len(calls)>1:return groq_response(req)
        if failure=='server':return httpx.Response(503,content=b'SECRET_DO_NOT_LOG')
        if failure=='network':raise httpx.ReadTimeout('SECRET_DO_NOT_LOG',request=req)
        if failure=='truncated':
            return httpx.Response(200,json={'choices':[{'finish_reason':'length',
                'message':{'content':'SECRET_DO_NOT_LOG'}}],'usage':{'total_tokens':2400}})
        return httpx.Response(200,content=b'SECRET_DO_NOT_LOG')
    g=Groq('TEST_ONLY',httpx.Client(transport=httpx.MockTransport(respond)),sleeper=lambda _:None)
    cf=Cloudflare('a'*32,'TEST_ONLY',client=httpx.Client(transport=httpx.MockTransport(
        lambda req:httpx.Response(200,json={'success':True,'result':{'response':ARTICLE}}))))
    p=Providers(g,cf)
    assert p.run({})==ARTICLE
    assert p.last_provider=='cloudflare'
    assert p.run({})==ARTICLE
    assert p.last_provider=='groq'
    assert g.calls==2 and cf.calls==1 and p.calls==2
    assert not p.disabled
    assert 'SECRET_DO_NOT_LOG' not in ' '.join(p.errors)


def test_transient_failure_without_fallback_can_recover_on_later_attempt():
    attempts=[]
    def respond(req):
        attempts.append(req)
        return httpx.Response(503) if len(attempts)==1 else groq_response(req)
    g=Groq('TEST_ONLY',httpx.Client(transport=httpx.MockTransport(respond)),sleeper=lambda _:None)
    p=Providers(g)
    with pytest.raises(AiError) as error:p.run({})
    assert error.value.retryable is True
    assert p.run({})==ARTICLE
    assert p.calls==2


def test_fatal_failure_disables_provider_for_rest_of_run():
    g=Groq('TEST_ONLY',httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(401))))
    p=Providers(g)
    for _ in range(2):
        with pytest.raises(AiError) as error:p.run({})
        assert error.value.retryable is False
    assert g.calls==1
    assert p.disabled=={'groq'}


def test_cloudflare_server_error_is_retryable_but_limit_is_not():
    cf=Cloudflare('a'*32,'TEST_ONLY',max_calls=1,client=httpx.Client(transport=httpx.MockTransport(
        lambda req:httpx.Response(503,content=b'SECRET_DO_NOT_LOG'))))
    with pytest.raises(AiError) as transient:cf.run({})
    assert transient.value.retryable is True
    with pytest.raises(AiError) as limit:cf.run({})
    assert limit.value.retryable is False
    assert cf.calls==1


def test_groq_schema_follows_editorial_mode_without_mutating_default():
    schemas=[]
    def respond(req):
        schemas.append(json.loads(req.content)['response_format']['json_schema']['schema'])
        return groq_response(req)
    g=Groq('TEST_ONLY',httpx.Client(transport=httpx.MockTransport(respond)),sleeper=lambda _:None)
    g.run({'mode_editorial':'tour_france'})
    g.run({'mode_editorial':'article'})
    for schema,count in zip(schemas,[4,6]):
        for key in ('paragraphes','preuves'):
            assert schema['properties'][key]['minItems']==count
            assert schema['properties'][key]['maxItems']==count


def test_recovered_rate_limit_and_numeric_headers_remain_visible_in_diagnostics():
    requests=[]
    def respond(req):
        requests.append(req)
        if len(requests)==1:
            return httpx.Response(429,headers={'retry-after':'2'},content=b'SECRET_DO_NOT_LOG')
        response=groq_response(req)
        response.headers.update({'x-ratelimit-limit-requests':'1000',
            'x-ratelimit-remaining-requests':'999','x-ratelimit-limit-tokens':'8000',
            'x-ratelimit-reset-tokens':'SECRET_DO_NOT_LOG'})
        return response
    p=Providers(Groq('TEST_ONLY',httpx.Client(transport=httpx.MockTransport(respond)),sleeper=lambda _:None))
    assert p.run({})==ARTICLE
    stats=p.usage_stats()
    assert stats['diagnostics']==[{'provider':'groq','category':'rate_limit',
        'retryable':True,'http_status':429,'retry_after_seconds':2.0}]
    assert stats['groq_rate_limits']=={'requests_limit_per_day':1000,
        'requests_remaining_per_day':999,'tokens_limit_per_minute':8000,
        'tokens_remaining_per_minute':7000}
    assert stats['disabled_providers']==[]
    assert stats['groq_requests']==2
    assert 'SECRET_DO_NOT_LOG' not in json.dumps(stats)


@pytest.mark.parametrize('status,category,retryable',[
    (401,'authentication',False),(403,'authentication',False),
    (429,'rate_limit',False),(503,'transient',True),(400,'provider_error',False),
])
def test_usage_diagnostics_distinguish_http_failures_without_logging_response(status,category,retryable):
    g=Groq('TEST_ONLY',httpx.Client(transport=httpx.MockTransport(
        lambda req:httpx.Response(status,headers={'retry-after':'3600',
            'x-ratelimit-remaining-tokens':'SECRET_DO_NOT_LOG'},content=b'SECRET_DO_NOT_LOG'))))
    p=Providers(g)
    with pytest.raises(AiError):p.run({'source':'PRIVATE_SOURCE_DO_NOT_LOG'})
    stats=p.usage_stats()
    assert stats['diagnostics'][0]['category']==category
    assert stats['diagnostics'][0]['retryable']==retryable
    assert stats['diagnostics'][0]['http_status']==status
    assert stats['groq_rate_limits']=={}
    assert 'SECRET_DO_NOT_LOG' not in json.dumps(stats)
    assert 'PRIVATE_SOURCE_DO_NOT_LOG' not in json.dumps(stats)


@pytest.mark.parametrize('attribute,value,limit',[
    ('calls',MAX_CALLS,'groq_calls'),('tokens',TOKEN_BUDGET,'groq_tokens'),
])
def test_groq_local_ceiling_is_distinct_from_provider_rate_limit(attribute,value,limit):
    g=Groq('TEST_ONLY',httpx.Client(transport=httpx.MockTransport(
        lambda req:pytest.fail('A local budget must stop the request before HTTP'))))
    setattr(g,attribute,value)
    p=Providers(g)
    with pytest.raises(AiError):p.run({})
    stats=p.usage_stats()
    assert stats['diagnostics']==[{'provider':'groq','category':'local_limit',
        'retryable':False,'limit':limit,'ceiling':value}]
    assert stats['limits']['groq_tokens']==65000
    assert stats['limits']['groq_calls']==18


def test_writer_call_ceiling_has_separate_diagnostic():
    p=Providers()
    p.calls=MAX_CALLS
    with pytest.raises(AiError):p.run({})
    assert p.usage_stats()['diagnostics']==[{'provider':'writer','category':'local_limit',
        'retryable':False,'limit':'writer_calls','ceiling':18}]
    assert p.calls==18


HISTORY={'lines':['Phrase historique fictive.']*6,'evidence':[['h1']]*6}


def test_history_uses_short_schema_and_shares_article_usage_budget():
    requests=[]
    def respond(req):
        payload=json.loads(req.content)
        requests.append(payload)
        if len(requests)==1:return groq_response(req)
        assert payload['max_completion_tokens']==1000
        assert payload['response_format']['json_schema']['name']=='ce_jour_la'
        assert 'Ce jour-là' in payload['messages'][0]['content']
        schema=payload['response_format']['json_schema']['schema']
        assert set(schema['properties'])=={'lines','evidence'}
        assert schema['properties']['lines']['minItems']==6
        assert schema['properties']['lines']['maxItems']==10
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop',
            'message':{'content':json.dumps(HISTORY)}}],'usage':{'total_tokens':600}})
    g=Groq('TEST_ONLY',httpx.Client(transport=httpx.MockTransport(respond)),sleeper=lambda _:None)
    p=Providers(g)
    assert p.run({})==ARTICLE
    assert p.run_history({'edition_date':'2026-11-24','event_date':'1969-11-24',
        'facts':[{'id':'h1','text':'Fait fictif.'}]})==HISTORY
    assert p.calls==2 and g.calls==2 and g.tokens==1834


def test_history_rate_limit_does_not_retry_or_consume_fallback():
    sleeps=[]
    g=Groq('TEST_ONLY',httpx.Client(transport=httpx.MockTransport(
        lambda req:httpx.Response(429,headers={'retry-after':'2'}))),sleeper=sleeps.append)
    cf=Cloudflare('a'*32,'TEST_ONLY',client=httpx.Client(transport=httpx.MockTransport(
        lambda req:pytest.fail('The history supplement must not make a fallback HTTP request'))))
    p=Providers(g,cf)
    with pytest.raises(AiError):p.run_history({})
    assert g.calls==1 and cf.calls==0 and p.calls==1
    assert sleeps==[]
    assert p.usage_stats()['diagnostics'][0]['category']=='rate_limit'


def test_history_works_with_cloudflare_only_and_has_same_local_ceiling():
    def respond(req):
        payload=json.loads(req.content)
        assert payload['max_tokens']==1000
        assert set(payload['response_format']['json_schema']['properties'])=={'lines','evidence'}
        return httpx.Response(200,json={'success':True,'result':{'response':HISTORY}})
    cf=Cloudflare('a'*32,'TEST_ONLY',max_calls=1,
        client=httpx.Client(transport=httpx.MockTransport(respond)))
    p=Providers(cloudflare=cf)
    assert p.run_history({})==HISTORY
    with pytest.raises(AiError):p.run_history({})
    assert cf.calls==1
    assert p.usage_stats()['diagnostics']==[{'provider':'cloudflare','category':'local_limit',
        'retryable':False,'limit':'cloudflare_calls','ceiling':1}]


def test_groq_json_validation_error_is_sanitized_and_does_not_disable_later_articles(capsys):
    requests=[]
    def respond(req):
        requests.append(req)
        if len(requests)==1:
            return httpx.Response(400,json={'error':{'code':'json_validate_failed',
                'type':'invalid_request_error','message':'PRIVATE_SOURCE_DO_NOT_LOG',
                'failed_generation':'SECRET_DO_NOT_LOG'}})
        return groq_response(req)
    g=Groq('TEST_ONLY',httpx.Client(transport=httpx.MockTransport(respond)),sleeper=lambda _:None)
    p=Providers(g)
    with pytest.raises(AiError) as first:p.run({})
    assert first.value.retryable is True
    assert g.calls==1  # No immediate HTTP retry for the rejected generation.
    assert not p.disabled
    assert p.run({})==ARTICLE
    assert g.calls==2 and p.calls==2
    assert p.usage_stats()['diagnostics']==[{'provider':'groq','category':'invalid_response',
        'retryable':True,'http_status':400,'error_code':'json_validate_failed',
        'error_type':'invalid_request_error'}]
    all_output=json.dumps(p.usage_stats())+' '.join(p.errors)+str(first.value)+capsys.readouterr().out
    assert 'SECRET_DO_NOT_LOG' not in all_output
    assert 'PRIVATE_SOURCE_DO_NOT_LOG' not in all_output


@pytest.mark.parametrize('code,category',[
    ('context_length_exceeded','provider_error'),('invalid_api_key','authentication'),
    ('strict_schema_unsupported','configuration'),('invalid_json_schema','configuration'),
])
def test_groq_fatal_400_codes_remain_fatal_and_distinguishable(code,category):
    g=Groq('TEST_ONLY',httpx.Client(transport=httpx.MockTransport(
        lambda req:httpx.Response(400,json={'error':{'code':code,'type':'invalid_request_error',
            'message':'SECRET_DO_NOT_LOG'}}))))
    p=Providers(g)
    for _ in range(2):
        with pytest.raises(AiError) as error:p.run({})
        assert error.value.retryable is False
    assert g.calls==1 and p.disabled=={'groq'}
    diagnostic=p.usage_stats()['diagnostics'][0]
    assert diagnostic['category']==category and diagnostic['error_code']==code
    assert 'SECRET_DO_NOT_LOG' not in json.dumps(p.usage_stats())


@pytest.mark.parametrize('error_body',[
    {'error':{'code':'SECRET_DO_NOT_LOG','type':'SECRET_DO_NOT_LOG',
              'message':'json_validate_failed','failed_generation':'PRIVATE_SOURCE_DO_NOT_LOG'}},
    {'error':{'code':['json_validate_failed'],'type':{'token':'SECRET_DO_NOT_LOG'}}},
    {'error':'SECRET_DO_NOT_LOG'},
    ['SECRET_DO_NOT_LOG'],
])
def test_groq_error_metadata_is_strictly_allowlisted_without_message_inference(error_body,capsys):
    g=Groq('TEST_ONLY',httpx.Client(transport=httpx.MockTransport(
        lambda req:httpx.Response(400,json=error_body))))
    p=Providers(g)
    with pytest.raises(AiError):p.run({})
    diagnostic=p.usage_stats()['diagnostics'][0]
    assert diagnostic=={'provider':'groq','category':'provider_error',
        'retryable':False,'http_status':400}
    output=json.dumps(p.usage_stats())+' '.join(p.errors)+capsys.readouterr().out
    assert 'SECRET_DO_NOT_LOG' not in output
    assert 'PRIVATE_SOURCE_DO_NOT_LOG' not in output
