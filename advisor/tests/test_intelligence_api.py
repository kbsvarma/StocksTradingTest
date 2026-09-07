import hashlib
from advisor.intelligence.api import dispatch
from advisor.intelligence.store import CallStore
from advisor.intelligence.access import Principal
from advisor.tests.test_intelligence_contract import call


def test_api_authentication_tenant_scope_and_read_only_routes(tmp_path):
    a=tmp_path/'a';b=tmp_path/'b'
    with CallStore(a/'intelligence'/'calls.sqlite',Principal('author','a','analyst')) as store:store.put(call())
    policy={'tokens':[{'sha256':hashlib.sha256(b'test-secret-a').hexdigest(),'user':'api-client','tenant':'a'},
                      {'sha256':hashlib.sha256(b'test-secret-b').hexdigest(),'user':'other','tenant':'b'}],
            'tenants':{'a':{'data_root':str(a)},'b':{'data_root':str(b)}}}
    assert dispatch('/v1/calls',None,policy)[0]==401
    assert dispatch('/v1/calls','Bearer wrong',policy)[0]==401
    assert len(dispatch('/v1/calls','Bearer test-secret-a',policy)[1]['calls'])==1
    assert dispatch('/v1/calls','Bearer test-secret-b',policy)[1]['calls']==[]
    assert dispatch('/v1/orders','Bearer test-secret-a',policy)[0]==404
    assert dispatch('/v1/calls?token=test-secret-a','Bearer test-secret-a',policy)[0]==400


def test_workspace_preferences_are_user_and_tenant_scoped(tmp_path):
    path=tmp_path/'store.sqlite'
    with CallStore(path,Principal('one','a','analyst')) as s:
        s.save_workspace({'watch':['TEST']});assert s.workspace()['watch']==['TEST']
    with CallStore(path,Principal('two','a','analyst')) as s:assert s.workspace()=={}
    with CallStore(path,Principal('one','b','analyst')) as s:assert s.workspace()=={}
