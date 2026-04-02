"""Auth dependencies for FastAPI endpoints."""

from typing import Annotated

from boldsci.auth import AuthContext
from fastapi import Depends

from src.auth.middleware import auth_middleware

Auth = Annotated[AuthContext, Depends(auth_middleware)]
