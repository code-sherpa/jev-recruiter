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
    monkeypatch.delenv('TYPESAFE_API_KEY', raising=False)
    monkeypatch.setattr(recruiter, 'Browser', lambda _: pytest.fail('Browser opened'))
    with pytest.raises(ValueError, match='TYPESAFE_API_KEY'):
        recruiter.Recruiter('A field marketer')


class FakeBrowser:
    instances = []

    def __init__(self, url, **viewport):
        self.url = url
        self.viewport = viewport
        self.closed = False
        self.actions = []
        self.instances.append(self)

    def observe(self, screenshot=True):
        if self.url.startswith(recruiter.SEARCH_URL):
            actions = [
                {'id': 'e1', 'kind': 'click', 'label': 'Alice',
                 'context': 'Alice\nField Marketing Manager', 'region': 'main',
                 'href': 'https://www.linkedin.com/in/alice/?tracking=1'},
                {'id': 'e2', 'kind': 'click', 'label': 'Alice duplicate',
                 'context': 'Alice\nField Marketing Manager', 'region': 'main',
                 'href': 'https://www.linkedin.com/in/alice/'},
                {'id': 'e3', 'kind': 'click', 'label': 'Connect',
                 'href': 'https://www.linkedin.com/mynetwork/'},
            ]
            text = 'Alice posted about events'
        else:
            actions = [{'id': 'scroll_down', 'kind': 'scroll', 'delta': 560}]
            text = f'Alice\nRan field events\nProfile screen {len(self.actions)}'
        actions.extend([
            {'id': 'social', 'kind': 'click', 'node': 9, 'role': 'button', 'label': 'Like'},
            {'id': 'fill', 'kind': 'fill', 'node': 10, 'role': 'textbox', 'label': 'Message'},
            {'id': 'select', 'kind': 'select', 'node': 11, 'role': 'combobox', 'label': 'Options'},
            {'id': 'wait', 'kind': 'wait'},
        ])
        for index, action in enumerate(actions):
            if action['kind'] == 'click':
                action.setdefault('node', index + 1)
                action.setdefault('role', 'link')
        return {'url': self.url, 'title': 'LinkedIn', 'text': text, 'actions': actions,
                'screenshot': 'private screenshot'}

    def fresh(self, page, action):
        return True

    def act(self, action, page):
        assert action['kind'] in {'scroll', 'wait'}
        self.actions.append(action)

    def close(self):
        self.closed = True


