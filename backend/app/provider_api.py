"""Write-only provider credentials scoped to the current browser session."""
from urllib.parse import urlsplit
import os
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel,Field
from app.infrastructure import config
from app.infrastructure.db import connection
from app.infrastructure.user_scope import enabled,visitor

router=APIRouter(prefix='/api/providers')
FIELDS={
 'llm_key':'DASHSCOPE_API_KEY','llm_base':'DASHSCOPE_BASE_URL','llm_model':'LLM_MODEL',
 'embedding_key':'SILICONFLOW_API_KEY','embedding_base':'SILICONFLOW_BASE_URL',
 'embedding_model':'SILICONFLOW_EMBEDDING_MODEL','rerank_model':'RERANK_MODEL',
 'mineru_key':'MINERU_API_TOKEN','semantic_key':'SEMANTIC_SCHOLAR_API_KEY',
}
DEFAULTS={'llm_base':'https://dashscope.aliyuncs.com/compatible-mode/v1','llm_model':'',
 'embedding_base':'https://api.siliconflow.cn/v1','embedding_model':'BAAI/bge-m3','rerank_model':'BAAI/bge-reranker-v2-m3'}
HOSTS={'dashscope.aliyuncs.com','dashscope-intl.aliyuncs.com','dashscope-us.aliyuncs.com',
 'api.siliconflow.cn','api.siliconflow.com','api.deepseek.com','api.openai.com','api.moonshot.cn','api.moonshot.ai'}


class ProviderSettings(BaseModel):
    llm_key:str|None=Field(None,max_length=512)
    llm_base:str|None=Field(None,max_length=300)
    llm_model:str|None=Field(None,max_length=100)
    embedding_key:str|None=Field(None,max_length=512)
    embedding_base:str|None=Field(None,max_length=300)
    embedding_model:str|None=Field(None,max_length=100)
    rerank_model:str|None=Field(None,max_length=100)
    mineru_key:str|None=Field(None,max_length=512)
    semantic_key:str|None=Field(None,max_length=512)


def status():
    result={'session_scoped':enabled()}
    for field,name in FIELDS.items():
        current=config.value(name,DEFAULTS.get(field,''))
        result[field+'_configured' if field.endswith('_key') else field]=bool(current.strip()) if field.endswith('_key') else current
    return result


@router.get('')
async def get_settings():return status()


@router.post('')
async def save_settings(payload:ProviderSettings):
    values=payload.model_dump(exclude_none=True)
    for field in ['llm_base','embedding_base']:
        if field not in values:continue
        try:
            parsed=urlsplit(values[field]);port=parsed.port
        except ValueError:
            raise HTTPException(400,'API 地址格式无效') from None
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment or port not in (None,443):
            raise HTTPException(400,'API 地址需使用不含账号、查询参数的 HTTPS 地址')
        if enabled() and parsed.hostname not in HOSTS and not parsed.hostname.endswith('.maas.aliyuncs.com'):
            raise HTTPException(400,'托管实例支持官方服务地址；自定义网关请使用自行部署的实例')
    with connection() as db,db:
        db.execute('BEGIN IMMEDIATE')
        for field,value in values.items():
            key='provider:'+FIELDS[field]
            if value.strip():db.execute('INSERT OR REPLACE INTO app_config(key,value) VALUES(?,?)',(key,value.strip()))
            else:db.execute('DELETE FROM app_config WHERE key=?',(key,))
    return status()
