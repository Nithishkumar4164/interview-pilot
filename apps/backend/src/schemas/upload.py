from pydantic import BaseModel


class UploadResponse(BaseModel):
    reference_id: str
    filename: str
    file_type: str
    size_bytes: int


class ExtractionStatusResponse(BaseModel):
    reference_id: str
    status: str  # pending | processing | completed | failed
    structured_data: dict | None = None
    error: str | None = None
