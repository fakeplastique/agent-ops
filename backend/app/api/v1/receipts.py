import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel

from app.api.deps import ActorDep, EngineDep, public_limit
from app.config import settings
from app.db.session import tenant_session
from app.domain.receipt import KeyRing
from app.services import receipts

router = APIRouter(tags=["receipts"])


class ReceiptOut(BaseModel):
    alg: str
    kid: str
    payload: dict[str, Any]
    signature: str


class VerifyOut(BaseModel):
    valid: bool


@router.get("/decision-requests/{decision_request_id}/receipt", response_model=ReceiptOut)
async def get_receipt(
    decision_request_id: uuid.UUID, actor: ActorDep, engine: EngineDep
) -> ReceiptOut:
    """The signed receipt of an approved request; 404 for any other status."""
    async with tenant_session(engine, actor.organization_id) as s:
        found = await receipts.get_receipt(s, actor.organization_id, decision_request_id)
    return ReceiptOut(**found)


@router.post("/receipts/verify", response_model=VerifyOut, dependencies=[Depends(public_limit)])
async def verify(receipt: Annotated[dict[str, Any], Body()], engine: EngineDep) -> VerifyOut:
    """Anyone may check a receipt they were handed; the answer reveals nothing but valid or not."""
    keys = KeyRing.parse(settings.receipt_keys)
    return VerifyOut(valid=await receipts.verify_receipt(engine, keys, receipt))
