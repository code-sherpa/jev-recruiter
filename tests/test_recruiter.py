import json

import pytest

from jev_ultrafast import recruiter


@pytest.mark.parametrize('url', [
    'http://www.linkedin.com/in/alice/', 'https://linkedin.com/in/alice/',
    'https://www.linkedin.com.evil.test/in/alice/', 'https://www.linkedin.com/in/alice/posts/',
    'https://www.linkedin.com/in/a%2fb/', 'https://user@www.linkedin.com/in/alice/',
    'https://www.linkedin.com/in/a%0a/', None,
])
def test_reject_untrusted_navigation(url):
    assert recruiter.profile_url(url) is None


def test_normalize_observed_profile():
    assert recruiter.profile_url('https://www.linkedin.com/in/alice?tracking=yes#x') == (
        'https://www.linkedin.com/in/alice/'
    )


def test_unsupported_claim_becomes_unknown():
    result = recruiter.validate_assessment(
        {'criteria': [{'criterion': 'Events', 'status': 'met', 'quote': 'Invented experience'}]},
        ['Events'], ['Unrelated text'],
    )
    assert result['recommendation'] == 'needs_review'
    assert result['criteria'][0]['status'] == 'unknown'


def test_missing_criterion_rejected():
    with pytest.raises(ValueError, match='every job criterion'):
        recruiter.validate_assessment({'criteria': []}, ['Events'], ['Events'])


def test_preference_does_not_disqualify():
    result = recruiter.validate_assessment({'criteria': [
        {'criterion': '[Required] Events', 'status': 'met', 'quote': 'Ran events'},
        {'criterion': '[Preferred] SaaS', 'status': 'not_met', 'quote': 'No SaaS experience'},
    ]}, ['[Required] Events', '[Preferred] SaaS'], ['Ran events. No SaaS experience'])
    assert result['recommendation'] == 'potential_match'


def test_missing_key_does_not_open_browser(monkeypatch):
    monkeypatch.delenv('TEXT_MODEL_API_KEY', raising=False)
    monkeypatch.setattr(recruiter, 'Browser', lambda _: pytest.fail('Browser opened'))
    with pytest.raises(ValueError, match='TEXT_MODEL_API_KEY'):
        recruiter.Recruiter('A field marketer')


class FakeBrowser:
    instances = []

    def __init__(self, url):
        self.url = url
        self.closed = False
        self.actions = []
        self.instances.append(self)

    def observe(self, screenshot=True):
        if self.url == recruiter.FEED_URL:
            actions = [
                {'id': 'e1', 'kind': 'click', 'label': 'Alice',
                 'href': 'https://www.linkedin.com/in/alice/?tracking=1'},
                {'id': 'e2', 'kind': 'click', 'label': 'Alice duplicate',
                 'href': 'https://www.linkedin.com/in/alice/'},
                {'id': 'e3', 'kind': 'click', 'label': 'Connect',
                 'href': 'https://www.linkedin.com/mynetwork/'},
            ]
            text = 'Alice posted about events'
        else:
            actions = [{'id': 'scroll_down', 'kind': 'scroll', 'delta': 560}]
            text = 'Alice\nRan field events'
        return {'url': self.url, 'title': 'LinkedIn', 'text': text, 'actions': actions,
                'screenshot': 'private screenshot'}

    def act(self, action, page):
        assert action['kind'] == 'scroll'
        self.actions.append(action)

    def close(self):
        self.closed = True


@pytest.fixture
def prepared(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('TEXT_MODEL_API_KEY', 'secret test key')
    FakeBrowser.instances = []
    monkeypatch.setattr(recruiter, 'Browser', FakeBrowser)
    responses = iter([
        {'criteria': ['[Required] Field events']},
        {'criteria': [{'criterion': '[Required] Field events', 'status': 'met', 'quote': 'Ran field events'}]},
    ])

    def post(*args):
        return {'choices': [{'message': {'content': json.dumps(next(responses))}}]}

    monkeypatch.setattr(recruiter, 'post_json', post)
    return recruiter.Recruiter('Must have field event experience', max_profiles=1)


def test_full_bounded_run_and_human_review(prepared):
    run = prepared
    for _ in range(20):
        state = run.command('tick')
        if state['status'] == 'done':
            break
    assert state['status'] == 'done'
    assert state['counts']['discovered'] == 1
    assert state['counts']['model_calls'] == 2
    assert state['candidates'][0]['assessment']['recommendation'] == 'potential_match'
    assert state['candidates'][0]['review'] == 'unreviewed'
    assert len(FakeBrowser.instances) == 2
    assert len(FakeBrowser.instances[1].actions) == 2
    assert FakeBrowser.instances[1].closed
    saved = run.path.read_text()
    assert 'private screenshot' not in saved
    assert 'secret test key' not in saved
    state = run.command('review', {'profile_url': state['candidates'][0]['profile_url'], 'decision': 'shortlisted'})
    assert state['candidates'][0]['review'] == 'shortlisted'
    run.close()
    assert FakeBrowser.instances[0].closed


def test_login_blocks_without_reading_profiles(prepared, monkeypatch):
    monkeypatch.setattr(FakeBrowser, 'observe', lambda *a, **kw: {
        'url': 'https://www.linkedin.com/checkpoint/challenge', 'title': 'Challenge', 'text': '', 'actions': [],
    })
    prepared.command('tick')
    state = prepared.command('tick')
    assert state['status'] == 'blocked'
    assert 'manually' in state['error']
    assert state['counts']['model_calls'] == 1


def test_scroll_failure_is_not_retried(prepared, monkeypatch):
    calls = []

    def fail(*args):
        calls.append(1)
        raise RuntimeError('uncertain mutation')

    monkeypatch.setattr(FakeBrowser, 'act', fail)
    for _ in range(12):
        prepared.command('tick')
    assert prepared.status == 'blocked'
    assert len(calls) == 1


def test_discovered_link_is_saved_before_profile_assessment(prepared):
    for _ in range(3):
        state = prepared.command('tick')
    assert not state['candidates']
    saved = json.loads(prepared.path.read_text())
    assert saved['discoveries'][0]['profile_url'] == 'https://www.linkedin.com/in/alice/'
    assert saved['discoveries'][0]['discovered_from'] == recruiter.FEED_URL
    assert saved['counts']['model_calls'] == 1


def test_profile_budget_is_validated_before_browser(monkeypatch):
    monkeypatch.setenv('TEXT_MODEL_API_KEY', 'secret')
    monkeypatch.setattr(recruiter, 'Browser', lambda _: pytest.fail('Browser opened'))
    with pytest.raises(ValueError, match='50'):
        recruiter.Recruiter('Field marketer experience', max_profiles=51)