@pytest.fixture
def prepared(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('TYPESAFE_API_KEY', 'secret test key')
    FakeBrowser.instances = []
    monkeypatch.setattr(recruiter, 'Browser', FakeBrowser)
    monkeypatch.delenv('TEXT_MODEL_API_KEY', raising=False)
    monkeypatch.delenv('TEXT_MODEL', raising=False)

    def choose(page, goal, history):
        assert all(a['kind'] in {'click', 'scroll', 'wait'} for a in page['actions'])
        assert all(a.get('label') != 'Like' for a in page['actions'])
        selected = next(a for a in page['actions'] if a['kind'] != 'wait')
        return decision(selected['id'], 'CLICK' if selected['kind'] == 'click' else 'SCROLL_DOWN')

    def assess(criteria, evidence, url):
        assert criteria == ['[Required] Field events']
        assert url == 'https://www.linkedin.com/in/alice/'
        assert any('Ran field events' in text for text in evidence)
        return {'criteria': [{'criterion': criteria[0], 'status': 'met', 'quote': 'Ran field events'}]}, {
            'model': 'jev-latest', 'usage': {}, 'latency_ms': 1,
        }

    def screen(requirements, profiles):
        return {p['profile_url']: {'status': 'relevant', 'quote': 'Field Marketing Manager',
                                   'confidence': 1, 'model': 'jev-latest'} for p in profiles}, {
            'model': 'jev-latest', 'usage': {}, 'latency_ms': 1}

    monkeypatch.setattr(recruiter, 'screen_profiles', screen)
    monkeypatch.setattr(recruiter, 'choose', choose)
    monkeypatch.setattr(recruiter, 'assess', assess)
    return recruiter.Recruiter('Field events', max_profiles=1)


def decision(choice, operation='DONE'):
    return {'choice': choice, 'operation': operation, 'target': choice,
            'model': 'jev-latest', 'latency_ms': 1, 'confidence': 1, 'usage': {}}


def test_full_bounded_run_and_human_review(prepared):
    run = prepared
    for _ in range(20):
        state = run.command('tick')
        if state['status'] == 'done':
            break
    assert state['status'] == 'done'
    assert state['counts']['discovered'] == 1
    assert state['counts']['model_calls'] == 5
    assert state['candidates'][0]['assessment']['recommendation'] == 'potential_match'
    assert state['candidates'][0]['review'] == 'unreviewed'
    assert len(FakeBrowser.instances) == 2
    assert all(b.viewport == {"width": 2048, "height": 1280} for b in FakeBrowser.instances)
    assert len(FakeBrowser.instances[1].actions) == 2
    assert not FakeBrowser.instances[1].closed
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
    assert state['counts']['model_calls'] == 0


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
    for _ in range(2):
        state = prepared.command('tick')
    assert not state['candidates']
    saved = json.loads(prepared.path.read_text())
    assert saved['discoveries'][0]['profile_url'] == 'https://www.linkedin.com/in/alice/'
    assert saved['discoveries'][0]['discovered_from'] == prepared.search_url
    assert saved['counts']['model_calls'] == 2


def test_profile_budget_is_validated_before_browser(monkeypatch):
    monkeypatch.setenv('TYPESAFE_API_KEY', 'secret')
    monkeypatch.setattr(recruiter, 'Browser', lambda _: pytest.fail('Browser opened'))
    with pytest.raises(ValueError, match='50'):
        recruiter.Recruiter('Field marketer experience', max_profiles=51)


def test_criteria_are_parsed_without_model(prepared):
    assert prepared.criteria == ['[Required] Field events']
    assert prepared.model_calls == 0
    assert not FakeBrowser.instances
    assert recruiter.parse_criteria('Required: Events\n\nPreferred: SaaS') == [
        '[Required] Events', '[Preferred] SaaS',
    ]


def test_stale_profile_link_never_opens(prepared, monkeypatch):
    monkeypatch.setattr(FakeBrowser, 'fresh', lambda *args: False)
    prepared.command('tick')
    state = prepared.command('tick')
    assert state['status'] == 'ready'
    assert state['decision'] is None
    assert 'fresh Jev decision' in state['message']
    assert len(FakeBrowser.instances) == 1
    assert state['discoveries'][0]['profile_url'] == 'https://www.linkedin.com/in/alice/'


def test_stale_scroll_observes_and_asks_jev_again(prepared, monkeypatch):
    prepared.command('tick')
    prepared.command('tick')
    original = FakeBrowser.act
    calls = []

    def changing_page(browser, action, page):
        calls.append(action['id'])
        if len(calls) == 1:
            raise recruiter.StalePage('Changed before input')
        return original(browser, action, page)

    monkeypatch.setattr(FakeBrowser, 'act', changing_page)
    state = prepared.command('tick')
    assert state['status'] == 'ready'
    assert not prepared.profile.actions
    requests = state['counts']['model_calls']
    state = prepared.command('tick')
    assert state['counts']['model_calls'] == requests + 1
    assert len(prepared.profile.actions) == 1
    assert prepared.profile_scrolls == 1


def test_jev_done_does_not_claim_coverage(prepared, monkeypatch):
    monkeypatch.setattr(recruiter, 'choose', lambda *args: decision('DONE'))
    prepared.command('tick')
    state = prepared.command('tick')
    assert state['status'] == 'done'
    assert 'coverage is not guaranteed' in state['message']
    assert not state['candidates']
    assert state['counts']['discovered'] == 1


def test_discovered_link_survives_choose_failure(prepared, monkeypatch):
    def fail(*args):
        raise RuntimeError('provider failed')

    monkeypatch.setattr(recruiter, 'choose', fail)
    prepared.command('tick')
    state = prepared.command('tick')
    assert state['status'] == 'blocked'
    saved = json.loads(prepared.path.read_text())
    assert saved['discoveries'][0]['profile_url'] == 'https://www.linkedin.com/in/alice/'
    assert len(FakeBrowser.instances) == 1


def test_waits_are_capped_at_three(prepared, monkeypatch):
    monkeypatch.setattr(recruiter, 'choose', lambda *args: decision('wait', 'WAIT'))
    for _ in range(10):
        state = prepared.command('tick')
    assert state['status'] == 'blocked'
    assert 'waited three times' in state['error']
    assert len(FakeBrowser.instances[0].actions) == 3
    assert state['counts']['model_calls'] == 4


def test_execution_logged_before_next_observation(prepared, monkeypatch):
    observe = FakeBrowser.observe

    def checked_observe(browser, screenshot=True):
        if browser.actions:
            executed = [item for item in prepared.history if item['action'] == 'Executed Jev browser action']
            assert len(executed) == len(browser.actions)
        return observe(browser, screenshot=screenshot)

    monkeypatch.setattr(FakeBrowser, 'observe', checked_observe)
    for _ in range(10):
        state = prepared.command('tick')
    assert state['status'] == 'done'


def test_relevant_seed_then_sidebar_only_with_hard_title_gate(prepared, monkeypatch):
    prepared.max_profiles = 2
    observed = []

    def card(slug, title, region='main'):
        return {'id': slug, 'kind': 'click', 'label': slug, 'region': region, 'sidebar_section_index': 2,
                'context': f'{slug}\n{title}', 'href': f'https://www.linkedin.com/in/{slug}/'}

    def observe(browser, screenshot=True):
        if browser.url.startswith(recruiter.SEARCH_URL):
            actions = [card('founder', 'Founder'), card('engineer', 'Software Engineer'),
                       card('unknown', ''), card('alice', 'Field Marketing Manager')]
        elif browser.url.endswith('/alice/'):
            actions = [card('main-marketer', 'Field Marketing Manager'),
                       card('sidebar-founder', 'Founder', 'sidebar'),
                       card('similar', 'Field Marketing Manager', 'sidebar')]
        else:
            actions = []
        return {'url': browser.url, 'text': 'Ran field events', 'actions': actions}

    def screen(requirements, profiles):
        observed.extend(p['profile_url'] for p in profiles)
        return {p['profile_url']: {
            'status': 'relevant' if 'Field Marketing Manager' in p['context'] else 'unknown',
            'quote': 'Field Marketing Manager' if 'Field Marketing Manager' in p['context'] else '',
        } for p in profiles}, {'model': 'jev-latest', 'usage': {}, 'latency_ms': 1}

    def assess(criteria, evidence, url):
        return {'criteria': [{'criterion': criteria[0], 'status': 'met', 'quote': 'Ran field events'}]}, {}

    monkeypatch.setattr(FakeBrowser, 'observe', observe)
    monkeypatch.setattr(recruiter, 'screen_profiles', screen)
    monkeypatch.setattr(recruiter, 'assess', assess)
    for _ in range(15):
        state = prepared.command('tick')
        if state['counts']['reviewed'] == 1:
            assert any(d['profile_url'].endswith('/similar/') for d in state['discoveries'])
        if state['status'] == 'done':
            break
    assert state['status'] == 'done'
    assert [c['profile_url'] for c in state['candidates']] == [
        'https://www.linkedin.com/in/alice/', 'https://www.linkedin.com/in/similar/']
    assert [b.url for b in FakeBrowser.instances][1:] == [
        'https://www.linkedin.com/in/alice/', 'https://www.linkedin.com/in/similar/']
    assert 'https://www.linkedin.com/in/main-marketer/' not in observed
    assert state['counts']['discovered'] == 6
    assert state['candidates'][1]['discovered_from'] == 'https://www.linkedin.com/in/alice/'


def test_screening_failure_still_preserves_discovered_url(prepared, monkeypatch):
    def fail(*args):
        saved = json.loads(prepared.path.read_text())
        assert saved['discoveries'][0]['profile_url'] == 'https://www.linkedin.com/in/alice/'
        raise RuntimeError('provider failure')

    monkeypatch.setattr(recruiter, 'screen_profiles', fail)
    prepared.command('tick')
    state = prepared.command('tick')
    assert state['status'] == 'blocked'
    assert len(FakeBrowser.instances) == 1


def test_unquoted_relevance_is_never_opened(prepared, monkeypatch):
    monkeypatch.setattr(recruiter, 'screen_profiles', lambda requirements, profiles: (
        {p['profile_url']: {'status': 'relevant', 'quote': 'Invented role'} for p in profiles}, {}))
    for _ in range(4):
        state = prepared.command('tick')
    assert state['status'] == 'done'
    assert len(FakeBrowser.instances) == 1
    assert not state['candidates']


def test_relevant_links_remain_clickable_after_scroll_budget(prepared):
    prepared.feed_scrolls = prepared.max_scrolls
    prepared.command('tick')
    state = prepared.command('tick')
    assert state['status'] == 'running'
    assert prepared.current['profile_url'] == 'https://www.linkedin.com/in/alice/'
    assert len(FakeBrowser.instances) == 2


def test_screening_cache_reused_after_stale_link(prepared, monkeypatch):
    monkeypatch.setattr(FakeBrowser, 'fresh', lambda *args: False)
    prepared.command('tick')
    prepared.command('tick')
    prepared.command('tick')
    assert sum(h['action'] == 'Requested Jev title relevance screening' for h in prepared.history) == 1
    assert sum(h['action'] == 'Requested Jev browser decision' for h in prepared.history) == 2


def test_duplicate_profile_anchors_offer_one_observed_target_with_title(prepared, monkeypatch):
    prepared.command('tick')
    observed = prepared.feed.observe()
    monkeypatch.setattr(prepared.feed, 'observe', lambda **kwargs: observed)
    original_actions = [dict(a) for a in observed['actions']]

    def choose(page, goal, history):
        clicks = [a for a in page['actions'] if a['kind'] == 'click']
        assert len(clicks) == 1
        assert clicks[0]['id'] == 'e2'
        assert 'Field Marketing Manager' in clicks[0]['label']
        assert clicks[0]['node'] == original_actions[1]['node']
        assert observed['actions'] == original_actions
        return decision(clicks[0]['id'], 'CLICK')

    monkeypatch.setattr(recruiter, 'choose', choose)
    state = prepared.command('tick')
    assert state['status'] == 'running'
    assert prepared.current['profile_url'] == 'https://www.linkedin.com/in/alice/'


@pytest.mark.parametrize("index", [1, 4, None, "2", True])
def test_first_and_unidentified_sidebar_sections_never_open(prepared, monkeypatch, index):
    prepared.command('tick')
    browser = prepared.feed
    prepared.sources = [{"browser": browser, "url": "https://www.linkedin.com/in/source/", "rewind": 0}]
    browser.url = "https://www.linkedin.com/in/source/"
    action = {'id': 'coworker', 'kind': 'click', 'label': 'Relevant coworker',
              'href': 'https://www.linkedin.com/in/coworker/', 'region': 'sidebar',
              'context': 'Field Marketing Manager', 'sidebar_section_index': index}
    monkeypatch.setattr(browser, 'observe', lambda **kwargs: {
        'url': browser.url, 'text': '', 'actions': [action, {'id': 'down', 'kind': 'scroll', 'delta': 560}]})
    monkeypatch.setattr(recruiter, 'screen_profiles', lambda *a: pytest.fail('Excluded section screened'))
    state = prepared.command('tick')
    assert state['status'] == 'running'
    assert browser.actions[-1]['kind'] == 'scroll'
    assert prepared.current is None
    assert len(FakeBrowser.instances) == 1


@pytest.mark.parametrize("index", [2, 3])
def test_allowed_sidebar_section_provenance_saved(prepared, monkeypatch, index):
    prepared.command('tick')
    browser = prepared.feed
    browser.url = "https://www.linkedin.com/in/source/"
    prepared.sources = [{"browser": browser, "url": browser.url, "rewind": 0}]
    action = {'id': 'marketer', 'kind': 'click', 'label': 'Alice',
              'href': 'https://www.linkedin.com/in/alice/', 'region': 'sidebar',
              'context': 'Field Marketing Manager', 'sidebar_section_index': index,
              'sidebar_section_title': 'People you may know'}
    monkeypatch.setattr(browser, 'observe', lambda **kwargs: {'url': browser.url, 'text': '', 'actions': [action]})
    prepared.command('tick')
    assert prepared.current['sidebar_section_index'] == index
    saved = json.loads(prepared.path.read_text())
    assert saved['discoveries'][0]['sidebar_section_index'] == index
    assert saved['discoveries'][0]['sidebar_section_title'] == 'People you may know'


@pytest.mark.parametrize('target', [0, -1, 3, True, '2'])
def test_invalid_match_target(prepared, target):
    with pytest.raises(ValueError, match='target_matches'):
        recruiter.Recruiter('Solutions engineer', max_profiles=2, target_matches=target)


@pytest.mark.parametrize('scrolls', [-1, 21, True, '6'])
def test_invalid_profile_scroll_limit(prepared, scrolls):
    with pytest.raises(ValueError, match='max_profile_scrolls'):
        recruiter.Recruiter('Solutions engineer', max_profile_scrolls=scrolls)


def test_extended_profile_reading_remains_bounded(prepared):
    prepared.max_profile_scrolls = 6
    for _ in range(20):
        state = prepared.command('tick')
        if state['status'] == 'done':
            break
    assert len(FakeBrowser.instances[1].actions) == 6
    assert state['max_profile_scrolls'] == 6
    assert 'At most 7' in state['limitations']


@pytest.mark.parametrize('max_profiles,expected_qualified,target_reached', [(3, 2, True), (2, 1, False)])
def test_match_target_counts_evidence_not_visits_or_shortlists(
        prepared, monkeypatch, max_profiles, expected_qualified, target_reached):
    run = recruiter.Recruiter('Solutions engineer\n3 to 5 years relevant professional experience',
                              max_profiles=max_profiles, target_matches=2)
    slugs = ['unknown-experience', 'first-match', 'second-match']

    def observe(browser, screenshot=True):
        if browser.url.startswith(recruiter.SEARCH_URL):
            actions = [{'id': slug, 'kind': 'click', 'label': slug, 'region': 'main',
                        'context': 'Solutions Engineer', 'href': f'https://www.linkedin.com/in/{slug}/'}
                       for slug in slugs]
        else:
            actions = []
        return {'url': browser.url, 'text': 'Solutions Engineer\n4 years relevant professional experience',
                'actions': actions}

    def screen(requirements, profiles):
        return {p['profile_url']: {'status': 'relevant', 'quote': 'Solutions Engineer'} for p in profiles}, {}

    def choose(page, goal, history):
        assert 'field or event marketing' not in goal
        assert 'Never open unrelated founders or engineers' not in goal
        return decision(page['actions'][0]['id'], 'CLICK')

    def assess(criteria, evidence, url):
        return {'criteria': [
            {'criterion': criteria[0], 'status': 'met', 'quote': 'Solutions Engineer'},
            {'criterion': criteria[1], 'status': 'unknown' if 'unknown-experience' in url else 'met',
             'quote': '' if 'unknown-experience' in url else '4 years relevant professional experience'},
        ]}, {}

    monkeypatch.setattr(FakeBrowser, 'observe', observe)
    monkeypatch.setattr(recruiter, 'screen_profiles', screen)
    monkeypatch.setattr(recruiter, 'choose', choose)
    monkeypatch.setattr(recruiter, 'assess', assess)
    for _ in range(30):
        state = run.command('tick')
        if state['candidates']:
            run.command('review', {'profile_url': state['candidates'][0]['profile_url'], 'decision': 'shortlisted'})
        if state['status'] == 'done':
            break
    assert state['status'] == 'done'
    assert state['counts']['qualified'] == expected_qualified
    assert state['counts']['reviewed'] == max_profiles
    assert state['target_reached'] is target_reached
    assert state['target_matches'] == 2
    assert state['max_model_calls'] == 1000
    assert ('target was not reached' in state['message']) is not target_reached
    assert state['candidates'][0]['assessment']['recommendation'] == 'needs_review'
    calls = state['counts']['model_calls']
    assert run.command('tick')['counts']['model_calls'] == calls
    assert json.loads(run.path.read_text())['target_reached'] is target_reached
