"""Adversarial and security tests for Stage 3.6:
- Artifact staging vs worker-owned final publication boundaries.
- Symlink substitution, path traversal, checksum/size validation.
- Generation fencing and publication atomicity.
- Cross-tenant artifact protection.
"""

from __future__ import annotations

import hashlib
import io
import os
import uuid
from pathlib import Path

import pypdf
import pytest

from app.analytics.exceptions import DatasetTooLargeError
from app.core.exceptions import ValidationError
from app.services.report_renderer import ReportRendererService
from app.services.report_storage import ReportStorageService


def test_storage_path_traversal_rejected(tmp_path: Path):
    storage = ReportStorageService(base_dir=tmp_path / "reports")
    org_id = uuid.uuid4()
    run_id = uuid.uuid4()

    # Traversal in filename
    with pytest.raises(ValidationError, match="Path traversal attempt detected"):
        storage.resolve_safe_run_path(org_id, run_id, "../escape.pdf")

    with pytest.raises(ValidationError, match="Path traversal attempt detected"):
        storage.resolve_safe_run_path(org_id, run_id, "sub/dir.pdf")

    with pytest.raises(ValidationError, match="Path traversal attempt detected"):
        storage.resolve_safe_run_path(org_id, run_id, "C:\\windows\\system32.pdf")


def test_storage_symlink_escape_rejected(tmp_path: Path):
    storage = ReportStorageService(base_dir=tmp_path / "reports")
    org_id = uuid.uuid4()
    run_id = uuid.uuid4()

    outside_file = tmp_path / "sensitive.txt"
    outside_file.write_text("SENSITIVE_DATA")

    run_dir = storage.runs_dir / str(org_id) / str(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    symlink_file = run_dir / "report_gen_1.pdf"

    try:
        os.symlink(outside_file, symlink_file)
    except OSError:
        pytest.skip("Symlink creation not permitted in this OS environment")

    # Storage read must detect and reject symlink escape
    with pytest.raises(
        ValidationError, match="(Path traversal attempt detected|Symlink escape attempt detected)"
    ):
        storage.read_and_verify_bytes(symlink_file)


def test_storage_checksum_mismatch_rejected(tmp_path: Path):
    storage = ReportStorageService(base_dir=tmp_path / "reports")
    org_id = uuid.uuid4()
    run_id = uuid.uuid4()

    valid_bytes = b"%PDF-1.4 test valid content"
    pdf_path = storage.get_pdf_artifact_path(org_id, run_id, claim_generation=1)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(valid_bytes)

    wrong_checksum = "0000000000000000000000000000000000000000000000000000000000000000"
    with pytest.raises(ValidationError, match="Artifact checksum mismatch"):
        storage.read_and_verify_bytes(pdf_path, expected_checksum=wrong_checksum)


def test_storage_size_limit_exceeded_rejected(tmp_path: Path):
    storage = ReportStorageService(base_dir=tmp_path / "reports")
    org_id = uuid.uuid4()
    run_id = uuid.uuid4()

    large_bytes = b"X" * 1024
    pdf_path = storage.get_pdf_artifact_path(org_id, run_id, claim_generation=1)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    pdf_path.write_bytes(large_bytes)

    with pytest.raises(DatasetTooLargeError, match="exceeds maximum allowable limit"):
        storage.read_and_verify_bytes(pdf_path, max_allowed_bytes=512)


def test_worker_independent_pdf_validation():
    renderer = ReportRendererService()

    # Empty bytes rejected
    with pytest.raises(ValidationError, match="Rendered PDF artifact is empty"):
        renderer.verify_and_measure_pdf(b"")

    # Corrupt / non-PDF bytes rejected
    with pytest.raises(ValidationError, match="structural integrity verification"):
        renderer.verify_and_measure_pdf(b"NOT A REAL PDF FILE")

    # Valid PDF accepted
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=612, height=792)
    stream = io.BytesIO()
    writer.write(stream)
    valid_pdf_bytes = stream.getvalue()

    pages, checksum, size = renderer.verify_and_measure_pdf(valid_pdf_bytes)
    assert pages == 1
    assert checksum == hashlib.sha256(valid_pdf_bytes).hexdigest()
    assert size == len(valid_pdf_bytes)


def test_atomic_promotion_and_generation_cleanup(tmp_path: Path):
    storage = ReportStorageService(base_dir=tmp_path / "reports")
    org_id = uuid.uuid4()
    run_id = uuid.uuid4()

    # Gen 1 stages and promotes
    staging_1 = storage.get_pdf_staging_path(org_id, run_id, claim_generation=1)
    final_1 = storage.get_pdf_artifact_path(org_id, run_id, claim_generation=1)
    staging_1.parent.mkdir(parents=True, exist_ok=True)
    staging_1.write_bytes(b"%PDF-gen1")
    storage.atomic_promote(staging_1, final_1)
    assert final_1.is_file()
    assert not staging_1.is_file()

    # Gen 2 stages
    staging_2 = storage.get_pdf_staging_path(org_id, run_id, claim_generation=2)
    final_2 = storage.get_pdf_artifact_path(org_id, run_id, claim_generation=2)
    staging_2.write_bytes(b"%PDF-gen2")

    # Gen 1 cleanup is invoked (e.g. after Gen 2 takes over)
    storage.cleanup_generation_artifacts(org_id, run_id, claim_generation=1)

    # Gen 1 final artifact deleted, but Gen 2 staging remains intact
    assert not final_1.is_file()
    assert staging_2.is_file()

    # Gen 2 promotes successfully
    storage.atomic_promote(staging_2, final_2)
    assert final_2.is_file()
    assert final_2.read_bytes() == b"%PDF-gen2"


def test_cross_tenant_staging_isolation(tmp_path: Path):
    storage = ReportStorageService(base_dir=tmp_path / "reports")
    org_a = uuid.uuid4()
    org_b = uuid.uuid4()
    run_id = uuid.uuid4()

    staging_a = storage.get_pdf_staging_path(org_a, run_id, claim_generation=1)
    staging_b = storage.get_pdf_staging_path(org_b, run_id, claim_generation=1)

    assert str(org_a) in str(staging_a)
    assert str(org_b) in str(staging_b)
    assert str(org_a) not in str(staging_b)
