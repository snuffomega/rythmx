from fastapi import APIRouter, Depends

from app.dependencies import verify_api_key

router = APIRouter(dependencies=[Depends(verify_api_key)])


@router.post("/personal-discovery/run")
def personal_discovery_run():
    """Stub endpoint — Personal Discovery engine not yet implemented."""
    return []
