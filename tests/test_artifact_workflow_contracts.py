"""Artifact runtime upgrades preserve same-run native archive contracts."""

import pytest

from tests.test_helper_release import workflow


UPLOAD = "actions/upload-artifact@cf430e030ddbb5b0abf93d22962f4752f3646cd9"
DOWNLOAD = "actions/download-artifact@9000827ccba6bdab643e8b6fd33ac0654aef8333"
TRANSFERS = (
    ("login-helper.yml", "build", UPLOAD, {
        "name": "tdm-login-helper-${{ matrix.platform }}",
        "path": "tdm-login-helper-${{ matrix.platform }}.tar.gz",
        "if-no-files-found": "error", "overwrite": "true",
        "archive": "true", "include-hidden-files": "false",
    }),
    ("login-helper.yml", "prepare-assets", DOWNLOAD, {
        "pattern": "tdm-login-helper-*-*", "path": "helper-artifacts",
        "merge-multiple": "true", "skip-decompress": "false", "digest-mismatch": "error",
    }),
    ("login-helper.yml", "prepare-assets", UPLOAD, {
        "name": "tdm-login-helper-release", "path": "release-assets/",
        "if-no-files-found": "error", "overwrite": "true",
        "archive": "true", "include-hidden-files": "false",
    }),
    ("github-release.yml", "create-github-release", DOWNLOAD, {
        "name": "tdm-login-helper-release", "path": "release-assets",
        "skip-decompress": "false", "digest-mismatch": "error",
    }),
)


@pytest.mark.parametrize("filename,job,reference,inputs", TRANSFERS)
def test_artifact_transfers_pin_node24_releases_and_preserve_packaging(filename, job, reference, inputs):
    family = reference.split("@", 1)[0] + "@"
    steps = workflow(filename)["jobs"][job]["steps"]
    transfer = next(step for step in steps if step.get("uses", "").startswith(family))
    assert transfer["uses"] == reference
    assert transfer["with"] == inputs


def test_all_artifact_transfers_are_covered_without_cross_run_credentials():
    found = []
    for filename in ("login-helper.yml", "github-release.yml"):
        for job_name, job in workflow(filename)["jobs"].items():
            for step in job.get("steps", []):
                if step.get("uses", "").startswith(("actions/upload-artifact@", "actions/download-artifact@")):
                    found.append((filename, job_name, step["uses"].split("@", 1)[0]))
                    assert "env" not in step
                    assert not {"github-token", "repository", "run-id", "artifact-ids"} & step["with"].keys()
    expected = [(filename, job, reference.split("@", 1)[0])
                for filename, job, reference, _ in TRANSFERS]
    assert sorted(found) == sorted(expected)
