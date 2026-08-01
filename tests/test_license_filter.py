"""Tests for deterministic OSS license filtering."""

import license_filter


def test_license_checker_accepts_osi_approved_spdx_license() -> None:
    result = license_filter.SpdxLicenseChecker().check("Apache License 2.0")

    assert result.status is license_filter.LicenseStatus.PASS
    assert result.spdx_id == "Apache-2.0"


def test_license_checker_excludes_spdx_license_without_osi_approval() -> None:
    result = license_filter.SpdxLicenseChecker().check("Creative Commons Attribution 4.0 International")

    assert result.status is license_filter.LicenseStatus.EXCLUDE_NON_OSI
    assert result.spdx_id == "CC-BY-4.0"


def test_license_checker_reviews_unknown_license() -> None:
    result = license_filter.SpdxLicenseChecker().check("Other")

    assert result.status is license_filter.LicenseStatus.REVIEW_UNRECOGNIZED
    assert result.spdx_id == ""
