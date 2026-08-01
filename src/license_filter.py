"""Deterministic SPDX and OSI license classification."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class LicenseStatus(StrEnum):
    """Outcome of the OSS license filter."""

    PASS = "pass"
    EXCLUDE_NON_OSI = "exclude_non_osi"
    REVIEW_UNRECOGNIZED = "review_unrecognized"
    NOT_EVALUATED = "not_evaluated"


@dataclass(frozen=True, slots=True)
class LicenseResult:
    """SPDX mapping and OSI approval result."""

    status: LicenseStatus
    spdx_id: str = ""
    reason: str = ""


class LicenseChecker(Protocol):
    """Classify a repository license label."""

    def check(self, license_name: str) -> LicenseResult:
        """Map one GitHub license label to an SPDX and OSI result."""
        ...


@dataclass(frozen=True, slots=True)
class _LicenseMetadata:
    spdx_id: str
    is_osi_approved: bool


# Derived from SPDX License List 3.28.0 for labels present in the input dataset.
_LICENSES = {
    "Apache License 2.0": _LicenseMetadata("Apache-2.0", True),
    "BSD 2-Clause Simplified License": _LicenseMetadata("BSD-2-Clause", True),
    "BSD 3-Clause Clear License": _LicenseMetadata("BSD-3-Clause-Clear", False),
    "BSD 3-Clause New or Revised License": _LicenseMetadata("BSD-3-Clause", True),
    "BSD Zero Clause License": _LicenseMetadata("0BSD", True),
    "Creative Commons Attribution 4.0 International": _LicenseMetadata("CC-BY-4.0", False),
    "Creative Commons Attribution Share Alike 4.0 International": _LicenseMetadata("CC-BY-SA-4.0", False),
    "Creative Commons Zero v1.0 Universal": _LicenseMetadata("CC0-1.0", False),
    "Do What The F*ck You Want To Public License": _LicenseMetadata("WTFPL", False),
    "Eclipse Public License 1.0": _LicenseMetadata("EPL-1.0", True),
    "Eclipse Public License 2.0": _LicenseMetadata("EPL-2.0", True),
    "European Union Public License 1.2": _LicenseMetadata("EUPL-1.2", True),
    "GNU Affero General Public License v3.0": _LicenseMetadata("AGPL-3.0", True),
    "GNU General Public License v2.0": _LicenseMetadata("GPL-2.0", True),
    "GNU General Public License v3.0": _LicenseMetadata("GPL-3.0", True),
    "GNU Lesser General Public License v2.1": _LicenseMetadata("LGPL-2.1", True),
    "GNU Lesser General Public License v3.0": _LicenseMetadata("LGPL-3.0", True),
    "ISC License": _LicenseMetadata("ISC", True),
    "MIT License": _LicenseMetadata("MIT", True),
    "MIT No Attribution": _LicenseMetadata("MIT-0", True),
    "Mozilla Public License 2.0": _LicenseMetadata("MPL-2.0", True),
    "Mulan Permissive Software License, Version 2": _LicenseMetadata("MulanPSL-2.0", True),
    "Open Data Commons Open Database License v1.0": _LicenseMetadata("ODbL-1.0", False),
    "Open Software License 3.0": _LicenseMetadata("OSL-3.0", True),
    "SIL Open Font License 1.1": _LicenseMetadata("OFL-1.1", True),
    "The Unlicense": _LicenseMetadata("Unlicense", True),
    "zlib License": _LicenseMetadata("Zlib", True),
}


class SpdxLicenseChecker:
    """Apply the pinned SPDX 3.28.0 OSI approval criterion."""

    __slots__ = ()

    def check(self, license_name: str) -> LicenseResult:
        """Return a high-precision OSI-only classification."""
        metadata = _LICENSES.get(license_name)
        if metadata is None:
            return LicenseResult(
                status=LicenseStatus.REVIEW_UNRECOGNIZED,
                reason="GitHub license label is Other, empty, or unrecognized",
            )
        if not metadata.is_osi_approved:
            return LicenseResult(
                status=LicenseStatus.EXCLUDE_NON_OSI,
                spdx_id=metadata.spdx_id,
                reason="SPDX license without OSI approval",
            )
        return LicenseResult(
            status=LicenseStatus.PASS,
            spdx_id=metadata.spdx_id,
            reason="SPDX OSI Approved",
        )
