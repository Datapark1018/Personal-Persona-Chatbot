"""Validated persona input and an explicit public presentation boundary."""
import json
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator, model_validator

ROOT = Path(__file__).resolve().parents[1]
Short = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]
Alias = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=60)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Document(StrictModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{0,63}$")
    title: Short
    category: Literal["about", "career", "skills", "project", "contact", "other"]
    visibility: Literal["public", "private"]
    summary: str = Field(min_length=1, max_length=300)
    text: str = Field(min_length=1, max_length=2000)
    tags: list[Alias] = Field(default_factory=list, max_length=20)
    aliases: list[Alias] = Field(default_factory=list, max_length=10)
    url: str | None = Field(default=None, max_length=2048)

    @field_validator("summary", "text")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("must not be blank")
        return value.strip()

    @field_validator("url")
    @classmethod
    def safe_url(cls, value):
        if value is None:
            return value
        # Internal anchors are generated from id, not accepted from the input.
        parsed = urlsplit(value)
        _ = parsed.port  # Reject malformed/out-of-range ports as well.
        if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password
                or any(c.isspace() or ord(c) < 32 for c in value) or "\\" in value):
            raise ValueError("url must be an absolute HTTPS URL without credentials")
        return value


class Persona(StrictModel):
    schema_version: Literal[1] = 1
    fictional: bool
    name: Short
    english_name: Short
    role: Short
    location: Short
    headline: str = Field(min_length=1, max_length=150)
    bio: str = Field(min_length=1, max_length=400)
    aliases: list[Alias] = Field(default_factory=list, max_length=10)
    tone: str = Field(default="차분하고 구체적으로, 간결하게", min_length=1, max_length=200)
    response_mode: Literal["assistant", "persona"] = "assistant"
    suggested_questions: list[Short] = Field(min_length=1, max_length=5)
    documents: list[Document] = Field(min_length=1, max_length=50)

    @model_validator(mode="after")
    def unique_documents(self):
        ids = [doc.id for doc in self.documents]
        if len(ids) != len(set(ids)):
            raise ValueError("document ids must be unique")
        public = [doc for doc in self.documents if doc.visibility == "public"]
        if not public:
            raise ValueError("at least one public document is required")
        # Ambiguous project aliases would silently select unrelated sources.
        owners = {}
        for doc in public:
            if doc.category == "project":
                for alias in {doc.id.casefold(), doc.title.casefold(), *(a.casefold() for a in doc.aliases)}:
                    if alias in owners and owners[alias] != doc.id:
                        raise ValueError("project aliases must be unique")
                    owners[alias] = doc.id
        return self

    def public_documents(self):
        return [doc.model_dump() for doc in self.documents if doc.visibility == "public"]

    def presentation(self):
        # Never serialize the raw persona to a public endpoint.
        return {
            **self.model_dump(include={"name", "english_name", "role", "location", "headline", "bio", "fictional", "response_mode", "suggested_questions"}),
            "sections": [{"id": d.id, "title": d.title, "category": d.category,
                          "summary": d.summary, "tags": d.tags, "url": d.url}
                         for d in self.documents if d.visibility == "public"],
        }


def load_persona(path: Path) -> Persona:
    try:
        if path.stat().st_size > 512 * 1024:
            raise ValueError("file exceeds 512 KiB")
        return Persona.model_validate_json(path.read_text(encoding="utf-8-sig"))
    except ValidationError as exc:
        # Pydantic's default error string may include private input values.
        locations = ", ".join(".".join(map(str, error["loc"])) or "root" for error in exc.errors())
        raise ValueError(f"Invalid persona configuration at: {locations}. Run python -m server.validate_persona.") from None
    except (OSError, UnicodeError, ValueError):
        raise ValueError("Cannot load persona: check the path, UTF-8 JSON, and 512 KiB limit.") from None


def write_schema(path: Path):
    path.write_text(json.dumps(Persona.model_json_schema(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
