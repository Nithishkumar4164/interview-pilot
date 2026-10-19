from pydantic import BaseModel


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    role: str
    avatar_url: str | None = None

    model_config = {"from_attributes": True}


class GoogleStartResponse(BaseModel):
    authorization_url: str


class GoogleCallbackRequest(BaseModel):
    code: str
    state: str


class AuthSessionResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserResponse
    next: str = "/"


class PairingTokenResponse(BaseModel):
    session_id: str
    pairing_token: str
    expires_in: int
