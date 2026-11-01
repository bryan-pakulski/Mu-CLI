"""Tool identities survive real agent turns, history projection and local replay."""
import copy
import json

import pytest

from mode_helpers import agent_session
from mu.session.messages import build_messages_from_history
from providers.base import LLMProvider, Message, MessagePart
from providers.local import LocalOpenAIProvider


def converter():
    provider = LocalOpenAIProvider.__new__(LocalOpenAIProvider)
    LLMProvider.__init__(provider, 'glm-5.3-flash')
    return provider


def project(history, **kwargs):
    return build_messages_from_history(history, {'role': 'user', 'parts': []}, **kwargs)[:-1]


def call(name='read_file', cid=None):
    return {'role': 'assistant', 'parts': [{'type': 'tool_call', 'tool_name': name,
            'tool_args': {}, **({'tool_call_id': cid} if cid else {})}]}


def result(name='read_file', cid=None):
    return {'role': 'tool', 'parts': [{'type': 'tool_result', 'tool_name': name,
            'tool_result': 'file contents', 'cache_key': 'saved-result',
            **({'tool_call_id': cid} if cid else {})}]}


def assert_pairs(wire):
    calls = {}
    answered = set()
    for message in wire:
        for c in message.get('tool_calls', []):
            assert c['id'] not in calls
            calls[c['id']] = c
        if message['role'] == 'tool':
            cid = message['tool_call_id']
            assert cid in calls and cid not in answered
            answered.add(cid)
    assert set(calls) == answered


def test_real_agent_loop_preserves_ids_for_parallel_same_name_tools_and_rehydration(tmp_path):
    ws = tmp_path/'ws'
    ws.mkdir()
    for name in ('a.txt', 'b.txt'):
        (ws/name).write_text(name)
    session = agent_session(tmp_path, workspace=ws, name='id-roundtrip', yolo=False,
                            steps=[[('read_file', {'filename': str(ws/n)}) for n in ('a.txt', 'b.txt')], 'done'])
    session.send_message('Read both files.')
    assert len(session.provider.calls) == 2
    follow = session.provider.calls[1]['messages']
    calls = [p for m in follow for p in m.parts if p.type == 'tool_call']
    results = [p for m in follow for p in m.parts if p.type == 'tool_result']
    assert [p.tool_call_id for p in calls] == ['c1', 'c2']
    assert [p.tool_call_id for p in results] == ['c1', 'c2']
    assert_pairs(converter()._convert_messages(follow))
    saved = json.loads(json.dumps(session.session_manager.history))
    assert [p['tool_call_id'] for m in saved for p in m['parts'] if p['type'] == 'tool_call'] == ['c1', 'c2']
    assert_pairs(converter()._convert_messages(project(saved)))


@pytest.mark.parametrize('options', [dict(auto_clear=False), dict(auto_clear=True),
                                   dict(auto_clear=True, tool_result_floor=1)])
def test_projection_preserves_ids_with_full_or_compacted_results_and_stubbed_args(options):
    history = [call(cid='provider-call'), result(cid='provider-call')]
    history[0]['parts'][0]['tool_args'] = {'content': 'long argument '*100}
    before = copy.deepcopy(history)
    messages = project(history, arg_stub_threshold=20, **options)
    assert messages[0].parts[0].tool_call_id == messages[1].parts[0].tool_call_id == 'provider-call'
    assert messages[0].parts[0].tool_args['content']['__stubbed__']
    assert history == before
    assert_pairs(converter()._convert_messages(messages))


def test_missing_ids_are_paired_in_projection_without_rewriting_saved_history():
    history = [call('get_workspace_details'), result('get_workspace_details'),
               call('get_workspace_details'), result('get_workspace_details')]
    before = copy.deepcopy(history)
    provider = converter()
    wire = provider._convert_messages(project(history))
    assert_pairs(wire)
    assert wire[0]['tool_calls'][0]['id'] != wire[2]['tool_calls'][0]['id']
    assert provider._convert_messages(project(history)) == wire
    extended = provider._convert_messages(project(history + [call(cid='next-id'), result(cid='next-id')]))
    assert extended[:len(wire)] == wire
    assert history == before


def test_explicit_ids_support_reordered_same_name_results_and_are_never_guessed():
    history = [call(cid='a')]
    history[0]['parts'].extend(call(cid='b')['parts'])
    history += [result(cid='b'), result(cid='a')]
    wire = converter()._convert_messages(project(history))
    assert_pairs(wire)
    assert [m['tool_call_id'] for m in wire[1:]] == ['b', 'a']
    history[-1]['parts'][0]['tool_call_id'] = 'wrong-explicit-id'
    assert converter()._convert_messages(project(history))[-1]['tool_call_id'] == 'wrong-explicit-id'


def test_missing_result_id_never_guesses_between_same_name_calls_or_orphans():
    ambiguous = [call(), result()]
    ambiguous[0]['parts'].extend(call()['parts'])
    for history in [ambiguous, [result()]]:
        with pytest.raises(ValueError, match='Cannot pair a tool result without an ID'):
            converter()._convert_messages(project(history))


def test_generated_ids_do_not_collide_with_explicit_ids():
    history = [call(), result(), call(cid='call_mucli_0_0'), result(cid='call_mucli_0_0')]
    wire = converter()._convert_messages(project(history))
    assert_pairs(wire)
    assert wire[2]['tool_calls'][0]['id'] == 'call_mucli_0_0'
