from pydantic import BaseModel, EmailStr


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class TokenRefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: str
    email: str
    name: str
    role: str
    avatar_url: str | None = None

    model_config = {"from_attributes": True}


class GoogleAuthRequest(BaseModel):
    code: str
    redirect_uri: str
