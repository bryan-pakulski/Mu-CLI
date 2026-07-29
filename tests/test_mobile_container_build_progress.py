from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_mobile_progress_component_toggles_live_output():
    source = read("mobile/android/src/components/ContainerBuildProgress.tsx")
    assert 'onPress={onToggle}' in source
    assert 'Tap to view Docker output' in source
    assert 'Hide Docker output' in source
    assert 'stdout / stderr' in source
    assert 'Waiting for Docker output' in source
    assert 'line.stream === \'stderr\'' in source
    assert 'selectable' in source


def test_mobile_session_creation_accumulates_and_displays_build_logs():
    source = read("mobile/android/src/components/NewSessionSheet.tsx")
    assert 'const [progressLogs, setProgressLogs]' in source
    assert 'setProgressLogs(current => [...current, ...incoming])' in source
    assert 'setProgressExpanded(true)' in source
    assert '<ContainerBuildProgress' in source
    assert 'onToggle={() => setProgressExpanded(current => !current)}' in source


def test_mobile_container_manager_uses_same_progress_disclosure():
    source = read("mobile/android/src/components/ContainerManagerSheet.tsx")
    assert 'const [jobLogs, setJobLogs]' in source
    assert 'setJobLogs(current => [...current, ...incoming])' in source
    assert '<ContainerBuildProgress' in source
    assert 'onToggle={() => setJobExpanded(current => !current)}' in source
