import pytest
import ci_update_all as runner
from integration import write


@pytest.fixture
def state(tmp_path,monkeypatch):
    data=tmp_path/'data';artifacts=tmp_path/'artifacts'
    monkeypatch.setattr(runner,'TOOL_ROOT',tmp_path)
    monkeypatch.setattr(runner,'DATA_ROOT',data)
    write(data/'catalog.json',{'databases':[{'dbms':'alpha'},{'dbms':'beta'}]})
    for db in ['alpha','beta']:write(data/db/'final.json',[{'feature_id':'old','dbms':db}])
    published=[];exports=[]
    monkeypatch.setattr(runner,'publish_dataset',lambda db,path,export:published.append(db))
    monkeypatch.setattr(runner,'export_web',lambda:exports.append(True)or {'projection_loss':0})
    return data,artifacts,published,exports


def artifact(root,db,count=1):
    folder=root/db
    write(folder/'result.json',{'dbms':db,'status':'PASS'})
    write(folder/'validation.json',{'dbms':db,'status':'PASS','network_fetch':True,
        'actual_external_llm_api_calls':0,'candidate_final':count,'candidate_review':0})
    write(folder/'summary.json',{'dbms':db,'actual_model_calls':0,'projection_loss':0})
    write(folder/'final.json',[{'dbms':db,'feature_id':db+str(i)}for i in range(count)])
    write(folder/'review.json',[])


def test_one_failure_does_not_discard_other_success_and_export_once(state):
    data,root,published,exports=state
    artifact(root,'alpha')
    result=runner.collect(root)
    assert published==['alpha'] and len(exports)==1
    assert result['updated']==1 and result['preserved']==1
    assert result['results'][1]['old_data_preserved'] is True


def test_removed_database_artifact_is_not_reintroduced(state):
    data,root,published,exports=state
    artifact(root,'hidden')
    result=runner.collect(root)
    assert published==[] and result['preserved']==2


def test_foreign_review_keeps_baseline(state):
    data,root,published,exports=state
    artifact(root,'alpha')
    write(root/'alpha/review.json',[{'record':{'dbms':'beta'}}])
    value=runner.read(root/'alpha/validation.json');value['candidate_review']=1
    write(root/'alpha/validation.json',value)
    assert runner.collect(root)['preserved']==2 and not published


def test_compare_against_newer_main_baseline(state):
    data,root,published,exports=state
    artifact(root,'alpha')
    write(data/'alpha/final.json',[{},{}])
    assert runner.collect(root)['preserved']==2 and not published


def test_duplicate_or_empty_catalog_rejected(state):
    data,*_=state
    write(data/'catalog.json',{'databases':[{'dbms':'alpha'},{'dbms':'alpha'}]})
    with pytest.raises(ValueError):runner.registered()
