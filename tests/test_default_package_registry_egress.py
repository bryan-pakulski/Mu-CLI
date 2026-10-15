"""Default egress allows the common package registries (npm, Cargo/crates.io, Maven/Gradle, PyPI).

Container-mode environments start from ``DEFAULT_EGRESS_ALLOW`` and job stages from the
``sandbox.egress_extra`` default; both share ``PACKAGE_REGISTRY_EGRESS``. Only implement, checks
and template job setup get extra egress; plan and review stay without it, and an explicit empty
list locks jobs down.
"""

from __future__ import annotations

import pytest

from mu.container.network import DEFAULT_EGRESS_ALLOW, PACKAGE_REGISTRY_EGRESS
from mu.jobs.sandbox.policy import SandboxPolicy

REQUIRED = {
    "registry.npmjs.org",                                   # npm
    "crates.io", "index.crates.io", "static.crates.io",     # cargo / crates.io
    "repo.maven.apache.org", "services.gradle.org", "plugins.gradle.org",  # maven / gradle
    "pypi.org", "files.pythonhosted.org",                   # pypi
}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("MUCLI_HOME", str(tmp_path))
    return tmp_path


def test_registry_list_covers_npm_cargo_crates_gradle_maven_and_pypi():
    assert REQUIRED <= set(PACKAGE_REGISTRY_EGRESS)
    assert len(PACKAGE_REGISTRY_EGRESS) == len(set(PACKAGE_REGISTRY_EGRESS))


def test_container_environments_allow_the_registries_by_default():
    assert set(PACKAGE_REGISTRY_EGRESS) <= set(DEFAULT_EGRESS_ALLOW)
    assert len(DEFAULT_EGRESS_ALLOW) == len(set(DEFAULT_EGRESS_ALLOW))


def test_job_implement_and_checks_get_the_registries_by_default(home):
    for stage in ("implement", "checks"):
        assert set(PACKAGE_REGISTRY_EGRESS) <= set(SandboxPolicy.for_stage(stage).egress_extra), stage
    for stage in ("plan", "review"):
        assert SandboxPolicy.for_stage(stage).egress_extra == (), stage


def test_an_explicit_empty_list_locks_jobs_down(home):
    from mu.jobs.job_settings import set_rule
    set_rule("sandbox.egress_extra", [])
    assert SandboxPolicy.for_stage("implement").egress_extra == ()
    assert SandboxPolicy.for_stage("checks", {"sandbox": {"egress_extra": ["pypi.org"]}}).egress_extra == ("pypi.org",)


def test_template_fit_does_not_warn_for_default_registries(home):
    from mu.jobs.sandbox.environments import template_fit
    detected = {"egress": ["registry.npmjs.org", "crates.io", "repo.maven.apache.org", "pypi.org"],
                "toolchains": ["node-npm", "rust", "gradle", "python"]}
    assert template_fit([], detected)["missing_egress"] == []
    assert template_fit([], detected, {"sandbox": {"egress_extra": []}})["missing_egress"] == detected["egress"]
