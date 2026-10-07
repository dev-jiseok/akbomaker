import copy
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from backend.drum_challenger import METHOD as MODEL_METHOD, sha256
from backend.drum_challenger_review import propose_rejected_hihats


def hit(start, pitch=42):
    return [start, start+.1, pitch, .7]


def candidate(start, pitch=42):
    return {'event':hit(start,pitch),'accepted':False,'votes':1,'pass_ids':[0]}


def test_suggestion_preserves_all_inputs_and_adt_time_subtype():
    original = [hit(.3,36),hit(.6,42)]
    candidates = [candidate(1.,46)]
    independent = [hit(1.025)]
    snapshots = copy.deepcopy((original,candidates,independent))
    result = propose_rejected_hihats(original,candidates,independent)
    assert (original,candidates,independent) == snapshots
    assert result['review_only'] and result['auto_apply'] is False
    assert result['suggestions'][0]['event'] == hit(1.,46)
    assert result['suggestions'][0]['independent_hat_articulation'] == 'unspecified'
    assert 'Country1' in result['warning'] and 'Disco' in result['warning']
    assert 'events' not in result and 'score_events' not in result


def test_no_independent_only_hat_or_kick_to_hat_conversion():
    assert not propose_rejected_hihats([],[],[hit(1.)])['suggestions']
    assert not propose_rejected_hihats([],[candidate(1.,36)],[hit(1.)])['suggestions']


def test_one_independent_hit_cannot_support_multiple_candidates():
    result = propose_rejected_hihats([],[candidate(1.),candidate(1.06,46)],[hit(1.03)])
    assert len(result['suggestions']) == 1


def test_one_candidate_cannot_use_multiple_independent_hits():
    result = propose_rejected_hihats([],[candidate(1.)],[hit(.98),hit(1.02)])
    assert len(result['suggestions']) == 1


def test_existing_accepted_hat_reserves_nearby_independent_hit():
    assert not propose_rejected_hihats([hit(1.)],[candidate(1.08)],[hit(1.04)])['suggestions']


def test_accepted_or_two_vote_candidate_not_rescued():
    first,second = candidate(1.),candidate(2.)
    first['accepted']=True
    second['votes']=2
    assert not propose_rejected_hihats([],[first,second],[hit(1.),hit(2.)])['suggestions']


@pytest.mark.parametrize('distance,expected', [(.05,1),(.05001,0),(-.05,1),(-.05001,0)])
def test_exact_fixed_tolerance(distance,expected):
    assert len(propose_rejected_hihats([],[candidate(1.)],[hit(1.+distance)])['suggestions']) == expected


@pytest.mark.parametrize('ids',[[],[0,1],[3],[True]])
def test_false_one_pass_provenance_rejected(ids):
    c = candidate(1.); c['pass_ids']=ids
    with pytest.raises(ValueError):
        propose_rejected_hihats([],[c],[hit(1.)])


def load_cli():
    spec=importlib.util.spec_from_file_location('review_cli',Path(__file__).resolve().parents[2]/'scripts/review-drum-challenger.py')
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_cli_binds_actual_audio_baseline_and_challenger(tmp_path):
    audio=tmp_path/'audio.wav';audio.write_bytes(b'fixture, not inference audio')
    baseline=tmp_path/'baseline.json';baseline.write_text(json.dumps({'events':[], 'runtime':{'consensus':{'passes':3,'minimum_votes':2,'candidates':[candidate(1.)]}}}))
    contender=tmp_path/'challenger.json';contender.write_text(json.dumps({'method':MODEL_METHOD,'research_only':True,'audio_sha256':sha256(audio),'events':[hit(1.)]}))
    record=tmp_path/'record.json';record.write_text(json.dumps({'name':'test','input_sha256':sha256(audio),'cached_sha256':sha256(baseline)}))
    cli=load_cli()
    result=cli.build_review(audio,baseline,contender,record,'test')
    assert len(result['suggestions'])==1
    assert result['provenance']['baseline_sha256']==sha256(baseline)
    audio.write_bytes(b'different input')
    with pytest.raises(ValueError,match='provenance mismatch'):
        cli.build_review(audio,baseline,contender,record,'test')


