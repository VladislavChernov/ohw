from enum import StrEnum
from typing import ClassVar

from pydantic import BaseModel, Field


class CaseType(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


class ArtifactType(StrEnum):
    TESTCASES = "testcases"
    CHECKLIST = "checklist"
    TESTPLAN = "testplan"


class TestCase(BaseModel):
    __test__: ClassVar[bool] = False

    id: str = Field(min_length=1)
    type: CaseType
    title: str = Field(min_length=1)
    preconditions: list[str] = Field(default_factory=list)
    steps: list[str] = Field(min_length=1)
    expected: str = Field(min_length=1)
    requirement: str | None = None


class ValidationResult(BaseModel):
    cases: list[TestCase]
    issues: list[str]

    @property
    def ok(self) -> bool:
        return not self.issues


class GenerationResult(BaseModel):
    __test__: ClassVar[bool] = False

    cases: list[TestCase] = Field(default_factory=list)
    document: str | None = None
    issues: list[str] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.issues
