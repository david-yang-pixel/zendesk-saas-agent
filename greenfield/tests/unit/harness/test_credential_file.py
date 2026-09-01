from __future__ import annotations

from pathlib import Path

from casework_harness.credential_file import CredentialFileEnvironment


def test_credential_file_environment_reads_only_namespaced_values(
    tmp_path: Path,
) -> None:
    path = tmp_path / "credentials.env"
    path.write_text(
        'CASEWORK_CREDENTIAL_OPENAI_PRIMARY="secret-$$value"\n'
        'DATABASE_URL="must-not-cross"\n',
        encoding="utf-8",
    )

    assert CredentialFileEnvironment(path)() == {
        "CASEWORK_CREDENTIAL_OPENAI_PRIMARY": "secret-$value"
    }