def review_fixture(tmp_path):
    audio=tmp_path/'audio.wav';audio.write_bytes(b'audio fixture')
    baseline=tmp_path/'baseline.json';baseline.write_text(json.dumps({'events':[], 'runtime':{'consensus':{'passes':3,'minimum_votes':2,'candidates':[candidate(1.)]}}}))
    contender=tmp_path/'challenger.json';contender.write_text(json.dumps({'method':MODEL_METHOD,'research_only':True,'audio_sha256':sha256(audio),'events':[hit(1.)]}))
    record=tmp_path/'record.json';record.write_text(json.dumps({'name':'test','input_sha256':sha256(audio),'cached_sha256':sha256(baseline)}))
    return audio,baseline,contender,record


@pytest.mark.parametrize('index', [0,1,2,3])
def test_replacement_during_review_fails_closed(tmp_path,monkeypatch,index):
    paths=review_fixture(tmp_path)
    cli=load_cli()
    original_propose=cli.propose_rejected_hihats
    def replacing_propose(*args):
        # Simulate a different complete file replacing one parsed input during
        # expensive analysis; no original user source is changed by this test.
        paths[index].write_bytes(b'{"changed":true}')
        return original_propose(*args)
    monkeypatch.setattr(cli,'propose_rejected_hihats',replacing_propose)
    with pytest.raises(ValueError,match='source changed during execution'):
        cli.build_review(*paths,'test')


@pytest.mark.parametrize('name', ['scripts/review-drum-challenger.py',
                                 'backend/drum_challenger_review.py','backend/drum_challenger.py'])
def test_implementation_replacement_during_review_fails_closed(tmp_path,monkeypatch,name):
    paths=review_fixture(tmp_path)
    cli=load_cli()
    # Operate on fake source snapshots, never mutate the real workspace code.
    fake_root=tmp_path/'source'
    for filename in cli.IMPLEMENTATION_FILES:
        file=fake_root/filename;file.parent.mkdir(parents=True,exist_ok=True);file.write_text('initial implementation')
    monkeypatch.setattr(cli,'ROOT',fake_root)
    original_propose=cli.propose_rejected_hihats
    def replacing_propose(*args):
        (fake_root/name).write_text('different implementation')
        return original_propose(*args)
    monkeypatch.setattr(cli,'propose_rejected_hihats',replacing_propose)
    with pytest.raises(ValueError,match='source changed during execution'):
        cli.build_review(*paths,'test')


def test_json_parse_and_digest_use_same_single_snapshot(tmp_path,monkeypatch):
    cli=load_cli()
    path=tmp_path/'input.json';initial=b'{"version":"initial"}'
    path.write_bytes(initial)
    loads=cli.json.loads
    def replacing_loads(content):
        path.write_bytes(b'{"version":"replacement"}')
        return loads(content)
    monkeypatch.setattr(cli.json,'loads',replacing_loads)
    parsed,digest=cli.json_snapshot(path)
    assert parsed == {'version':'initial'}
    assert digest == hashlib.sha256(initial).hexdigest()
    assert digest != sha256(path)


def test_json_snapshot_read_is_bounded(tmp_path,monkeypatch):
    cli=load_cli();monkeypatch.setattr(cli,'MAX_JSON_BYTES',8)
    path=tmp_path/'oversized.json';path.write_bytes(b'{"too_large":true}')
    with pytest.raises(ValueError,match='size limit'):
        cli.json_snapshot(path)


def test_cli_rechecks_inputs_after_serialization_before_creating_output(tmp_path,monkeypatch):
    paths=review_fixture(tmp_path)
    cli=load_cli();output=tmp_path/'must-not-exist.json'
    argv=['review-drum-challenger.py','--audio',str(paths[0]),'--baseline',str(paths[1]),
          '--challenger',str(paths[2]),'--baseline-run-record',str(paths[3]),'--case','test','--output',str(output)]
    monkeypatch.setattr(cli.sys,'argv',argv)
    dumps=cli.json.dumps
    def replacing_dumps(value,*args,**kwargs):
        serialized=dumps(value,*args,**kwargs)
        paths[2].write_bytes(b'{"changed_after_build":true}')
        return serialized
    monkeypatch.setattr(cli.json,'dumps',replacing_dumps)
    with pytest.raises(ValueError,match='source changed during execution'):
        cli.main()
    assert not output.exists()
