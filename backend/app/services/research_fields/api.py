"""Workspace-authorized API; shared reports never contain private paper material."""
import asyncio
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from app.infrastructure import llm
from app.services.papers.workspaces import WorkspaceNotFound
from app.services.research_fields import store
router=APIRouter()

async def call(function,*args):
    try:return await asyncio.to_thread(function,*args)
    except (WorkspaceNotFound,ValueError) as exc:raise HTTPException(404,str(exc)) from None

@router.get('/api/workspaces/{workspace_id}/fields')
async def fields(workspace_id:str):
    return await call(store.workspace_fields,workspace_id)

@router.post('/api/workspaces/{workspace_id}/fields/identify')
async def identify(workspace_id:str):
    return {'queued':await call(store.queue_legacy,workspace_id)}

class Analyze(BaseModel):
    refresh:bool=False

@router.get('/api/workspaces/{workspace_id}/fields/{field_id}/report')
async def report(workspace_id:str,field_id:str):
    await call(store.require_membership,workspace_id,field_id)
    return await call(store.report,field_id)

@router.post('/api/workspaces/{workspace_id}/fields/{field_id}/analyze')
async def analyze(workspace_id:str,field_id:str,payload:Analyze):
    await call(store.require_membership,workspace_id,field_id)
    created=await call(store.request_report,field_id,llm.model_name(),payload.refresh)
    return {**(await call(store.report,field_id)),'created':created}
